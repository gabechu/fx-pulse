"""Offline tests for tick + signal persistence — uses tmp_path, no network."""
from __future__ import annotations

import sqlite3

from fx_pulse.providers import Tick
from fx_pulse.signal import Signal
from fx_pulse.storage import SignalStore, TickStore, tick_id_for


def test_open_creates_table_and_index(tmp_path):
    db = tmp_path / "ticks.db"
    with TickStore.open(str(db)):
        pass
    with sqlite3.connect(db) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        indexes = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
    assert "ticks" in tables
    assert "signals" in tables
    assert "idx_ticks_instrument_time" in indexes
    assert "idx_ticks_tick_id" in indexes
    assert "idx_signals_instrument_time" in indexes


def test_open_enables_wal_mode(tmp_path):
    db = tmp_path / "ticks.db"
    with TickStore.open(str(db)):
        pass
    with sqlite3.connect(db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_write_persists_tick(tmp_path):
    db = tmp_path / "ticks.db"
    t = Tick(instrument="AUD_USD", time="2026-05-21T00:00:00Z", bid=0.66012, ask=0.66016)
    with TickStore.open(str(db)) as store:
        store.write(t, tick_id_for(t), source="live")
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT instrument, time, bid, ask FROM ticks"
        ).fetchone()
    assert row == ("AUD_USD", "2026-05-21T00:00:00Z", 0.66012, 0.66016)


def test_write_preserves_insertion_order_across_session(tmp_path):
    db = tmp_path / "ticks.db"
    ticks = [
        Tick(
            instrument="AUD_USD",
            time=f"2026-05-21T00:00:{i:02d}Z",
            bid=0.66000 + i * 0.0001,
            ask=0.66004 + i * 0.0001,
        )
        for i in range(3)
    ]
    with TickStore.open(str(db)) as store:
        for t in ticks:
            store.write(t, tick_id_for(t), source="live")
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT time FROM ticks ORDER BY time"
        ).fetchall()
    assert [r[0] for r in rows] == [
        "2026-05-21T00:00:00Z",
        "2026-05-21T00:00:01Z",
        "2026-05-21T00:00:02Z",
    ]


def test_write_dedupes_on_repeat_tick_id(tmp_path):
    db = tmp_path / "ticks.db"
    t = Tick(instrument="AUD_USD", time="2026-05-21T00:00:00Z", bid=0.66012, ask=0.66016)
    tid = tick_id_for(t)
    with TickStore.open(str(db)) as store:
        store.write(t, tid, source="live")
        store.write(t, tid, source="live")
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    assert count == 1


def test_signal_store_persists_signal(tmp_path):
    db = tmp_path / "ticks.db"
    s = Signal(
        instrument="AUD_USD",
        time="2026-05-21T00:00:00Z",
        short_ma=0.66012,
        long_ma=0.66010,
        label="long",
    )
    with SignalStore.open(str(db)) as store:
        store.write(s)
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT instrument, time, short_ma, long_ma, label FROM signals"
        ).fetchone()
    assert row == ("AUD_USD", "2026-05-21T00:00:00Z", 0.66012, 0.66010, "long")


def test_signal_store_dedupes_on_instrument_time(tmp_path):
    db = tmp_path / "ticks.db"
    s = Signal(
        instrument="AUD_USD",
        time="2026-05-21T00:00:00Z",
        short_ma=0.66012,
        long_ma=0.66010,
        label="long",
    )
    with SignalStore.open(str(db)) as store:
        store.write(s)
        store.write(s)
    with sqlite3.connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert count == 1


def test_tick_store_and_signal_store_share_db(tmp_path):
    # Both stores point at the same file; opening one then the other must
    # leave both tables intact (i.e. SignalStore.open doesn't clobber ticks).
    db = tmp_path / "ticks.db"
    t = Tick(instrument="AUD_USD", time="2026-05-21T00:00:00Z", bid=0.66012, ask=0.66016)
    s = Signal(
        instrument="AUD_USD",
        time="2026-05-21T00:00:00Z",
        short_ma=0.66014,
        long_ma=0.66013,
        label="long",
    )
    with TickStore.open(str(db)) as ticks, SignalStore.open(str(db)) as signals:
        ticks.write(t, tick_id_for(t), source="live")
        signals.write(s)
    with sqlite3.connect(db) as conn:
        tick_count = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
        signal_count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    assert tick_count == 1
    assert signal_count == 1
