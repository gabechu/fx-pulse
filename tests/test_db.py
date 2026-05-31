"""Tests for the migration runner — runs against a per-test Postgres schema."""
from __future__ import annotations

import psycopg
import pytest

from fx_pulse import db
from fx_pulse.db import apply_migrations, open_connection


def _tables(conn: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
    }


def test_apply_migrations_creates_schema_version_table(pg_dsn):
    apply_migrations(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        assert "schema_version" in _tables(conn)


def test_apply_migrations_records_each_applied_version(pg_dsn):
    apply_migrations(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        versions = sorted(
            row[0] for row in conn.execute("SELECT version FROM schema_version")
        )
    # 001_ticks.sql + 002_signals.sql + 003_tick_source.sql; new files extend this.
    assert versions == [1, 2, 3]


def test_apply_migrations_is_idempotent(pg_dsn):
    apply_migrations(pg_dsn)
    apply_migrations(pg_dsn)
    apply_migrations(pg_dsn)
    with psycopg.connect(pg_dsn) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 3  # one row per applied version, no duplicates


def test_open_connection_applies_schema(pg_dsn):
    conn = open_connection(pg_dsn)
    try:
        tables = _tables(conn)
    finally:
        conn.close()
    assert {"ticks", "signals", "schema_version"} <= tables


def test_migration_failure_rolls_back(pg_dsn, tmp_path, monkeypatch):
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

    with pytest.raises(psycopg.Error):
        apply_migrations(pg_dsn)

    with psycopg.connect(pg_dsn) as conn:
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        tables = _tables(conn)
    assert versions == {1}
    assert "good" in tables
    assert "half" not in tables  # rolled back


def test_new_migration_files_are_picked_up_without_python_changes(
    pg_dsn, tmp_path, monkeypatch
):
    # Proves the "drop a .sql file in" incremental-change story: a brand-new
    # migration file is detected and applied without touching db.py.
    fake_dir = tmp_path / "migs"
    fake_dir.mkdir()
    (fake_dir / "001_a.sql").write_text("CREATE TABLE a (x INTEGER);")
    monkeypatch.setattr(db, "_MIGRATIONS_DIR", fake_dir)

    apply_migrations(pg_dsn)

    # Operator drops a new file in later; second run picks it up.
    (fake_dir / "002_b.sql").write_text("CREATE TABLE b (x INTEGER);")
    apply_migrations(pg_dsn)

    with psycopg.connect(pg_dsn) as conn:
        tables = _tables(conn)
    assert {"a", "b"} <= tables
