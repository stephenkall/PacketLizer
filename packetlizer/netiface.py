"""Network interface enumeration and liveness checks.

Two jobs:
  * feed the "Network interfaces" multi-select in the main window, so probes
    can be bound to a specific adapter (e.g. Wi-Fi *and* Ethernet at once);
  * gate the monitor loop on "is there an actual, usable connection right
    now", so a laptop waking from sleep (or a Wi-Fi adapter mid-reconnect)
    doesn't get charged with packet loss for a window it never had a real
    chance to probe through -- see monitor.py.

Requires ``psutil``. If it isn't importable (should never happen -- it ships
in requirements.txt) every function degrades to "don't block, don't offer a
selector", so the app still runs with the pre-existing single-probe behavior.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass

try:
    import psutil
except ImportError:  # pragma: no cover - psutil always ships in requirements.txt
    psutil = None  # type: ignore


@dataclass(frozen=True)
class InterfaceInfo:
    name: str
    ipv4: str | None
    is_up: bool

    @property
    def usable(self) -> bool:
        """Up, with a real (non-loopback, non-APIPA) IPv4 address.

        169.254.0.0/16 is what Windows assigns an adapter when DHCP hasn't
        (yet) handed out a real address -- exactly the state an adapter sits
        in for a moment after resuming from sleep or reconnecting, so it's
        treated the same as "no address".
        """
        return bool(self.is_up and self.ipv4 and not self.ipv4.startswith(("127.", "169.254.")))

    @property
    def label(self) -> str:
        return f"{self.name} ({self.ipv4})" if self.ipv4 else self.name


def list_interfaces() -> list[InterfaceInfo]:
    """Every non-loopback-looking adapter psutil reports, sorted by name."""
    if psutil is None:
        return []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except Exception:  # pragma: no cover - platform edge cases
        return []
    out = []
    for name, st in stats.items():
        ipv4 = next((a.address for a in addrs.get(name, []) if a.family == socket.AF_INET), None)
        if ipv4 and ipv4.startswith("127."):
            continue
        out.append(InterfaceInfo(name=name, ipv4=ipv4, is_up=bool(st.isup)))
    out.sort(key=lambda i: i.name.lower())
    return out


def interface_status(name: str) -> InterfaceInfo | None:
    for iface in list_interfaces():
        if iface.name == name:
            return iface
    return None


def is_ready(name: str) -> bool:
    """True if the named interface is up and has a real IPv4 address."""
    info = interface_status(name)
    return bool(info and info.usable)


def any_interface_ready() -> bool:
    """True if *some* non-loopback interface looks usable.

    Used as the connectivity gate for the default (no interfaces explicitly
    selected) monitoring mode, where probes aren't bound to one adapter.
    """
    if psutil is None:
        return True  # can't check -- don't block probing
    return any(i.usable for i in list_interfaces())
