from packetlizer.config import (
    STATUS_DNS_FAIL,
    STATUS_OK,
    STATUS_TIMEOUT,
    STATUS_UNREACHABLE,
)
from packetlizer.probe import parse_ping_output

WIN_PT_OK = """
Disparando www.vivo.com.br [200.155.4.10] com 32 bytes de dados:
Resposta de 200.155.4.10: bytes=32 tempo=17ms TTL=56

Estatisticas do Ping para 200.155.4.10:
    Pacotes: Enviados = 1, Recebidos = 1, Perdidos = 0 (0% de perda),
"""

WIN_EN_OK = """
Pinging www.vivo.com.br [200.155.4.10] with 32 bytes of data:
Reply from 200.155.4.10: bytes=32 time=23ms TTL=54
"""

WIN_PT_TIMEOUT = """
Disparando 200.155.4.10 com 32 bytes de dados:
Esgotado o tempo limite do pedido.

Estatisticas do Ping para 200.155.4.10:
    Pacotes: Enviados = 1, Recebidos = 0, Perdidos = 1 (100% de perda),
"""

WIN_EN_TIMEOUT = "Request timed out.\n"

LINUX_OK = """
PING www.vivo.com.br (200.155.4.10) 56(84) bytes of data.
64 bytes from 200.155.4.10: icmp_seq=1 ttl=54 time=19.8 ms

--- www.vivo.com.br ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
"""

LINUX_LOSS = """
PING 200.155.4.10 (200.155.4.10) 56(84) bytes of data.

--- 200.155.4.10 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 0ms
"""

UNREACH = "Reply from 192.168.0.1: Destination host unreachable.\n"
DNS_FAIL = "Ping request could not find host www.naoexiste-xyz.com.br. Please check the name.\n"
SUBMS = "Reply from 192.168.0.1: bytes=32 time<1ms TTL=64\n"


def test_windows_pt_ok():
    r = parse_ping_output(WIN_PT_OK, 0)
    assert r.status == STATUS_OK
    assert r.rtt_ms == 17.0


def test_windows_en_ok():
    assert parse_ping_output(WIN_EN_OK, 0).rtt_ms == 23.0


def test_linux_ok_decimal():
    r = parse_ping_output(LINUX_OK, 0)
    assert r.status == STATUS_OK
    assert abs(r.rtt_ms - 19.8) < 1e-6


def test_windows_pt_timeout():
    assert parse_ping_output(WIN_PT_TIMEOUT, 1).status == STATUS_TIMEOUT


def test_windows_en_timeout():
    assert parse_ping_output(WIN_EN_TIMEOUT, 1).status == STATUS_TIMEOUT


def test_linux_loss():
    assert parse_ping_output(LINUX_LOSS, 1).status == STATUS_TIMEOUT


def test_unreachable():
    assert parse_ping_output(UNREACH, 1).status == STATUS_UNREACHABLE


def test_dns_fail():
    assert parse_ping_output(DNS_FAIL, 1).status == STATUS_DNS_FAIL


def test_sub_millisecond():
    r = parse_ping_output(SUBMS, 0)
    assert r.status == STATUS_OK
    assert r.rtt_ms is not None and r.rtt_ms < 1
