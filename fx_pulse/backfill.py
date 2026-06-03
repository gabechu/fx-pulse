"""Backfill historical candles into the ticks table.

Run:
    uv run --env-file .env python -m fx_pulse.backfill \\
        --instrument AUD_USD \\
        --from 2026-04-30T00:00:00Z --to 2026-05-30T00:00:00Z \\
        --granularity M1

Rows are written with `source='candle'` so they're distinguishable from live
ticks. Re-runs are safe — `ON CONFLICT (tick_id) DO NOTHING` dedups.

Vendor selection is via `FX_PULSE_PROVIDER` (default "oanda"). DB target is
via `DATABASE_URL` (libpq URI, e.g. postgresql://user:pass@host:5432/db).
"""
from __future__ import annotations

import argparse
import sys

from fx_pulse import config
from fx_pulse.obs import get_logger
from fx_pulse.providers import HistoricalSource, get_historical
from fx_pulse.storage import TickStore, tick_id_for

_PROGRESS_EVERY = 1000
# One transaction per OANDA page; amortises the commit/fsync over the batch.
_BATCH_SIZE = 5000

log = get_logger("fx_pulse.backfill")


def run(
    *,
    source: HistoricalSource,
    instrument: str,
    start: str,
    end: str,
    granularity: str,
    dsn: str,
) -> int:
    """Backfill candles in [start, end) as ticks. Returns rows written.

    Caller injects `source` and `dsn` so this function is reusable from
    cron-driven entrypoints (see `fx_pulse.fill_yesterday`) without
    duplicating the transactional batching loop.
    """
    written = 0
    with TickStore.open(dsn) as ticks:
        in_batch = 0
        ticks.begin()
        try:
            for tick in source.fetch(instrument, start, end, granularity):
                ticks.write(tick, tick_id_for(tick), source="candle")
                written += 1
                in_batch += 1
                if in_batch >= _BATCH_SIZE:
                    ticks.commit()
                    ticks.begin()
                    in_batch = 0
                if written % _PROGRESS_EVERY == 0:
                    log.info(
                        "backfill progress",
                        extra={"written": written, "last": tick.time},
                    )
            ticks.commit()
        except KeyboardInterrupt:
            ticks.commit()  # keep what we have; ON CONFLICT makes restart cheap
            log.warning("backfill interrupted", extra={"written": written})
        except BaseException:
            ticks.rollback()
            raise
    return written


def main() -> None:
    args = _parse_args()
    try:
        source = get_historical()
        dsn = config.database_url()
    except (RuntimeError, ValueError) as e:
        log.error("startup failed", extra={"error": str(e)})
        sys.exit(1)
    log.info(
        "backfill starting",
        extra={
            "instrument": args.instrument,
            "granularity": args.granularity,
            "from": args.start,
            "to": args.end,
        },
    )
    written = run(
        source=source,
        instrument=args.instrument,
        start=args.start,
        end=args.end,
        granularity=args.granularity,
        dsn=dsn,
    )
    log.info("backfill done", extra={"written": written})


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill historical candles as ticks.")
    p.add_argument("--instrument", default=config.INSTRUMENT)
    p.add_argument("--from", dest="start", required=True, help="RFC3339, e.g. 2026-04-30T00:00:00Z")
    p.add_argument("--to", dest="end", required=True, help="RFC3339, e.g. 2026-05-30T00:00:00Z")
    p.add_argument("--granularity", default="M1", help="OANDA granularity (M1, M5, H1, D, ...)")
    return p.parse_args()


if __name__ == "__main__":
    main()
