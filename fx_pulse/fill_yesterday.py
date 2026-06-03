"""Daily catch-up backfill for yesterday's UTC trading window at S5 granularity.

Run:
    uv run --env-file .env python -m fx_pulse.fill_yesterday

Scheduled in `ops/crontab` to fire once per day. Wraps the existing
`fx_pulse.backfill.run` so the transactional batching loop is shared
with the manual `python -m fx_pulse.backfill` entrypoint.

Overlap with periods the live streamer already covered is intended and
safe: `TickStore.write` dedups via `ON CONFLICT (tick_id) DO NOTHING`,
so identical timestamps from the streamer and from S5 candles collide
on insert and the second one is silently dropped. The point of this
job is to fill the gaps when the streamer was down, not to replace it.

`yesterday_range_utc` is split out as a pure function so the date math
is unit-testable without touching OANDA or Postgres.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

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

log = get_logger("fx_pulse.fill_yesterday")


def yesterday_range_utc(now: datetime | None = None) -> tuple[str, str]:
    """Return `[yesterday 00:00 UTC, today 00:00 UTC)` as RFC3339 strings."""
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return yesterday_start.strftime(fmt), today_start.strftime(fmt)


def main() -> None:
    try:
        dsn = config.database_url()
    except RuntimeError as e:
        log.error("startup failed", extra={"error": str(e)})
        sys.exit(1)

    start, end = yesterday_range_utc()
    with open_connection(dsn) as conn:
        with track_run(conn, "fill_yesterday") as run:
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


if __name__ == "__main__":
    main()
