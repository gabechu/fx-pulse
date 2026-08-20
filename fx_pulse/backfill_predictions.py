"""Backfill model predictions over a historical window.

Run:
    uv run --env-file .env python -m fx_pulse.backfill_predictions \\
        --from 2026-06-13T00:00:00Z --to 2026-08-20T00:00:00Z

Rows are stored with `predicted_at = feature_at`: the dashboard plots
`predicted_at`, so wall-clock stamping would stack the window onto today
and make re-runs duplicate instead of colliding on the primary key.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd
import psycopg

from fx_pulse import config
from fx_pulse.db import open_connection
from fx_pulse.features.assemble import assemble
from fx_pulse.jobs import track_run
from fx_pulse.ml.infer import Predictor, write_prediction
from fx_pulse.obs import get_logger

_CHUNK = pd.Timedelta(days=1)

log = get_logger("fx_pulse.backfill_predictions")


def run(
    *,
    predictor: Predictor,
    conn: psycopg.Connection,
    start: pd.Timestamp,
    end: pd.Timestamp,
    stride: pd.Timedelta,
    instrument: str,
) -> int:
    written = 0
    for chunk_start, chunk_end in _chunks(start, end):
        timestamps = pd.date_range(
            chunk_start, chunk_end, freq=stride, inclusive="left"
        )
        features = assemble(timestamps, conn).dropna(how="all")
        if features.empty:
            log.info("no data in chunk", extra={"from": str(chunk_start)})
            continue
        # One shadow threshold per day-chunk: the trailing 42-day quantile
        # moves too slowly for per-minute recomputation to matter, and a
        # sequential backfill bootstraps it from the chunks it already wrote.
        ml_buy_threshold = predictor.ml_buy_threshold_at(chunk_start, conn)
        predictions = predictor.predict_frame(features, ml_buy_threshold)
        with conn.transaction():
            for t, pred in zip(features.index, predictions):
                write_prediction(
                    conn,
                    pred,
                    predicted_at=t,
                    feature_at=t,
                    instrument=instrument,
                    model_version=predictor.model_version,
                )
        written += len(predictions)
        log.info(
            "chunk done",
            extra={
                "from": str(chunk_start),
                "rows": len(predictions),
                "buys": sum(p.decision == "BUY" for p in predictions),
                "ml_shadow_signals": sum(p.ml_buy_signal for p in predictions),
                "written_total": written,
            },
        )
    return written


def _chunks(start: pd.Timestamp, end: pd.Timestamp):
    cursor = start
    while cursor < end:
        yield cursor, min(cursor + _CHUNK, end)
        cursor += _CHUNK


def main() -> None:
    args = _parse_args()
    try:
        dsn = config.database_url()
    except RuntimeError as e:
        log.error("startup failed", extra={"error": str(e)})
        sys.exit(1)

    model_dir = config.model_dir()
    if not (model_dir / "model.joblib").exists():
        log.error("no model artifact", extra={"model_dir": str(model_dir)})
        sys.exit(1)
    predictor = Predictor.load(model_dir)

    stride = pd.Timedelta(minutes=config.PREDICTION_STRIDE_MIN)
    with open_connection(dsn) as conn:
        with track_run(conn, "backfill_predictions") as job:
            log.info(
                "backfill starting",
                extra={
                    "instrument": config.INSTRUMENT,
                    "from": str(args.start),
                    "to": str(args.end),
                    "stride_min": config.PREDICTION_STRIDE_MIN,
                    "model_version": predictor.model_version,
                },
            )
            written = run(
                predictor=predictor,
                conn=conn,
                start=args.start,
                end=args.end,
                stride=stride,
                instrument=config.INSTRUMENT,
            )
            job.rows_changed = written
            log.info("backfill done", extra={"written": written})


def _utc_timestamp(value: str) -> pd.Timestamp:
    t = pd.Timestamp(value)
    return t.tz_localize(timezone.utc) if t.tz is None else t.tz_convert(timezone.utc)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill predictions over a window.")
    p.add_argument(
        "--from",
        dest="start",
        type=_utc_timestamp,
        required=True,
        help="RFC3339, e.g. 2026-06-13T00:00:00Z",
    )
    p.add_argument(
        "--to",
        dest="end",
        type=_utc_timestamp,
        default=pd.Timestamp(datetime.now(timezone.utc)).floor("min"),
        help="RFC3339, exclusive. Defaults to the current minute.",
    )
    return p.parse_args()


if __name__ == "__main__":
    main()