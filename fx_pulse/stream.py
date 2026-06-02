"""Stream live AUD/USD prices to terminal and persist to Postgres.

Also runs the BUY classifier once per 15-minute boundary if a model
artifact is present at `FX_PULSE_MODEL_DIR` (default `data/models`).
Predictions go to the `predictions` table; absent model = no-op (streamer
keeps writing ticks regardless).

Run: `uv run --env-file .env python -m fx_pulse.stream`

Vendor selection is via the `FX_PULSE_PROVIDER` env var (default "oanda").
DB target is via `DATABASE_URL` (libpq URI).
"""
from __future__ import annotations

import os
import signal as signalmod
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from fx_pulse.db import open_connection
from fx_pulse.ml.infer import Predictor, write_prediction
from fx_pulse.obs import Metrics, get_logger, touch_liveness
from fx_pulse.providers import get_provider
from fx_pulse.signal import MACrossover
from fx_pulse.storage import SignalStore, TickStore, tick_id_for

log = get_logger("fx_pulse.stream")

PREDICTION_STRIDE_MIN = 1
DEFAULT_MODEL_DIR = Path(os.environ.get("FX_PULSE_MODEL_DIR", "data/models"))


def _stride_boundary(tick_time: str) -> Optional[pd.Timestamp]:
    """Return the most-recent PREDICTION_STRIDE_MIN UTC boundary at-or-before tick_time."""
    ts = pd.Timestamp(tick_time)
    if ts.tz is None:
        ts = ts.tz_localize("UTC")
    return ts.floor(f"{PREDICTION_STRIDE_MIN}min")


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

    predictor: Optional[Predictor] = None
    try:
        if (DEFAULT_MODEL_DIR / "model.joblib").exists():
            predictor = Predictor.load(DEFAULT_MODEL_DIR)
            log.info("predictor loaded", extra={"model_version": predictor.model_version})
        else:
            log.info("no model artifact found; running without predictions",
                     extra={"model_dir": str(DEFAULT_MODEL_DIR)})
    except Exception as e:
        # A broken model artifact must not block tick ingest.
        log.error("predictor load failed; continuing without predictions",
                  extra={"error": str(e)})

    log.info("streaming starting", extra={"instrument": "AUD_USD"})
    metrics = Metrics(logger=log)
    crossover = MACrossover()
    last_prediction_boundary: Optional[pd.Timestamp] = None

    stop = threading.Event()
    signalmod.signal(signalmod.SIGTERM, lambda _signum, _frame: stop.set())
    pred_conn = open_connection(dsn) if predictor else None

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

                if predictor is not None and pred_conn is not None:
                    boundary = _stride_boundary(tick.time)
                    if boundary is not None and boundary != last_prediction_boundary:
                        # Only predict once per stride. The first tick after a
                        # boundary triggers; subsequent ticks in the same
                        # stride are skipped (last_prediction_boundary guard).
                        try:
                            pred = predictor.predict_at(boundary)
                            write_prediction(
                                pred_conn,
                                pred,
                                predicted_at=pd.Timestamp(datetime.now(timezone.utc)),
                                feature_at=boundary,
                                instrument=tick.instrument,
                                model_version=predictor.model_version,
                            )
                            log.info(
                                "prediction",
                                extra={
                                    "feature_at": boundary.isoformat(),
                                    "decision": pred.decision,
                                    "buy_proba": pred.buy_proba,
                                    "sell_proba": pred.sell_proba,
                                    "model_version": predictor.model_version,
                                },
                            )
                        except Exception as e:
                            log.error("prediction failed", extra={"error": str(e)})
                        finally:
                            last_prediction_boundary = boundary

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
        if pred_conn is not None:
            pred_conn.close()
        metrics.flush()
        log.info("stopped")


if __name__ == "__main__":
    main()
