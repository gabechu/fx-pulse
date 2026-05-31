"""Stream live AUD/USD prices to terminal and persist to Postgres.

Run: `uv run --env-file .env python -m fx_pulse.stream`

Vendor selection is via the `FX_PULSE_PROVIDER` env var (default "oanda").
DB target is via `DATABASE_URL` (libpq URI).
"""
from __future__ import annotations

import os
import sys

from fx_pulse.providers import get_provider
from fx_pulse.signal import MACrossover
from fx_pulse.storage import SignalStore, TickStore, tick_id_for


def main() -> None:
    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)
    print("Streaming AUD_USD  (Ctrl-C to stop)", flush=True)
    crossover = MACrossover()
    try:
        with TickStore.open(dsn) as ticks, SignalStore.open(dsn) as signals:
            for tick in provider.stream(["AUD_USD"]):
                tid = tick_id_for(tick)
                ticks.write(tick, tid, source="live")
                signal = crossover.update(tick)
                if signal is None:
                    print(f"{tid}  {tick.time}  bid={tick.bid:.5f}  ask={tick.ask:.5f}  signal=warmup")
                else:
                    signals.write(signal)
                    print(
                        f"{tid}  {tick.time}  bid={tick.bid:.5f}  ask={tick.ask:.5f}  "
                        f"signal={signal.label}  short={signal.short_ma:.5f}  long={signal.long_ma:.5f}"
                    )
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
