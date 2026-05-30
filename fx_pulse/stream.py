"""Stream live AUD/USD prices to terminal and persist to SQLite.

Run: `uv run --env-file .env python -m fx_pulse.stream`

Vendor selection is via the `FX_PULSE_PROVIDER` env var (default "oanda").
DB path is via `FX_PULSE_DB_PATH` (default "fx_pulse.db").
"""
from __future__ import annotations

import os
import sys

from fx_pulse.providers import get_provider
from fx_pulse.storage import TickStore, tick_id_for


def main() -> None:
    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    db_path = os.getenv("FX_PULSE_DB_PATH", "fx_pulse.db")
    print(f"Streaming AUD_USD → {db_path}  (Ctrl-C to stop)", flush=True)
    try:
        with TickStore.open(db_path) as store:
            for tick in provider.stream(["AUD_USD"]):
                tid = tick_id_for(tick)
                store.write(tick, tid)
                print(f"{tid}  {tick.time}  bid={tick.bid:.5f}  ask={tick.ask:.5f}")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
