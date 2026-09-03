"""Carregamento e persistencia da configuracao do PacketLizer.

A config e os dados ficam em %LOCALAPPDATA%\\PacketLizer (Windows) ou
~/.local/share/PacketLizer (outros SOs), de proposito FORA da pasta do repo
(que pode estar no OneDrive e sob controle de versao).

Pode ser sobrescrito com a variavel de ambiente PACKETLIZER_HOME.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Latencia (ms) usada para representar um pacote perdido nos graficos.
DEFAULT_TIMEOUT_SENTINEL_MS = 9999.0

# Status de cada amostra.
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
    timeout_ms: int = 2000
    timeout_sentinel_ms: float = DEFAULT_TIMEOUT_SENTINEL_MS
    # Numero minimo de perdas consecutivas para contar como uma "queda" (outage).
    outage_min_consecutive: int = 3
    # Dados mais antigos que isto sao apagados no arranque do monitor (0 = nunca).
    retention_days: int = 60
    db_path: str = ""
    # Preferir ICMP raw (precisa admin); se indisponivel cai para o ping do SO.
    prefer_raw_icmp: bool = True

    _source_path: Path = field(default=None, repr=False, compare=False)

    def resolved_db_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path).expanduser()
        return app_home() / "packetlizer.db"

    def to_json(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        return d

    def save(self, path: Path | None = None) -> Path:
        path = Path(path) if path else (self._source_path or (app_home() / "config.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
        self._source_path = path
        return path

    def describe(self) -> str:
        lines = ["Configuracao efetiva do PacketLizer:"]
        lines.append(f"  arquivo de config : {self._source_path}")
        for k, v in self.to_json().items():
            lines.append(f"  {k:<20}: {v}")
        lines.append(f"  banco de dados     : {self.resolved_db_path()}")
        lines.append(f"  pasta de dados     : {app_home()}")
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
        try:
            cfg.save(cfg_path)
        except OSError:
            pass
    return cfg
