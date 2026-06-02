"""Tests for tick persistence — runs against a per-test Postgres schema."""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg

from fx_pulse.providers import Tick
from fx_pulse.storage import TickStore, tick_id_for


def _indexes(conn: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
        )
    }


def _tables(conn: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
    }


def test_open_creates_table_and_index(pg_dsn):
    with TickStore.open(pg_dsn):
        pass
    with psycopg.connect(pg_dsn) as conn:
        tables = _tables(conn)
        indexes = _indexes(conn)
    assert "ticks" in tables
    assert "idx_ticks_instrument_time" in indexes
    assert "idx_ticks_tick_id" in indexes


def test_write_persists_tick(pg_dsn):
    t = Tick(instrument="AUD_USD", time="2026-05-21T00:00:00Z", bid=0.66012, ask=0.66016)
    with TickStore.open(pg_dsn) as store:
        store.write(t, tick_id_for(t), source="live")
    with psycopg.connect(pg_dsn) as conn:
        row = conn.execute(
            "SELECT instrument, time, bid, ask FROM ticks"
        ).fetchone()
    assert row == (
        "AUD_USD",
        datetime(2026, 5, 21, 0, 0, tzinfo=timezone.utc),
        0.66012,
        0.66016,
    )


def test_write_preserves_insertion_order_across_session(pg_dsn):
    ticks = [
        Tick(
            instrument="AUD_USD",
            time=f"2026-05-21T00:00:{i:02d}Z",
            bid=0.66000 + i * 0.0001,
            ask=0.66004 + i * 0.0001,
        )
        for i in range(3)
    ]
    with TickStore.open(pg_dsn) as store:
        for t in ticks:
            store.write(t, tick_id_for(t), source="live")
    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute("SELECT time FROM ticks ORDER BY time").fetchall()
    assert [r[0] for r in rows] == [
        datetime(2026, 5, 21, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 21, 0, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 21, 0, 0, 2, tzinfo=timezone.utc),
    ]


def test_write_dedupes_on_repeat_tick_id(pg_dsn):
    t = Tick(instrument="AUD_USD", time="2026-05-21T00:00:00Z", bid=0.66012, ask=0.66016)
    tid = tick_id_for(t)
    with TickStore.open(pg_dsn) as store:
        store.write(t, tid, source="live")
        store.write(t, tid, source="live")
    with psycopg.connect(pg_dsn) as conn:
        count = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    assert count == 1
