"""Periodic-job run tracking — one shared table for every scheduled job.

Every periodic job wraps its body in `with track_run(conn, name) as run:`.
The context manager inserts a `job_runs` row on enter and updates it on
exit with finish time + status. The job mutates `run.rows_changed` during
its work to report a single health metric; richer per-job metrics belong
in stdout JSON logs (see `fx_pulse.obs`), not here.

To add a new periodic job:
    1. Write `python -m fx_pulse.<job_name>` whose body is wrapped in
       `with track_run(conn, "<job_name>") as run: ...`.
    2. Add one line to `ops/crontab`.
    3. Add a Makefile target for manual triggers.
Nothing in this module changes.

Design notes:
    SRP — this module only persists run state. It does not open
    connections, configure logging, or parse env. The caller owns those.
    DIP — `track_run` takes an injected `psycopg.Connection` rather than
    reading config itself, which keeps it trivially testable against a
    per-test schema.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

import psycopg

# Cap on stored exception text. Postgres TEXT has no length limit, but a
# runaway error (e.g. an HTTP response body in a requests exception) would
# bloat the table for no operational gain.
_ERROR_TEXT_LIMIT = 2000


@dataclass
class Run:
    """Mutable handle the job uses to report its row-count metric.

    Anything beyond `rows_changed` belongs in structured logs. Keeping
    this object narrow forces the dashboard contract to stay narrow too.
    """
    rows_changed: int | None = None


@contextmanager
def track_run(conn: psycopg.Connection, job_name: str) -> Iterator[Run]:
    """Record one row in `job_runs` spanning the wrapped block.

    On success: `status='ok'`, `finished_at=now`, `rows_changed=run.rows_changed`.
    On failure: `status='error'`, `error_text=str(exc)[:2000]`, then re-raise.

    Caller owns the connection (it likely already has one open for its
    real work). Autocommit semantics are required so the started_at row
    is durably visible to the dashboard before the job body begins.
    """
    started_at = datetime.now(timezone.utc)
    run_id = conn.execute(
        "INSERT INTO job_runs (job_name, started_at) VALUES (%s, %s) RETURNING id",
        (job_name, started_at),
    ).fetchone()[0]
    run = Run()
    try:
        yield run
    except BaseException as exc:
        conn.execute(
            "UPDATE job_runs "
            "SET finished_at = %s, status = 'error', error_text = %s "
            "WHERE id = %s",
            (datetime.now(timezone.utc), str(exc)[:_ERROR_TEXT_LIMIT], run_id),
        )
        raise
    else:
        conn.execute(
            "UPDATE job_runs "
            "SET finished_at = %s, status = 'ok', rows_changed = %s "
            "WHERE id = %s",
            (datetime.now(timezone.utc), run.rows_changed, run_id),
        )
