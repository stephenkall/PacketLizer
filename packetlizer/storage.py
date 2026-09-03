"""Armazenamento compacto das amostras em SQLite.

Esquema (uma linha por sonda):
    samples(ts INTEGER, rtt_ms REAL NULL, status INTEGER)
    meta(key TEXT PRIMARY KEY, value TEXT)

~1 amostra/seg ocupa da ordem de poucos MB por dia; a retencao (config)
apaga o historico antigo e o VACUUM recupera o espaco.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .config import STATUS_OK

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts     INTEGER NOT NULL,
    rtt_ms REAL,
    status INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass(frozen=True)
class Sample:
    ts: int
    rtt_ms: float | None
    status: int

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


class Storage:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- escrita -----------------------------------------------------------
    def add(self, rtt_ms: float | None, status: int, ts: int | None = None) -> None:
        self._conn.execute(
            "INSERT INTO samples(ts, rtt_ms, status) VALUES (?, ?, ?)",
            (int(ts if ts is not None else time.time()), rtt_ms, int(status)),
        )

    def add_many(self, rows: Iterable[tuple[int, float | None, int]]) -> None:
        self._conn.executemany(
            "INSERT INTO samples(ts, rtt_ms, status) VALUES (?, ?, ?)", rows
        )

    def commit(self) -> None:
        self._conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    # -- leitura ---------------------------------------------------------
    def count(self, start: int | None = None, end: int | None = None) -> int:
        sql, params = self._range_sql("SELECT COUNT(*) FROM samples", start, end)
        return int(self._conn.execute(sql, params).fetchone()[0])

    def time_bounds(self) -> tuple[int | None, int | None]:
        row = self._conn.execute("SELECT MIN(ts), MAX(ts) FROM samples").fetchone()
        return (row[0], row[1]) if row else (None, None)

    def iter_samples(
        self, start: int | None = None, end: int | None = None, batch: int = 5000
    ) -> Iterator[Sample]:
        sql, params = self._range_sql(
            "SELECT ts, rtt_ms, status FROM samples", start, end
        )
        sql += " ORDER BY ts ASC"
        cur = self._conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for ts, rtt, status in rows:
                yield Sample(int(ts), rtt, int(status))

    @staticmethod
    def _range_sql(base: str, start: int | None, end: int | None) -> tuple[str, list]:
        clauses, params = [], []
        if start is not None:
            clauses.append("ts >= ?")
            params.append(int(start))
        if end is not None:
            clauses.append("ts <= ?")
            params.append(int(end))
        if clauses:
            base += " WHERE " + " AND ".join(clauses)
        return base, params

    # -- manutencao -----------------------------------------------------
    def purge_older_than(self, cutoff_ts: int) -> int:
        cur = self._conn.execute("DELETE FROM samples WHERE ts < ?", (int(cutoff_ts),))
        self._conn.commit()
        return cur.rowcount

    def vacuum(self) -> None:
        self._conn.execute("VACUUM")
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.commit()
        finally:
            self._conn.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
