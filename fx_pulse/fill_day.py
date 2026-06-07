"""Backfill one UTC day's worth of S5 candles into the ticks table.

Run:
    # Default: yesterday's UTC window (cron mode)
    uv run --env-file .env python -m fx_pulse.fill_day

    # Specific day:
    uv run --env-file .env python -m fx_pulse.fill_day --date 2026-06-04

Scheduled in `ops/crontab` to fire once per day with no arg, which
defaults to yesterday's UTC window. The `--date` flag is for manual
catch-up of a specific day (e.g. when the streamer was down for a
window the daily cron already moved past).

Wraps the existing `fx_pulse.backfill.run` so the transactional
batching loop is shared with the manual `python -m fx_pulse.backfill`
entrypoint.

Overlap with periods the live streamer already covered is intended and
safe: `TickStore.write` dedups via `ON CONFLICT (tick_id) DO NOTHING`,
so identical timestamps from the streamer and from S5 candles collide
on insert and the second one is silently dropped. The point of this
job is to fill the gaps when the streamer was down, not to replace it.

`day_range_utc` is split out as a pure function so the date math is
unit-testable without touching OANDA or Postgres.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from fx_pulse import config
from fx_pulse import backfill
from fx_pulse.db import open_connection
from fx_pulse.jobs import track_run
from fx_pulse.obs import get_logger
from fx_pulse.providers import get_historical

# Finest second-resolution OANDA offers. ~17k candles/day, ~4 paginated
# requests. Anything finer would need the tick stream, which is the
# streamer's job.
_GRANULARITY = "S5"

log = get_logger("fx_pulse.fill_day")


def day_range_utc(day: date) -> tuple[str, str]:
    """Return `[day 00:00 UTC, day+1 00:00 UTC)` as RFC3339 strings."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def yesterday_utc(now: datetime | None = None) -> date:
    """Return yesterday's UTC date relative to `now` (defaults to wall clock)."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=1)).date()


def main() -> None:
    args = _parse_args()
    try:
        dsn = config.database_url()
    except RuntimeError as e:
        log.error("startup failed", extra={"error": str(e)})
        sys.exit(1)

    day = args.date or yesterday_utc()
    start, end = day_range_utc(day)
    with open_connection(dsn) as conn:
        with track_run(conn, "fill_day") as run:
            source = get_historical()
            log.info(
                "fill starting",
                extra={
                    "instrument": config.INSTRUMENT,
                    "from": start,
                    "to": end,
                    "granularity": _GRANULARITY,
                },
            )
            written = backfill.run(
                source=source,
                instrument=config.INSTRUMENT,
                start=start,
                end=end,
                granularity=_GRANULARITY,
                dsn=dsn,
            )
            run.rows_changed = written
            log.info("fill done", extra={"written": written})


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill one UTC day of S5 candles.")
    p.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="UTC day to fill, YYYY-MM-DD. Defaults to yesterday UTC.",
    )
    return p.parse_args()


if __name__ == "__main__":
    main()
