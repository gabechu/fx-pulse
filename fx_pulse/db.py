"""Connection lifecycle + forward-only schema migrations.

Single source of truth for PRAGMAs and DDL. Stores call `open_connection`;
they never run schema DDL themselves. To add a table or column, drop a new
`fx_pulse/migrations/NNN_description.sql` file in — no Python changes.

Forward-only: no down-migrations, no DDL DSL. Single-writer SQLite service
doesn't need more than that.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def open_connection(path: str) -> sqlite3.Connection:
    """Apply any pending migrations, then return a WAL-mode autocommit connection."""
    apply_migrations(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def apply_migrations(path: str) -> None:
    """Apply every unapplied numbered .sql file in migrations/ in version order.

    Idempotent: when nothing is pending this costs one SELECT. Each migration
    runs in an explicit transaction so a half-applied file rolls back cleanly
    and the `schema_version` row only commits if the DDL succeeds.
    """
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            version = _version_from_filename(sql_file)
            if version in applied:
                continue
            statements = _split_sql(sql_file.read_text())
            try:
                conn.execute("BEGIN")
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


def _version_from_filename(path: Path) -> int:
    # Files are "NNN_description.sql"; integer prefix is the version.
    return int(path.stem.split("_", 1)[0])


def _split_sql(sql: str) -> List[str]:
    # Naive split on ';' — adequate while migration files stay simple
    # (no semicolons inside strings or comments). If that ever changes,
    # switch to a real parser.
    return [s.strip() for s in sql.split(";") if s.strip()]
