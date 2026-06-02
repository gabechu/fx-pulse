"""Per-minute BUY classifier driver.

Runs in its own process so an ML failure (broken artifact, feature load
error, slow query) cannot interfere with the live tick streamer. Wakes
shortly after each UTC minute boundary, calls
`Predictor.predict_at(boundary, conn)`, writes one prediction row.

Run: `uv run --env-file .env python -m fx_pulse.predict`

Exits 1 if no model artifact is present — there is nothing to do without
one, and the operator should know rather than have the process loop silently.
"""
from __future__ import annotations

import signal as signalmod
import sys
import threading
from datetime import datetime, timezone

import pandas as pd
import psycopg

from fx_pulse import config
from fx_pulse.db import open_connection
from fx_pulse.ml.infer import Predictor, write_prediction
from fx_pulse.obs import get_logger

log = get_logger("fx_pulse.predict")

# Seconds to wait after a minute boundary before predicting. Gives the
# streamer time to land any ticks at-or-just-after the boundary so the
# feature assembly sees them.
GRACE_SECONDS = 3.0


def main() -> None:
    try:
        dsn = config.database_url()
    except RuntimeError as e:
        log.error("startup failed", extra={"error": str(e)})
        sys.exit(1)

    model_dir = config.model_dir()
    if not (model_dir / "model.joblib").exists():
        log.error("no model artifact", extra={"model_dir": str(model_dir)})
        sys.exit(1)

    try:
        predictor = Predictor.load(model_dir)
    except Exception as e:
        log.error("predictor load failed", extra={"error": str(e)})
        sys.exit(1)

    log.info("predictor loaded", extra={"model_version": predictor.model_version})

    stop = threading.Event()
    signalmod.signal(signalmod.SIGTERM, lambda _signum, _frame: stop.set())

    stride = pd.Timedelta(minutes=config.PREDICTION_STRIDE_MIN)
    try:
        with open_connection(dsn) as conn:
            while not stop.is_set():
                if not _sleep_to_next_boundary(stop, stride):
                    return
                boundary = _just_passed_boundary(stride)
                _predict_once(predictor, conn, boundary)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("stopped")


def _sleep_to_next_boundary(stop: threading.Event, stride: pd.Timedelta) -> bool:
    """Sleep until just past the next stride boundary. Returns False if interrupted."""
    now = pd.Timestamp(datetime.now(timezone.utc))
    next_boundary = (now + stride).floor(stride)
    wakeup_at = next_boundary + pd.Timedelta(seconds=GRACE_SECONDS)
    sleep_secs = max(0.0, (wakeup_at - now).total_seconds())
    return not stop.wait(sleep_secs)


def _just_passed_boundary(stride: pd.Timedelta) -> pd.Timestamp:
    """Floor of now() — the boundary GRACE_SECONDS ago that we should predict for."""
    now = pd.Timestamp(datetime.now(timezone.utc))
    return now.floor(stride)


def _predict_once(
    predictor: Predictor, conn: psycopg.Connection, boundary: pd.Timestamp
) -> None:
    try:
        pred = predictor.predict_at(boundary, conn)
        predicted_at = pd.Timestamp(datetime.now(timezone.utc))
        write_prediction(
            conn,
            pred,
            predicted_at=predicted_at,
            feature_at=boundary,
            instrument=config.INSTRUMENT,
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
        # One bad prediction must not kill the loop — log and try again next stride.
        log.error("prediction failed", extra={"error": str(e), "feature_at": boundary.isoformat()})


if __name__ == "__main__":
    main()
