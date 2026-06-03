"""Tests for `track_run` — run against a per-test Postgres schema."""
from __future__ import annotations

import psycopg
import pytest

from fx_pulse.db import open_connection
from fx_pulse.jobs import track_run


def _last_run(conn: psycopg.Connection, job_name: str):
    return conn.execute(
        "SELECT status, rows_changed, error_text, started_at, finished_at "
        "FROM job_runs WHERE job_name = %s ORDER BY id DESC LIMIT 1",
        (job_name,),
    ).fetchone()


def test_success_records_ok_and_rows_changed(pg_dsn):
    with open_connection(pg_dsn) as conn:
        with track_run(conn, "demo") as run:
            run.rows_changed = 42
        status, rows_changed, error_text, started, finished = _last_run(conn, "demo")
    assert status == "ok"
    assert rows_changed == 42
    assert error_text is None
    assert started is not None and finished is not None


def test_success_without_setting_rows_changed_stores_null(pg_dsn):
    with open_connection(pg_dsn) as conn:
        with track_run(conn, "demo"):
            pass
        status, rows_changed, _, _, _ = _last_run(conn, "demo")
    assert status == "ok"
    assert rows_changed is None


def test_exception_records_error_and_reraises(pg_dsn):
    with open_connection(pg_dsn) as conn:
        with pytest.raises(ValueError, match="boom"):
            with track_run(conn, "demo"):
                raise ValueError("boom")
        status, rows_changed, error_text, _, finished = _last_run(conn, "demo")
    assert status == "error"
    assert rows_changed is None
    assert error_text == "boom"
    assert finished is not None


def test_long_error_message_is_truncated(pg_dsn):
    with open_connection(pg_dsn) as conn:
        with pytest.raises(RuntimeError):
            with track_run(conn, "demo"):
                raise RuntimeError("x" * 5000)
        _, _, error_text, _, _ = _last_run(conn, "demo")
    assert error_text is not None and len(error_text) == 2000


def test_started_row_visible_before_body_returns(pg_dsn):
    # Dashboards rely on the started_at row being durable before the job
    # finishes, so a long-running job shows as "running" rather than absent.
    with open_connection(pg_dsn) as conn:
        with track_run(conn, "demo"):
            row = conn.execute(
                "SELECT status, finished_at FROM job_runs "
                "WHERE job_name = 'demo' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            assert row == (None, None)  # started, not yet finished
