"""Postgres persistence for ticks.

Schema (tables, indexes, future column changes) lives in `fx_pulse/migrations/`
and is applied by `fx_pulse.db.open_connection`. Stores here are thin DAOs:
they know how to read/write their rows and nothing about DDL.

Connection is opened in autocommit mode, so a bare `.write()` is durable
immediately. Backfill wraps a sequence of writes in `begin()`/`commit()` to
amortise round-trips into one transaction per page.
"""
from __future__ import annotations

import hashlib
from types import TracebackType
from typing import Optional, Type

import psycopg

from fx_pulse.db import open_connection
from fx_pulse.providers import Tick


def tick_id_for(tick: Tick) -> str:
    """Deterministic; centralised so the derivation can evolve without a schema migration."""
    return hashlib.blake2b(
        f"{tick.instrument}|{tick.time}".encode(), digest_size=16
    ).hexdigest()


class TickStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, dsn: str) -> "TickStore":
        return cls(open_connection(dsn))

    def write(self, tick: Tick, tick_id: str, source: str) -> None:
        self._conn.execute(
            "INSERT INTO ticks (instrument, time, bid, ask, tick_id, source) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (tick_id) DO NOTHING",
            (tick.instrument, tick.time, tick.bid, tick.ask, tick_id, source),
        )

    def begin(self) -> None:
        self._conn.execute("BEGIN")

    def commit(self) -> None:
        self._conn.execute("COMMIT")

    def rollback(self) -> None:
        self._conn.execute("ROLLBACK")

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
