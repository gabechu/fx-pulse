"""Tests for the daily-backfill date math.

The end-to-end ingest path (OANDA fetch + Postgres upsert) is exercised
by `tests/test_storage.py` and the existing backfill loop — this file
just pins down the pieces that are specific to `fill_day`:
deriving yesterday's UTC date, and turning any UTC date into the
[day 00:00, day+1 00:00) window."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fx_pulse.fill_day import day_range_utc, yesterday_utc


def test_day_range_is_full_utc_day():
    start, end = day_range_utc(date(2026, 6, 2))
    assert start == "2026-06-02T00:00:00Z"
    assert end == "2026-06-03T00:00:00Z"


def test_day_range_spans_month_boundary():
    start, end = day_range_utc(date(2026, 5, 31))
    assert start == "2026-05-31T00:00:00Z"
    assert end == "2026-06-01T00:00:00Z"


def test_yesterday_utc_basic():
    now = datetime(2026, 6, 3, 14, 22, 30, tzinfo=timezone.utc)
    assert yesterday_utc(now=now) == date(2026, 6, 2)


def test_yesterday_utc_at_midnight():
    # Edge case: cron fires at exactly 00:00 UTC. Should still return the
    # prior calendar day, never today.
    now = datetime(2026, 6, 3, 0, 0, 0, tzinfo=timezone.utc)
    assert yesterday_utc(now=now) == date(2026, 6, 2)


def test_yesterday_utc_across_month_boundary():
    now = datetime(2026, 6, 1, 1, 30, 0, tzinfo=timezone.utc)
    assert yesterday_utc(now=now) == date(2026, 5, 31)
