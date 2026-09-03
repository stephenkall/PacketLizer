"""Loading and persistence of the PacketLizer configuration.

Config and data live in %LOCALAPPDATA%\\PacketLizer (Windows) or
~/.local/share/PacketLizer (other OSes), deliberately OUTSIDE the repo folder
(which may be in OneDrive and under version control).

Can be overridden with the PACKETLIZER_HOME environment variable.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Per-sample status codes.
STATUS_OK = 0
STATUS_TIMEOUT = 1
STATUS_UNREACHABLE = 2
STATUS_DNS_FAIL = 3

STATUS_LABEL = {
    STATUS_OK: "ok",
    STATUS_TIMEOUT: "timeout",
    STATUS_UNREACHABLE: "unreachable",
    STATUS_DNS_FAIL: "dns_fail",
}


def app_home() -> Path:
    env = os.environ.get("PACKETLIZER_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "PacketLizer"
    return Path(os.path.expanduser("~")) / ".local" / "share" / "PacketLizer"


@dataclass
class Config:
    target: str = "www.vivo.com.br"
    interval_seconds: float = 1.0
    # Per-ping timeout (ms). Also the latency value used on the report chart to
    # represent a lost packet (the "timeout" line).
    timeout_ms: int = 2000
    # Minimum number of consecutive losses that counts as one outage.
    outage_min_consecutive: int = 3
    # Data older than this is deleted at monitor startup.
    # 0 (or negative) = UNLIMITED retention, nothing is deleted.
    retention_days: int = 60
    db_path: str = ""
    # Prefer raw ICMP (needs admin); falls back to the OS ping when unavailable.
    prefer_raw_icmp: bool = True
    # UI language code: "auto" (detect from the OS), "en", "pt_BR".
    language: str = "auto"

    _source_path: Path = field(default=None, repr=False, compare=False)
    _created: bool = field(default=False, repr=False, compare=False)

    def resolved_db_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path).expanduser()
        return app_home() / "packetlizer.db"

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if not k.startswith("_")}

    def save(self, path: Path | None = None) -> Path:
        path = Path(path) if path else (self._source_path or (app_home() / "config.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
        self._source_path = path
        return path

    def describe(self) -> str:
        lines = ["PacketLizer effective configuration:"]
        lines.append(f"  config file : {self._source_path}")
        for k, v in self.to_json().items():
            lines.append(f"  {k:<20}: {v}")
        lines.append(f"  database    : {self.resolved_db_path()}")
        lines.append(f"  data folder : {app_home()}")
        return "\n".join(lines)


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path).expanduser() if path else (app_home() / "config.json")
    data: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    known = {f for f in Config.__dataclass_fields__ if not f.startswith("_")}
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    cfg._source_path = cfg_path
    if not cfg_path.exists():
        cfg._created = True
        try:
            cfg.save(cfg_path)
        except OSError:
            pass
    return cfg
