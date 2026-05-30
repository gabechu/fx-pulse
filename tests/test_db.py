"""Offline tests for the migration runner — uses tmp_path, no network."""
from __future__ import annotations

import sqlite3

import pytest

from fx_pulse import db
from fx_pulse.db import apply_migrations, open_connection


def test_apply_migrations_creates_schema_version_table(tmp_path):
    p = str(tmp_path / "x.db")
    apply_migrations(p)
    with sqlite3.connect(p) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "schema_version" in tables


def test_apply_migrations_records_each_applied_version(tmp_path):
    p = str(tmp_path / "x.db")
    apply_migrations(p)
    with sqlite3.connect(p) as conn:
        versions = sorted(
            row[0] for row in conn.execute("SELECT version FROM schema_version")
        )
    # 001_ticks.sql + 002_signals.sql shipped today; new files extend this.
    assert versions == [1, 2]


def test_apply_migrations_is_idempotent(tmp_path):
    p = str(tmp_path / "x.db")
    apply_migrations(p)
    apply_migrations(p)
    apply_migrations(p)
    with sqlite3.connect(p) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 2  # one row per applied version, no duplicates


def test_open_connection_enables_wal_and_applies_schema(tmp_path):
    p = str(tmp_path / "x.db")
    conn = open_connection(p)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    finally:
        conn.close()
    assert mode.lower() == "wal"
    assert {"ticks", "signals", "schema_version"} <= tables


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    # Point the runner at a fake migrations dir whose second file is bad SQL.
    # The runner must record version 1, then leave version 2 unrecorded —
    # and the bad file must not have left a half-built table behind.
    fake_dir = tmp_path / "migs"
    fake_dir.mkdir()
    (fake_dir / "001_good.sql").write_text("CREATE TABLE good (x INTEGER);")
    (fake_dir / "002_bad.sql").write_text(
        "CREATE TABLE half (x INTEGER); CREATE NOPE syntax error;"
    )
    monkeypatch.setattr(db, "_MIGRATIONS_DIR", fake_dir)

    p = str(tmp_path / "x.db")
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(p)

    with sqlite3.connect(p) as conn:
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert versions == {1}
    assert "good" in tables
    assert "half" not in tables  # rolled back


def test_new_migration_files_are_picked_up_without_python_changes(tmp_path, monkeypatch):
    # Proves the "drop a .sql file in" incremental-change story: a brand-new
    # migration file is detected and applied without touching db.py.
    fake_dir = tmp_path / "migs"
    fake_dir.mkdir()
    (fake_dir / "001_a.sql").write_text("CREATE TABLE a (x INTEGER);")
    monkeypatch.setattr(db, "_MIGRATIONS_DIR", fake_dir)

    p = str(tmp_path / "x.db")
    apply_migrations(p)

    # Operator drops a new file in later; second run picks it up.
    (fake_dir / "002_b.sql").write_text("CREATE TABLE b (x INTEGER);")
    apply_migrations(p)

    with sqlite3.connect(p) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {"a", "b"} <= tables
