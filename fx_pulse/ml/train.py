"""Train two binary classifiers (buy / sell), pick precision-targeted thresholds.

Pipeline:
1. Build a grid of timestamps to evaluate (every TRAIN_STRIDE_MIN minutes
   over the price-data span).
2. Call `features.assemble(timestamps)` once → wide feature matrix.
3. Call `ml.label.compute_labels(timestamps)` → forward 30-day-return labels.
4. Chronological 70/15/15 split with a HORIZON_BARS gap between splits to
   prevent label-leakage across the boundary.
5. Per-direction HistGradientBoostingClassifier → isotonic calibration on
   the val slice → smallest val threshold meeting target precision.
6. Persist {model.joblib, model_meta.json} so `infer.Predictor` can load.

Train and inference both call the same `assemble()`. Parity by reuse.

To iterate on the model: edit `Hyperparameters` (HGB params) or the
`DEFAULT_TARGET_PRECISION` / `DEFAULT_TRAIN_STRIDE_MIN` / `MODEL_VERSION`
constants, then `make train-model`.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import precision_score, recall_score

from fx_pulse import config
from fx_pulse.db import open_connection
from fx_pulse.features import FEATURES
from fx_pulse.features.assemble import assemble
from fx_pulse.ml.label import HORIZON_BARS, compute_labels
from fx_pulse.obs import get_logger

log = get_logger(__name__)

DEFAULT_TARGET_PRECISION = 0.90
DEFAULT_TRAIN_STRIDE_MIN = 15  # evaluate one row every 15 min over history
MODEL_VERSION = "v3"


@dataclass(frozen=True)
class Hyperparameters:
    """HistGradientBoostingClassifier knobs. Bump MODEL_VERSION when you change these."""
    max_iter: int = 400
    learning_rate: float = 0.05
    max_leaf_nodes: int = 63
    min_samples_leaf: int = 200
    l2_regularization: float = 0.1
    random_state: int = 0


DEFAULT_HYPERPARAMS = Hyperparameters()


@dataclass
class DirectionResult:
    direction: str
    threshold: float
    val_precision: float
    val_recall: float
    val_signal_rate: float
    test_precision: float
    test_recall: float
    test_signal_rate: float
    base_rate: float
    n_pos: int
    n_total: int


def main(
    target_precision: float = DEFAULT_TARGET_PRECISION,
    train_stride_min: int = DEFAULT_TRAIN_STRIDE_MIN,
    hyperparams: Hyperparameters = DEFAULT_HYPERPARAMS,
    model_dir: Path | None = None,
) -> None:
    if model_dir is None:
        model_dir = config.model_dir()

    with open_connection(config.database_url()) as conn:
        timestamps = _build_grid(conn, train_stride_min)
        log.info(
            "built timestamp grid",
            extra={
                "rows": len(timestamps),
                "first": str(timestamps[0]),
                "last": str(timestamps[-1]),
                "stride_min": train_stride_min,
            },
        )

        log.info("assembling features")
        features = assemble(timestamps, conn).dropna(how="all")
        log.info("assembled features", extra={"rows": len(features), "cols": features.shape[1]})

        log.info("computing labels")
        labels = compute_labels(timestamps, conn)
        log.info(
            "computed labels",
            extra={
                "rows": len(labels),
                "buy_rate": float(labels["buy"].mean()),
                "sell_rate": float(labels["sell"].mean()),
            },
        )

    joined = features.join(labels, how="inner").dropna(subset=["buy", "sell"])
    log.info("joined feature+label rows", extra={"rows": len(joined)})

    bars_per_stride = train_stride_min  # 1 row per `stride` minutes
    gap_rows = HORIZON_BARS // bars_per_stride
    train, val, test = _time_split(joined, gap_rows=gap_rows)
    log.info(
        "split sizes",
        extra={"train": len(train), "val": len(val), "test": len(test)},
    )

    feat_cols = list(FEATURES.keys())
    results: dict[str, DirectionResult] = {}
    models: dict[str, CalibratedClassifierCV] = {}

    for direction in ("buy", "sell"):
        log.info("training direction", extra={"direction": direction})
        x_train = train[feat_cols].to_numpy()
        y_train = train[direction].to_numpy().astype(int)
        x_val = val[feat_cols].to_numpy()
        y_val = val[direction].to_numpy().astype(int)
        x_test = test[feat_cols].to_numpy()
        y_test = test[direction].to_numpy().astype(int)

        base = HistGradientBoostingClassifier(**dataclasses.asdict(hyperparams))
        base.fit(x_train, y_train)

        calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
        calibrated.fit(x_val, y_val)

        val_proba = calibrated.predict_proba(x_val)[:, 1]
        threshold = _pick_threshold(y_val, val_proba, target_precision)

        val_pred = val_proba >= threshold
        test_proba = calibrated.predict_proba(x_test)[:, 1]
        test_pred = test_proba >= threshold

        results[direction] = DirectionResult(
            direction=direction,
            threshold=float(threshold),
            val_precision=_safe_precision(y_val, val_pred),
            val_recall=_safe_recall(y_val, val_pred),
            val_signal_rate=float(val_pred.mean()),
            test_precision=_safe_precision(y_test, test_pred),
            test_recall=_safe_recall(y_test, test_pred),
            test_signal_rate=float(test_pred.mean()),
            base_rate=float(y_train.mean()),
            n_pos=int(y_train.sum()),
            n_total=int(len(y_train)),
        )
        models[direction] = calibrated
        log.info("direction trained", extra={"direction": direction, **vars(results[direction])})

    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = model_dir / "model.joblib"
    meta_path = model_dir / "model_meta.json"
    joblib.dump(
        {"buy": models["buy"], "sell": models["sell"], "feature_columns": feat_cols},
        artifact_path,
    )
    meta = {
        "model_version": MODEL_VERSION,
        "target_precision": target_precision,
        "train_stride_min": train_stride_min,
        "horizon_bars": HORIZON_BARS,
        "hyperparams": dataclasses.asdict(hyperparams),
        "results": {d: vars(r) for d, r in results.items()},
        "train_start": str(train.index[0]),
        "train_end": str(train.index[-1]),
        "val_start": str(val.index[0]),
        "val_end": str(val.index[-1]),
        "test_start": str(test.index[0]),
        "test_end": str(test.index[-1]),
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=str))
    log.info("artifacts written", extra={"model": str(artifact_path), "meta": str(meta_path)})

    _print_report(results, target_precision)


def _build_grid(conn: psycopg.Connection, stride_min: int) -> pd.DatetimeIndex:
    """Sample one timestamp every `stride_min` minutes over the AUD/USD span in `ticks`."""
    row = conn.execute(
        "SELECT MIN(time), MAX(time) FROM ticks WHERE instrument = %s",
        (config.INSTRUMENT,),
    ).fetchone()
    first, last = row
    grid = pd.date_range(
        start=pd.Timestamp(first).ceil(f"{stride_min}min"),
        end=pd.Timestamp(last).floor(f"{stride_min}min"),
        freq=f"{stride_min}min",
        tz="UTC",
    )
    return grid


def _time_split(
    df: pd.DataFrame, gap_rows: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    n = len(df)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)
    train = df.iloc[: max(0, train_end - gap_rows)]
    val = df.iloc[train_end : max(train_end, val_end - gap_rows)]
    test = df.iloc[val_end:]
    return train, val, test


def _pick_threshold(y: np.ndarray, proba: np.ndarray, target_precision: float) -> float:
    """Smallest threshold whose precision on (y, proba) >= target. 1.01 if unreachable."""
    order = np.argsort(proba)[::-1]
    sorted_y = y[order]
    sorted_p = proba[order]
    tp = 0
    fp = 0
    best_threshold = 1.01
    n = len(sorted_p)
    for i in range(n):
        if sorted_y[i] == 1:
            tp += 1
        else:
            fp += 1
        if i + 1 < n and sorted_p[i + 1] == sorted_p[i]:
            continue
        precision = tp / (tp + fp)
        if precision >= target_precision:
            best_threshold = float(sorted_p[i])
    return best_threshold


def _safe_precision(y: np.ndarray, pred: np.ndarray) -> float:
    if pred.sum() == 0:
        return float("nan")
    return float(precision_score(y, pred, zero_division=0))


def _safe_recall(y: np.ndarray, pred: np.ndarray) -> float:
    if y.sum() == 0:
        return float("nan")
    return float(recall_score(y, pred, zero_division=0))


def _print_report(results: dict[str, DirectionResult], target_precision: float) -> None:
    lines = ["", f"=== Training report (target precision {target_precision:.0%}) ==="]
    for direction, r in results.items():
        lines.append(f"\n[{direction.upper()}]")
        lines.append(
            f"  base rate (train): {r.base_rate:.1%}  ({r.n_pos:,}/{r.n_total:,} positive)"
        )
        lines.append(f"  threshold (chosen on val): {r.threshold:.4f}")
        lines.append(
            f"  VAL  precision={r.val_precision:.3f}  "
            f"recall={r.val_recall:.3f}  signal_rate={r.val_signal_rate:.4%}"
        )
        lines.append(
            f"  TEST precision={r.test_precision:.3f}  "
            f"recall={r.test_recall:.3f}  signal_rate={r.test_signal_rate:.4%}"
        )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
