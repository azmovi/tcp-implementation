import asyncio
from math import ceil
from random import randint
from time import time
from grader.tcputils import (
    FLAGS_SYN,
    FLAGS_ACK,
    FLAGS_FIN,
    read_header,
    calc_checksum,
    fix_checksum,
    make_header,
    MSS,
)


class Servidor:
    def __init__(self, rede, porta):
        self.rede = rede
        self.porta = porta
        self.conexoes = {}
        self.callback = None
        self.rede.registrar_recebedor(self._rdt_rcv)

    def registrar_monitor_de_conexoes_aceitas(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que uma nova conexão for aceita
        """
        self.callback = callback

    def _rdt_rcv(self, src_addr, dst_addr, segment):
        (
            src_port,
            dst_port,
            seq_no,
            ack_no,
            flags,
            window_size,
            checksum,
            urg_ptr,
        ) = read_header(segment)

        if dst_port != self.porta:
            # Ignora segmentos que não são destinados à porta do nosso servidor
            return
        if (
            not self.rede.ignore_checksum
            and calc_checksum(segment, src_addr, dst_addr) != 0
        ):
            print('descartando segmento com checksum incorreto')
            return

        payload = segment[4 * (flags >> 12) :]
        id_conexao = (src_addr, src_port, dst_addr, dst_port)

        if (flags & FLAGS_SYN) == FLAGS_SYN:
            print('Abrindo conexão')
            conexao = self.conexoes[id_conexao] = Conexao(
                self,
                id_conexao,
                randint(0, 0xFFFF),
                seq_no,
            )
            if self.callback:
                self.callback(conexao)

        elif id_conexao in self.conexoes:
            self.conexoes[id_conexao]._rdt_rcv(seq_no, ack_no, flags, payload)
        else:
            print(
                '%s:%d -> %s:%d (pacote associado a conexão desconhecida)'
                % (src_addr, src_port, dst_addr, dst_port)
            )


class Conexao:
    def __init__(self, servidor, id_conexao, seq_no, ack_no):
        self.servidor = servidor
        self.id_conexao = id_conexao
        self.callback = None
        self.seq_no = seq_no
        self.ack_no = ack_no
        self._handshake()
        self.open = True
        self.not_confirmed = {}
        self.timer = None
        self.reenvio = False
        self.send_time = None
        self.timeout_interval = None
        self.cwnd = 1
        self.segments = []

    def _handshake(self):
        self.ack_no += 1
        self.cwnd = 1
        flags = FLAGS_SYN + FLAGS_ACK
        self._tratar(flags)

    def _reenviar(self):
        self.reenvio = True
        self.seq_no = min(self.not_confirmed)
        payload = self.not_confirmed.pop(self.seq_no, None)
        self.seq_no -= len(payload)

        self.cwnd = ceil(self.cwnd / 2)
        if not self.cwnd:
            self.cwnd = 1

        self._tratar(FLAGS_ACK, payload)

    def _tratar(self, flags, payload=None):
        self._enviar_para_servidor(flags, payload)
        if payload:
            self._tratar_payload(payload)

    def _enviar_para_servidor(self, flags, payload=None):
        src_addr, src_port, dst_addr, dst_port = self.id_conexao
        segmento = fix_checksum(
            make_header(dst_port, src_port, self.seq_no, self.ack_no, flags)
            + (payload or b''),
            src_addr,
            dst_addr,
        )
        self.servidor.rede.enviar(segmento, src_addr)

    def _tratar_payload(self, payload):
        self.seq_no += len(payload)
        self.not_confirmed[self.seq_no] = payload

        if self.timer:
            self.timer.cancel()

        self.timer = asyncio.get_event_loop().call_later(
            (self.timeout_interval or 1), self._reenviar
        )

    def _calculate_time(self):
        if self.send_time != None:
            self.sample_rtt = time() - self.send_time

        if self.timeout_interval:
            self.estimate_rtt = (
                0.875 * self.estimate_rtt + 0.125 * self.sample_rtt
            )
            self.dev_rtt = 0.75 * self.dev_rtt + 0.25 * abs(
                self.sample_rtt - self.estimate_rtt
            )
        else:
            self.estimate_rtt = self.sample_rtt
            self.dev_rtt = self.sample_rtt / 2

        return self.estimate_rtt + 4 * self.dev_rtt

    def _remover_confirmados(self, ack_no):
        for seq_no in sorted(self.not_confirmed):
            if seq_no <= ack_no:
                del self.not_confirmed[seq_no]

    def _rdt_rcv(self, seq_no, ack_no, flags, payload):
        if not self.open:
            return

        if not self.reenvio and self.send_time:
            self.timeout_interval = self._calculate_time()


        if seq_no == self.ack_no:
            self.ack_no = seq_no + len(payload)
            if self.seq_no < ack_no:
                self.seq_no = ack_no

            if ack_no in self.not_confirmed:  # Limpar segmentos reconhecidos
                self._remover_confirmados(ack_no)
                if not self.reenvio:
                    self.cwnd += 1
                if self.segments:
                    self.enviar(b''.join(self.segments))

            if payload:
                new_flags = FLAGS_ACK
                self.callback(self, payload)
                self._tratar(new_flags)

            if flags & FLAGS_FIN == FLAGS_FIN:
                self.ack_no = seq_no + 1
                self.fechar()

    # Os métodos abaixo fazem parte da API

    def registrar_recebedor(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que dados forem corretamente recebidos
        """
        self.callback = callback

    def enviar(self, dados):
        """
        Usado pela camada de aplicação para enviar dados
        """
        if not self.open:
            return

        self.reenvio = False
        self.send_time = time()
        flags = FLAGS_ACK
        self.segments = []
        for i in range(0, len(dados), MSS):
            payload = dados[i : i + MSS]
            if payload:
                if len(self.not_confirmed) < self.cwnd:
                    self._tratar(flags, payload)
                else:
                    self.segments.append(payload)

    def fechar(self):
        """
        Usado pela camada de aplicação para fechar a conexão
        """
        flags = FLAGS_FIN | FLAGS_ACK
        self.callback(self, b'')
        self._tratar(flags)
        self.open = False
