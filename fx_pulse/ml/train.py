"""Train two binary classifiers (buy / sell), pick precision-targeted thresholds.

End-to-end pipeline:

1. Pull 1-min mid prices from Postgres.
2. Compute features and forward labels.
3. Chronological 70/15/15 split with a HORIZON_BARS gap between splits to
   prevent label-leakage across the boundary.
4. Train HistGradientBoostingClassifier per direction. Wrap in
   CalibratedClassifierCV (isotonic, prefit) on the validation slice so the
   probabilities we threshold are well-calibrated.
5. Pick the smallest probability threshold on validation that achieves the
   target precision; report precision/recall + signal frequency on test.
6. Persist artifacts to MODEL_DIR.

Run with `make train-model` (calls `python -m fx_pulse.ml.train`).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import precision_score, recall_score

from fx_pulse.ml.dataset import load_mid_prices
from fx_pulse.ml.features import compute_features, feature_columns
from fx_pulse.ml.labels import HORIZON_BARS, compute_labels
from fx_pulse.obs import get_logger

log = get_logger(__name__)

DEFAULT_TARGET_PRECISION = 0.90
DEFAULT_TRAIN_STRIDE = 15  # use every Nth bar for training to reduce label correlation
DEFAULT_MODEL_DIR = Path(os.environ.get("FX_PULSE_MODEL_DIR", "data/models"))


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
    n_train_pos: int
    n_train: int


def main(
    target_precision: float = DEFAULT_TARGET_PRECISION,
    train_stride: int = DEFAULT_TRAIN_STRIDE,
    model_dir: Path = DEFAULT_MODEL_DIR,
) -> None:
    log.info("loading mid prices from postgres")
    df = load_mid_prices()
    log.info(
        "loaded mid prices",
        extra={"rows": len(df), "first": str(df.index[0]), "last": str(df.index[-1])},
    )

    log.info("computing features")
    features = compute_features(df["mid"])
    log.info("computed features", extra={"rows": len(features)})

    log.info("computing labels")
    labels = compute_labels(df["mid"])
    log.info(
        "computed labels",
        extra={
            "rows": len(labels),
            "buy_rate": float(labels["buy"].mean()),
            "sell_rate": float(labels["sell"].mean()),
        },
    )

    joined = features.join(labels, how="inner")
    log.info("joined feature+label rows", extra={"rows": len(joined)})

    train, val, test = _time_split(joined, gap_bars=HORIZON_BARS)
    log.info(
        "split sizes",
        extra={"train": len(train), "val": len(val), "test": len(test)},
    )

    # Downsample training rows: adjacent minute bars share most of their
    # forward label, so taking every Nth row removes redundancy without
    # discarding information.
    train_ds = train.iloc[::train_stride]
    log.info(
        "downsampled training set",
        extra={"stride": train_stride, "rows": len(train_ds)},
    )

    feat_cols = feature_columns()
    results: dict[str, DirectionResult] = {}
    models: dict[str, CalibratedClassifierCV] = {}

    for direction in ("buy", "sell"):
        log.info("training direction", extra={"direction": direction})
        x_train = train_ds[feat_cols].to_numpy()
        y_train = train_ds[direction].to_numpy()
        x_val = val[feat_cols].to_numpy()
        y_val = val[direction].to_numpy()
        x_test = test[feat_cols].to_numpy()
        y_test = test[direction].to_numpy()

        base = HistGradientBoostingClassifier(
            max_iter=400,
            learning_rate=0.05,
            max_leaf_nodes=63,
            min_samples_leaf=200,
            l2_regularization=0.1,
            random_state=0,
        )
        base.fit(x_train, y_train)

        # Calibrate on the validation slice so threshold selection (also on
        # val) operates on well-calibrated probabilities. FrozenEstimator
        # tells CalibratedClassifierCV not to refit the base learner.
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
            n_train_pos=int(y_train.sum()),
            n_train=int(len(y_train)),
        )
        models[direction] = calibrated
        log.info(
            "direction trained",
            extra={"direction": direction, **vars(results[direction])},
        )

    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = model_dir / "model.joblib"
    meta_path = model_dir / "model_meta.json"
    joblib.dump(
        {
            "buy": models["buy"],
            "sell": models["sell"],
            "feature_columns": feat_cols,
        },
        artifact_path,
    )
    meta = {
        "target_precision": target_precision,
        "train_stride": train_stride,
        "horizon_bars": HORIZON_BARS,
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


def _time_split(
    df: pd.DataFrame, gap_bars: int
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Chronological 70/15/15 with a `gap_bars` buffer between splits.

    The buffer is essential: a row in train carries a label that peeks
    HORIZON_BARS forward; without a gap, the last rows of train would have
    labels overlapping the first rows of val.
    """
    n = len(df)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)
    train = df.iloc[: max(0, train_end - gap_bars)]
    val = df.iloc[train_end : max(train_end, val_end - gap_bars)]
    test = df.iloc[val_end:]
    return train, val, test


def _pick_threshold(
    y: np.ndarray, proba: np.ndarray, target_precision: float
) -> float:
    """Smallest threshold in [0,1] whose precision on (y, proba) >= target.

    Returns 1.01 (i.e. never fires) if no threshold reaches the target.
    """
    # Sort candidate thresholds descending; walk down, track positive count
    # and TP count, stop when precision first dips below target after having
    # been above it.
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
        # Only consider committing to a threshold at the end of a run of
        # ties — otherwise we'd be claiming precision over a partial group.
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
    lines = [
        "",
        f"=== Training report (target precision {target_precision:.0%}) ===",
    ]
    for direction, r in results.items():
        lines.append(f"\n[{direction.upper()}]")
        lines.append(
            f"  base rate (train): {r.base_rate:.1%}  "
            f"({r.n_train_pos:,}/{r.n_train:,} positive)"
        )
        lines.append(f"  threshold (chosen on val): {r.threshold:.4f}")
        lines.append(
            f"  VAL  precision={r.val_precision:.3f}  "
            f"recall={r.val_recall:.3f}  "
            f"signal_rate={r.val_signal_rate:.4%}"
        )
        lines.append(
            f"  TEST precision={r.test_precision:.3f}  "
            f"recall={r.test_recall:.3f}  "
            f"signal_rate={r.test_signal_rate:.4%}"
        )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
