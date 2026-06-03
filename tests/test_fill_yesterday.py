"""Tests for the daily-backfill date math.

The end-to-end ingest path (OANDA fetch + Postgres upsert) is exercised
by `tests/test_storage.py` and the existing backfill loop — this file
just pins down the one piece that is specific to `fill_yesterday`:
computing the previous-UTC-day window."""
from __future__ import annotations

from datetime import datetime, timezone

from fx_pulse.fill_yesterday import yesterday_range_utc


def test_range_is_previous_utc_day_at_midnight_boundaries():
    now = datetime(2026, 6, 3, 14, 22, 30, tzinfo=timezone.utc)
    start, end = yesterday_range_utc(now=now)
    assert start == "2026-06-02T00:00:00Z"
    assert end == "2026-06-03T00:00:00Z"


def test_range_when_called_exactly_at_midnight():
    # Edge case: fired at 00:00 UTC. Should still return [yesterday, today),
    # never produce an empty or future-spanning window.
    now = datetime(2026, 6, 3, 0, 0, 0, tzinfo=timezone.utc)
    start, end = yesterday_range_utc(now=now)
    assert start == "2026-06-02T00:00:00Z"
    assert end == "2026-06-03T00:00:00Z"


def test_range_spans_a_month_boundary():
    now = datetime(2026, 6, 1, 1, 30, 0, tzinfo=timezone.utc)
    start, end = yesterday_range_utc(now=now)
    assert start == "2026-05-31T00:00:00Z"
    assert end == "2026-06-01T00:00:00Z"
