"""Tests for the RBA cash-rate ingester.

`parse_csv` is exercised with a tiny in-line fixture covering the layout
the real RBA download uses (BOM, header rows, blank columns). `upsert`
runs against a per-test Postgres schema via the `pg_dsn` fixture and
proves the three behaviours that matter: fresh insert, no-op re-run, and
revision detection.
"""
from __future__ import annotations

from datetime import date

import pytest

from fx_pulse.db import open_connection
from fx_pulse.ingest.rba_cash_rate import parse_csv, upsert


# Three-column-wide miniature of the real source CSV. Includes the UTF-8
# BOM (﻿), the 9 header rows, and three data rows — one without a change
# value (typical), one with a change (decision day), and a trailing blank.
_FIXTURE_CSV = (
    "﻿F1 INTEREST RATES AND YIELDS – MONEY MARKET\n"
    "Title,Cash Rate Target,Change in the Cash Rate Target\n"
    "Description,Cash Rate Target on date,Change in the Cash Rate Target\n"
    "Frequency,Daily,as announced\n"
    "Type,Original,Original\n"
    "Units,Per cent,Per cent\n"
    "\n"
    "Source,RBA,RBA\n"
    "Publication date,03-Jun-2026,03-Jun-2026\n"
    "Series ID,FIRMMCRTD,FIRMMCCRT\n"
    "04-Jan-2011,4.75,\n"
    "05-Jan-2011,4.75,\n"
    "01-Nov-2011,4.50,-0.25\n"
    "\n"
)


# ── parser ────────────────────────────────────────────────────────────────

def test_parse_csv_extracts_publication_date_and_rows():
    pub_date, rows = parse_csv(_FIXTURE_CSV)
    assert pub_date == "03-Jun-2026"
    assert rows == [
        (date(2011, 1, 4), 4.75, None),
        (date(2011, 1, 5), 4.75, None),
        (date(2011, 11, 1), 4.50, -0.25),
    ]


def test_parse_csv_raises_when_column_layout_unexpected():
    bad = _FIXTURE_CSV.replace(
        "Title,Cash Rate Target,Change in the Cash Rate Target",
        "Title,Something Else,Change in the Cash Rate Target",
    )
    with pytest.raises(ValueError, match="column layout changed"):
        parse_csv(bad)


def test_parse_csv_raises_when_series_id_header_missing():
    bad = _FIXTURE_CSV.replace("Series ID,", "Series IDz,")
    with pytest.raises(ValueError, match="missing"):
        parse_csv(bad)


# ── upsert (integration with real Postgres) ──────────────────────────────

def _rows(conn):
    return list(
        conn.execute(
            "SELECT date, cash_rate_target, change_in_cash_rate_target "
            "FROM rba_cash_rate ORDER BY date"
        )
    )


def test_upsert_inserts_fresh_rows(pg_dsn):
    rows = [
        (date(2011, 1, 4), 4.75, None),
        (date(2011, 11, 1), 4.50, -0.25),
    ]
    with open_connection(pg_dsn) as conn:
        added, updated = upsert(conn, rows)
        stored = _rows(conn)
    assert (added, updated) == (2, 0)
    assert stored == [
        (date(2011, 1, 4), 4.75, None),
        (date(2011, 11, 1), 4.50, -0.25),
    ]


def test_upsert_is_a_noop_when_csv_is_unchanged(pg_dsn):
    rows = [(date(2011, 1, 4), 4.75, None)]
    with open_connection(pg_dsn) as conn:
        upsert(conn, rows)
        added, updated = upsert(conn, rows)
    assert (added, updated) == (0, 0)


def test_upsert_detects_a_revised_value(pg_dsn):
    original = [(date(2011, 1, 4), 4.75, None)]
    revised = [(date(2011, 1, 4), 4.50, None)]  # RBA restates the value
    with open_connection(pg_dsn) as conn:
        upsert(conn, original)
        added, updated = upsert(conn, revised)
        stored = _rows(conn)
    assert (added, updated) == (0, 1)
    assert stored == [(date(2011, 1, 4), 4.50, None)]


def test_upsert_distinguishes_added_from_updated_in_mixed_batch(pg_dsn):
    with open_connection(pg_dsn) as conn:
        upsert(conn, [(date(2011, 1, 4), 4.75, None)])
        # One revised, one fresh, one unchanged.
        added, updated = upsert(
            conn,
            [
                (date(2011, 1, 4), 4.50, None),    # revised → update
                (date(2011, 1, 5), 4.75, None),    # new    → insert
            ],
        )
    assert (added, updated) == (1, 1)
