"""Network probing: raw ICMP (icmplib, needs admin) with an OS-``ping`` fallback.

``select_probe()`` chooses automatically:
  * raw ICMP  -> if the process is privileged (admin/root) and a raw socket opens;
  * OS ping   -> otherwise (requires no privileges at all).

If raw ICMP loses permission at runtime the RawIcmpProbe raises PermissionError
and the monitor switches to the PingExeProbe.
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

# select_probe() returns one of these keys; the UI translates them via i18n.
REASON_RAW_PRIVILEGED = "raw_privileged"
REASON_PING_NO_ADMIN = "ping_no_admin"
REASON_PING_RAW_UNAVAILABLE = "ping_raw_unavailable"


@dataclass(frozen=True)
class ProbeResult:
    rtt_ms: float | None
    status: int


def is_privileged() -> bool:
    """True if the process can open raw ICMP sockets."""
    if sys.platform.startswith("win"):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _no_window_popen_kwargs() -> dict:
    """Keep the child ``ping`` process from flashing a console window.

    A windowed (``--noconsole``) PyInstaller build has no console of its own, so
    every ``subprocess`` call would otherwise pop a brief cmd window. CREATE_NO_WINDOW
    plus a hidden STARTUPINFO suppress it. No-op on non-Windows.
    """
    if not sys.platform.startswith("win"):
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return {"startupinfo": startupinfo, "creationflags": creationflags}


# --------------------------------------------------------------------------
# Parser for the OS ``ping`` text -- pure function, covered by tests.
# --------------------------------------------------------------------------
# Require the "=" or "<" separator so we don't match the "time 0ms" of the Linux
# ping summary line ("... 100% packet loss, time 0ms").
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
    """Interpret the output of a ``ping -c/-n 1``. Locale-independent."""
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
    # "100% packet loss", "Request timed out", "Esgotado o tempo limite", etc.
    return ProbeResult(None, STATUS_TIMEOUT)


class PingExeProbe:
    """Calls the operating system's ``ping`` and parses its output."""

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
                **_no_window_popen_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return ProbeResult(None, STATUS_TIMEOUT)
        except FileNotFoundError:
            raise RuntimeError("'ping' command not found on this system")
        return parse_ping_output((proc.stdout or "") + "\n" + (proc.stderr or ""), proc.returncode)


class RawIcmpProbe:
    """Raw ICMP via icmplib. Requires privileges; raises PermissionError if missing."""

    name = "icmp-raw"

    def __init__(self, target: str, timeout_ms: int):
        self.target = target
        self.timeout_s = max(0.2, timeout_ms / 1000)

    def probe(self) -> ProbeResult:
        from icmplib import ping as _ping
        from icmplib.exceptions import NameLookupError, SocketPermissionError

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
    """Return ``(probe, reason_key)`` where reason_key is one of the REASON_* keys."""
    if prefer_raw and is_privileged():
        try:
            p = RawIcmpProbe(target, timeout_ms)
            p.probe()  # smoke test
            return p, REASON_RAW_PRIVILEGED
        except PermissionError:
            pass
        except Exception:
            pass
    reason = REASON_PING_NO_ADMIN if not is_privileged() else REASON_PING_RAW_UNAVAILABLE
    return PingExeProbe(target, timeout_ms), reason


def timed_probe(probe) -> tuple[ProbeResult, float]:
    """Run the probe and return ``(result, wall_duration_ms)``."""
    t0 = time.perf_counter()
    res = probe.probe()
    return res, (time.perf_counter() - t0) * 1000
