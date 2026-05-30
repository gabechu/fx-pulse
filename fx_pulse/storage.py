"""SQLite persistence for ticks.

Sits downstream of the provider layer and sees only the vendor-agnostic `Tick`.
WAL mode lets a reader (e.g. dashboard) query while this process writes.
"""
from __future__ import annotations

import hashlib
import sqlite3
from types import TracebackType
from typing import Optional, Type

from fx_pulse.providers import Tick


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    instrument TEXT NOT NULL,
    time       TEXT NOT NULL,
    bid        REAL NOT NULL,
    ask        REAL NOT NULL,
    tick_id    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ticks_tick_id ON ticks(tick_id);
CREATE INDEX IF NOT EXISTS idx_ticks_instrument_time ON ticks(instrument, time);
"""


def tick_id_for(tick: Tick) -> str:
    """Deterministic; centralised so the derivation can evolve without a schema migration."""
    return hashlib.blake2b(
        f"{tick.instrument}|{tick.time}".encode(), digest_size=16
    ).hexdigest()


class TickStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: str) -> "TickStore":
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        return cls(conn)

    def write(self, tick: Tick, tick_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO ticks (instrument, time, bid, ask, tick_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (tick.instrument, tick.time, tick.bid, tick.ask, tick_id),
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TickStore":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()
