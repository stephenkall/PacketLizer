"""Sondagem de rede: ICMP raw (icmplib, precisa admin) e fallback pelo ping do SO.

`select_probe()` escolhe automaticamente:
  * ICMP raw  -> se o processo tem privilegio (admin/root) e o socket raw abre;
  * ping.exe  -> caso contrario (nao exige privilegio nenhum).

Se o ICMP raw falhar em tempo de execucao por permissao, o RawIcmpProbe
levanta PermissionError e o monitor troca para o PingExeProbe.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

from .config import STATUS_DNS_FAIL, STATUS_OK, STATUS_TIMEOUT, STATUS_UNREACHABLE


@dataclass(frozen=True)
class ProbeResult:
    rtt_ms: float | None
    status: int


def is_privileged() -> bool:
    """True se o processo pode abrir sockets ICMP raw."""
    if sys.platform.startswith("win"):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


# --------------------------------------------------------------------------
# Parser do texto do `ping` do SO -- funcao pura, coberta por testes.
# --------------------------------------------------------------------------
# Exige o separador "=" ou "<" para nao confundir com o "time 0ms" da linha de
# resumo do ping do Linux ("... 100% packet loss, time 0ms").
_TIME_RE = re.compile(
    r"(?:time|tempo|tiempo|zeit|dur[ée]e|durata|temps)\s*[=<]\s*([\d]+(?:[.,]\d+)?)\s*ms",
    re.IGNORECASE,
)
_TIME_LT_RE = re.compile(r"(?:time|tempo|tiempo)\s*<\s*1\s*ms", re.IGNORECASE)
_TTL_RE = re.compile(r"\bttl[=:]\s*\d+", re.IGNORECASE)
_UNREACH_RE = re.compile(
    r"unreachable|inacess[íi]vel|inalcanzable|inaccessible|no route|rede de destino",
    re.IGNORECASE,
)
_DNS_RE = re.compile(
    r"could not find host|nao foi possivel encontrar o host|n[ãa]o encontrado|"
    r"name or service not known|unknown host|ping request could not find",
    re.IGNORECASE,
)


def parse_ping_output(text: str, returncode: int | None = None) -> ProbeResult:
    """Interpreta a saida de um `ping -c/-n 1`. Independente de locale."""
    if _DNS_RE.search(text):
        return ProbeResult(None, STATUS_DNS_FAIL)

    has_ttl = bool(_TTL_RE.search(text))
    if has_ttl and _TIME_LT_RE.search(text):
        return ProbeResult(0.5, STATUS_OK)
    m = _TIME_RE.search(text)
    if m and has_ttl:
        return ProbeResult(float(m.group(1).replace(",", ".")), STATUS_OK)

    if _UNREACH_RE.search(text):
        return ProbeResult(None, STATUS_UNREACHABLE)
    # "100% packet loss", "Esgotado o tempo limite", "Request timed out", etc.
    return ProbeResult(None, STATUS_TIMEOUT)


class PingExeProbe:
    """Chama o `ping` do sistema operacional e faz parse da saida."""

    name = "ping"

    def __init__(self, target: str, timeout_ms: int):
        self.target = target
        self.timeout_ms = max(200, int(timeout_ms))

    def _cmd(self) -> list[str]:
        if sys.platform.startswith("win"):
            return ["ping", "-n", "1", "-w", str(self.timeout_ms), self.target]
        secs = max(1, round(self.timeout_ms / 1000))
        wflag = "-W" if sys.platform == "linux" else "-t"
        return ["ping", "-c", "1", wflag, str(secs), self.target]

    def probe(self) -> ProbeResult:
        try:
            proc = subprocess.run(
                self._cmd(),
                capture_output=True,
                text=True,
                timeout=self.timeout_ms / 1000 + 3,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return ProbeResult(None, STATUS_TIMEOUT)
        except FileNotFoundError:
            raise RuntimeError("comando 'ping' nao encontrado no sistema")
        return parse_ping_output((proc.stdout or "") + "\n" + (proc.stderr or ""), proc.returncode)


class RawIcmpProbe:
    """ICMP raw via icmplib. Requer privilegio; levanta PermissionError se faltar."""

    name = "icmp-raw"

    def __init__(self, target: str, timeout_ms: int):
        self.target = target
        self.timeout_s = max(0.2, timeout_ms / 1000)

    def probe(self) -> ProbeResult:
        from icmplib import ping as _ping
        from icmplib.exceptions import (
            NameLookupError,
            SocketPermissionError,
        )

        try:
            host = _ping(self.target, count=1, timeout=self.timeout_s, privileged=True)
        except SocketPermissionError as e:
            raise PermissionError(str(e)) from e
        except NameLookupError:
            return ProbeResult(None, STATUS_DNS_FAIL)
        except OSError as e:
            return ProbeResult(None, STATUS_UNREACHABLE if e.errno else STATUS_TIMEOUT)

        if host.packets_received > 0 and host.rtts:
            return ProbeResult(float(host.rtts[0]), STATUS_OK)
        return ProbeResult(None, STATUS_TIMEOUT)


def select_probe(target: str, timeout_ms: int, prefer_raw: bool = True):
    """Devolve (probe, motivo_str)."""
    if prefer_raw and is_privileged():
        try:
            p = RawIcmpProbe(target, timeout_ms)
            p.probe()  # teste de fumaca
            return p, "ICMP raw (processo com privilegio)"
        except PermissionError:
            pass
        except Exception:
            pass
    reason = "ping do SO (sem privilegio de admin)" if not is_privileged() else "ping do SO (ICMP raw indisponivel)"
    return PingExeProbe(target, timeout_ms), reason


def timed_probe(probe) -> tuple[ProbeResult, float]:
    """Executa a sonda e devolve (resultado, duracao_wall_ms)."""
    t0 = time.perf_counter()
    res = probe.probe()
    return res, (time.perf_counter() - t0) * 1000
