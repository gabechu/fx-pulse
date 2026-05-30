"""Stream live AUD/USD prices to terminal.

Run inside Docker: `docker compose up --build`

Vendor selection is via the `FX_PULSE_PROVIDER` env var (default "oanda").
"""
from __future__ import annotations

import sys

from fx_pulse.providers import get_provider


def main() -> None:
    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print("Streaming AUD_USD  (Ctrl-C to stop)", flush=True)
    try:
        for tick in provider.stream(["AUD_USD"]):
            print(f"{tick.time}  bid={tick.bid:.5f}  ask={tick.ask:.5f}")
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
