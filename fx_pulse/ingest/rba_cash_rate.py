"""Download the RBA cash-rate-target series and upsert into `rba_cash_rate`.

Run:
    uv run --env-file .env python -m fx_pulse.ingest.rba_cash_rate

Source is the RBA's published F1 statistical table (Money Market Interest
Rates). Of its 17 columns we keep only what the AUD/USD model wants: the
cash rate target and its change on decision days. Anything else would be
YAGNI until a feature spec asks for it.

Idempotency is handled in Postgres: `ON CONFLICT (date) DO UPDATE … WHERE
… IS DISTINCT FROM EXCLUDED` means re-runs do no writes when the CSV is
unchanged, and update only the cells RBA has restated. The diff (inserts
vs updates) is reported back via `xmax`, logged structured, and the total
flows into `job_runs.rows_changed` so the dashboard surfaces job health.

Module layout follows SRP — `download_csv` does network I/O, `parse_csv`
is a pure transformation (trivially unit-testable), `upsert` does DB I/O.
`main` only orchestrates and wraps the work in `track_run`.
"""
from __future__ import annotations

import csv
import io
import sys
import urllib.request
from datetime import date, datetime

import psycopg

from fx_pulse import config
from fx_pulse.db import open_connection
from fx_pulse.jobs import track_run
from fx_pulse.obs import get_logger

_CSV_URL = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"
_NETWORK_TIMEOUT_S = 30
# RBA emits dates as "04-Jan-2011".
_DATE_FORMAT = "%d-%b-%Y"

log = get_logger("fx_pulse.ingest.rba_cash_rate")

# Row = (date, cash_rate_target | None, change_in_cash_rate_target | None)
Row = tuple[date, float | None, float | None]


def download_csv(url: str = _CSV_URL) -> str:
    """Fetch the source CSV. utf-8-sig strips the BOM RBA prepends."""
    with urllib.request.urlopen(url, timeout=_NETWORK_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8-sig")


def parse_csv(csv_text: str) -> tuple[str | None, list[Row]]:
    """Parse the CSV into (publication_date_string, data_rows).

    Defensive about column layout: asserts the first three Title columns
    are exactly `[Title, Cash Rate Target, Change in the Cash Rate Target]`.
    If RBA ever reorders, the job fails loud with a clear error rather
    than silently writing wrong values.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))

    title_idx = pub_idx = series_id_idx = None
    for i, row in enumerate(rows):
        if not row:
            continue
        head = row[0]
        if head == "Title":
            title_idx = i
        elif head == "Publication date":
            pub_idx = i
        elif head == "Series ID":
            series_id_idx = i
            break

    if title_idx is None or series_id_idx is None:
        raise ValueError("RBA cash-rate CSV: missing 'Title' or 'Series ID' header row")

    title = rows[title_idx]
    expected = ["Title", "Cash Rate Target", "Change in the Cash Rate Target"]
    if title[: len(expected)] != expected:
        raise ValueError(
            "RBA cash-rate CSV: column layout changed; "
            f"expected first three columns {expected}, got {title[:3]}"
        )

    publication_date = rows[pub_idx][1] if pub_idx is not None else None

    data: list[Row] = []
    for row in rows[series_id_idx + 1:]:
        if not row or not row[0].strip():
            continue
        try:
            day = datetime.strptime(row[0], _DATE_FORMAT).date()
        except ValueError:
            continue  # not a data row (blank, footer, etc.)
        rate = float(row[1]) if len(row) > 1 and row[1] else None
        change = float(row[2]) if len(row) > 2 and row[2] else None
        data.append((day, rate, change))

    return publication_date, data


def upsert(conn: psycopg.Connection, rows: list[Row]) -> tuple[int, int]:
    """Upsert rows into `rba_cash_rate`. Returns (added, updated).

    Uses Postgres `xmax = 0` to distinguish newly-inserted rows from
    updated ones. Rows whose values match the existing record do not
    appear in RETURNING at all (the WHERE clause filters them out) and
    so are excluded from the count — exactly the "diff" semantic.
    """
    added = 0
    updated = 0
    sql = (
        "INSERT INTO rba_cash_rate (date, cash_rate_target, change_in_cash_rate_target) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (date) DO UPDATE SET "
        "    cash_rate_target = EXCLUDED.cash_rate_target, "
        "    change_in_cash_rate_target = EXCLUDED.change_in_cash_rate_target "
        "WHERE rba_cash_rate IS DISTINCT FROM EXCLUDED "
        "RETURNING (xmax = 0) AS inserted"
    )
    with conn.transaction():
        for row in rows:
            result = conn.execute(sql, row).fetchone()
            if result is None:
                continue  # no-op: existing row already matched
            if result[0]:
                added += 1
            else:
                updated += 1
    return added, updated


def main() -> None:
    try:
        dsn = config.database_url()
    except RuntimeError as e:
        log.error("startup failed", extra={"error": str(e)})
        sys.exit(1)

    with open_connection(dsn) as conn:
        with track_run(conn, "ingest_rba_cash_rate") as run:
            log.info("ingest starting", extra={"source": _CSV_URL})
            csv_text = download_csv()
            publication_date, rows = parse_csv(csv_text)
            log.info(
                "csv parsed",
                extra={"publication_date": publication_date, "rows": len(rows)},
            )
            added, updated = upsert(conn, rows)
            run.rows_changed = added + updated
            log.info(
                "ingest done",
                extra={
                    "rows_added": added,
                    "rows_updated": updated,
                    "publication_date": publication_date,
                },
            )


if __name__ == "__main__":
    main()
