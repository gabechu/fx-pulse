"""Backfill historical candles into the ticks table.

Run:
    uv run --env-file .env python -m fx_pulse.backfill \\
        --instrument AUD_USD \\
        --from 2026-04-30T00:00:00Z --to 2026-05-30T00:00:00Z \\
        --granularity M1

Rows are written with `source='candle'` so they're distinguishable from live
ticks. Re-runs are safe — `INSERT OR IGNORE` on `tick_id` dedups.

Vendor selection is via `FX_PULSE_PROVIDER` (default "oanda"). DB path is via
`FX_PULSE_DB_PATH` (default "fx_pulse.db").
"""
from __future__ import annotations

import argparse
import os
import sys

from fx_pulse.providers import get_historical
from fx_pulse.storage import TickStore, tick_id_for

_PROGRESS_EVERY = 1000


def main() -> None:
    args = _parse_args()
    try:
        source = get_historical()
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    db_path = os.getenv("FX_PULSE_DB_PATH", "fx_pulse.db")
    print(
        f"Backfilling {args.instrument} {args.granularity} "
        f"[{args.start} → {args.end}] into {db_path}",
        flush=True,
    )
    written = 0
    with TickStore.open(db_path) as ticks:
        try:
            for tick in source.fetch(
                args.instrument, args.start, args.end, args.granularity
            ):
                ticks.write(tick, tick_id_for(tick), source="candle")
                written += 1
                if written % _PROGRESS_EVERY == 0:
                    print(f"  …{written} candles (last={tick.time})", flush=True)
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
    print(f"Done. Wrote {written} rows.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill historical candles as ticks.")
    p.add_argument("--instrument", default="AUD_USD")
    p.add_argument("--from", dest="start", required=True, help="RFC3339, e.g. 2026-04-30T00:00:00Z")
    p.add_argument("--to", dest="end", required=True, help="RFC3339, e.g. 2026-05-30T00:00:00Z")
    p.add_argument("--granularity", default="M1", help="OANDA granularity (M1, M5, H1, D, ...)")
    return p.parse_args()


if __name__ == "__main__":
    main()
