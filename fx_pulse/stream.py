"""Stream live AUD/USD prices to terminal and persist to Postgres.

Run: `uv run --env-file .env python -m fx_pulse.stream`

Vendor selection is via the `FX_PULSE_PROVIDER` env var (default "oanda").
DB target is via `DATABASE_URL` (libpq URI).
"""
from __future__ import annotations

import os
import signal as signalmod
import sys
import threading

from fx_pulse.obs import Metrics, get_logger, touch_liveness
from fx_pulse.providers import get_provider
from fx_pulse.signal import MACrossover
from fx_pulse.storage import SignalStore, TickStore, tick_id_for

log = get_logger("fx_pulse.stream")


def main() -> None:
    try:
        provider = get_provider()
    except (RuntimeError, ValueError) as e:
        log.error("provider init failed", extra={"error": str(e)})
        sys.exit(1)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL is not set")
        sys.exit(1)
    log.info("streaming starting", extra={"instrument": "AUD_USD"})
    metrics = Metrics(logger=log)
    crossover = MACrossover()
    # SIGTERM-driven graceful shutdown: ECS sends SIGTERM on stop, and
    # Python does not translate it to KeyboardInterrupt. The handler sets
    # the event; the provider checks it at loop top and uses it as an
    # interruptible sleep so we don't sit out a 30s backoff before exiting.
    stop = threading.Event()
    signalmod.signal(signalmod.SIGTERM, lambda _signum, _frame: stop.set())
    try:
        with TickStore.open(dsn) as ticks, SignalStore.open(dsn) as signals:
            for tick in provider.stream(
                ["AUD_USD"],
                on_reconnect=metrics.record_reconnect,
                stop=stop,
            ):
                tid = tick_id_for(tick)
                ticks.write(tick, tid, source="live")
                touch_liveness()
                signal = crossover.update(tick)
                metrics.record_tick(tick.time)
                if signal is None:
                    log.info(
                        "tick",
                        extra={
                            "instrument": tick.instrument,
                            "time": tick.time,
                            "bid": tick.bid,
                            "ask": tick.ask,
                            "signal": "warmup",
                        },
                    )
                else:
                    signals.write(signal)
                    log.info(
                        "tick",
                        extra={
                            "instrument": tick.instrument,
                            "time": tick.time,
                            "bid": tick.bid,
                            "ask": tick.ask,
                            "signal": signal.label,
                            "short_ma": signal.short_ma,
                            "long_ma": signal.long_ma,
                        },
                    )
                metrics.maybe_flush()
    except KeyboardInterrupt:
        pass
    finally:
        metrics.flush()
        log.info("stopped")


if __name__ == "__main__":
    main()
