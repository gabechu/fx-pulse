"""Connection lifecycle + forward-only schema migrations.

Single source of truth for connection setup and DDL. Stores call
`open_connection`; they never run schema DDL themselves. To add a table or
column, drop a new `fx_pulse/migrations/NNN_description.sql` file in — no
Python changes.

Forward-only: no down-migrations, no DDL DSL.
"""
from __future__ import annotations

from pathlib import Path

import psycopg

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def open_connection(dsn: str) -> psycopg.Connection:
    """Apply any pending migrations, then return an autocommit connection.

    Callers that need batched writes wrap a sequence of inserts in an
    explicit `BEGIN`/`COMMIT` (see TickStore).
    """
    apply_migrations(dsn)
    return psycopg.connect(dsn, autocommit=True)


def apply_migrations(dsn: str) -> None:
    """Apply every unapplied numbered .sql file in migrations/ in version order.

    Idempotent: when nothing is pending this costs one SELECT. Each migration
    runs in its own transaction so a half-applied file rolls back cleanly and
    the `schema_version` row only commits if the DDL succeeds.
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {
            row[0] for row in conn.execute("SELECT version FROM schema_version")
        }
        for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            version = _version_from_filename(sql_file)
            if version in applied:
                continue
            with conn.transaction():
                conn.execute(sql_file.read_text())
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (%s)",
                    (version,),
                )


def _version_from_filename(path: Path) -> int:
    # Files are "NNN_description.sql"; integer prefix is the version.
    return int(path.stem.split("_", 1)[0])
