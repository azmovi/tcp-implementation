import asyncio
from grader.tcputils import (
    FLAGS_FIN,
    FLAGS_SYN,
    FLAGS_RST,
    FLAGS_ACK,
    calc_checksum,
    read_header,
    fix_checksum,
    MSS,
    make_header,
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

    def retornar_servidor(self):
        return self

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
            ack_no = seq_no + 1

            servidor = self.retornar_servidor()
            conexao = self.conexoes[id_conexao] = Conexao(
                servidor, id_conexao, seq_no, ack_no
            )

            flags += FLAGS_ACK
            response_segment = fix_checksum(
                make_header(dst_port, src_port, seq_no, ack_no, flags),
                src_addr,
                dst_addr,
            )

            self.rede.enviar(response_segment, src_addr)
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
    # id_conexao = src_addr, src_port, dst_addr, dst_port

    def __init__(self, servidor, id_conexao, seq_no, ack_no):
        self.servidor = servidor
        self.id_conexao = id_conexao
        self.callback = None
        self.timer = asyncio.get_event_loop().call_later(
            1, self._exemplo_timer
        )  # um timer pode ser criado assim; esta linha é só um exemplo e pode ser removida
        self.seq_no = seq_no
        self.ack_no = ack_no
        self.seq_client = ack_no
        self.segmentos = {}
        self.payloads = {}
        self.mss = MSS
        # self.timer.cancel()   # é possível cancelar o timer chamando esse método; esta linha é só um exemplo e pode ser removida

    def _exemplo_timer(self):
        # Esta função é só um exemplo e pode ser removida
        print('Este é um exemplo de como fazer um timer')

    def _rdt_rcv(self, seq_no, ack_no, flags, payload):
        if seq_no != self.ack_no:
            return

        self.seq_no = self.ack_no
        self.ack_no += len(payload)

        self.callback(self, payload)

        src_addr, src_port, dst_addr, dst_port = self.id_conexao
        flags = FLAGS_ACK
        newSegment = fix_checksum(
            make_header(dst_port, src_port, self.seq_no, self.ack_no, flags),
            src_addr,
            dst_addr,
        )
        self.servidor.rede.enviar(newSegment, src_addr)

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
        i = 0
        src_addr, src_port, dst_addr, dst_port = self.id_conexao
        for qtd in range(0, len(dados), MSS):
            payload = dados[i : i + MSS]
            flags = FLAGS_ACK
            newSegment = fix_checksum(
                make_header(
                    dst_port, src_port, self.seq_client, self.ack_no, flags
                )
                + payload,
                src_addr,
                dst_addr,
            )

            self.seq_client += len(payload)
            self.servidor.rede.enviar(newSegment, src_addr)
            i += MSS

    def fechar(self):
        """
        Usado pela camada de aplicação para fechar a conexão
        """
        # TODO: implemente aqui o fechamento de conexão
        pass
