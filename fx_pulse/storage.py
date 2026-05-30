"""SQLite persistence for ticks and signals.

Schema (tables, indexes, future column changes) lives in `fx_pulse/migrations/`
and is applied by `fx_pulse.db.open_connection`. Stores here are thin DAOs:
they know how to read/write their rows and nothing about DDL.
"""
from __future__ import annotations

import hashlib
import sqlite3
from types import TracebackType
from typing import Optional, Type

from fx_pulse.db import open_connection
from fx_pulse.providers import Tick
from fx_pulse.signal import Signal


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
        return cls(open_connection(path))

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


class SignalStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, path: str) -> "SignalStore":
        return cls(open_connection(path))

    def write(self, signal: Signal) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO signals "
            "(instrument, time, short_ma, long_ma, label) VALUES (?, ?, ?, ?, ?)",
            (
                signal.instrument,
                signal.time,
                signal.short_ma,
                signal.long_ma,
                signal.label,
            ),
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SignalStore":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()
