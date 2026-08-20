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

The absolute thresholds recorded here are training diagnostics: serving
decisions come from rule heads, with the ML head in shadow behind an
adaptive quantile threshold (see `fx_pulse.ml.infer`).

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
# Threshold support floor: the smallest precision-qualifying operating point
# must admit at least this fraction of eval rows as signals. Stops threshold
# selection from locking onto a noise-dominated, few-sample tail (see
# `_pick_threshold`). To claim a precision target as an actionable edge we
# want it backed by real trade volume, not a handful of lucky bars: 2% of the
# ~4-month val window is ~1.6 signals/day. This is also what keeps the
# regime-degenerate SELL head silent — it only clears the target at a ~1.2%
# signal rate, below the floor, so it refuses rather than trading on noise.
DEFAULT_MIN_SIGNAL_RATE = 0.02
# Walk-forward folds for the regime-robust generalisation estimate (separate
# from the single shipped split). More folds = lower-variance estimate but each
# refits both heads, so cost scales linearly.
DEFAULT_WALK_FORWARD_FOLDS = 4
MODEL_VERSION = "v6"


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
    walk_forward_folds: int = DEFAULT_WALK_FORWARD_FOLDS,
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
        y_train = train[direction].to_numpy().astype(int)
        y_val = val[direction].to_numpy().astype(int)
        y_test = test[direction].to_numpy().astype(int)

        calibrated, threshold, _ = _fit_head(
            train[feat_cols].to_numpy(),
            y_train,
            val[feat_cols].to_numpy(),
            y_val,
            target_precision,
            hyperparams,
        )
        val_prec, val_rec, val_sig = _eval_head(calibrated, threshold, val[feat_cols].to_numpy(), y_val)
        test_prec, test_rec, test_sig = _eval_head(calibrated, threshold, test[feat_cols].to_numpy(), y_test)

        results[direction] = DirectionResult(
            direction=direction,
            threshold=float(threshold),
            val_precision=val_prec,
            val_recall=val_rec,
            val_signal_rate=val_sig,
            test_precision=test_prec,
            test_recall=test_rec,
            test_signal_rate=test_sig,
            base_rate=float(y_train.mean()),
            n_pos=int(y_train.sum()),
            n_total=int(len(y_train)),
        )
        models[direction] = calibrated
        log.info("direction trained", extra={"direction": direction, **vars(results[direction])})

    log.info("walk-forward evaluation", extra={"n_folds": walk_forward_folds})
    wf = _walk_forward_eval(
        joined, feat_cols, target_precision, hyperparams, gap_rows, walk_forward_folds
    )

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
        "walk_forward": {d: [vars(f) for f in folds] for d, folds in wf.items()},
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
    _print_walk_forward(wf)


def _build_grid(conn: psycopg.Connection, stride_min: int) -> pd.DatetimeIndex:
    """Sample one timestamp every `stride_min` minutes over the AUD/USD span in `ticks`.

    Weekends (Sat/Sun UTC) are excluded: FX is closed, so a weekend grid
    timestamp would ffill features and labels from Friday's close,
    producing many duplicate training rows that bias the loss, val/test
    metrics, threshold selection, and calibration. Sunday-evening reopen
    is accepted as collateral; a stricter "fresh tick within X minutes"
    filter is the principled fix if we ever need it.
    """
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
    return grid[grid.dayofweek < 5]


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


def _pick_threshold(
    y: np.ndarray,
    proba: np.ndarray,
    target_precision: float,
    min_signals: int,
) -> float:
    """Smallest threshold whose precision on (y, proba) >= target. 1.01 if unreachable.

    `min_signals` is a support floor: a threshold is only eligible once it
    admits at least that many predicted positives. Without it, precision in
    the top few predictions is computed from a handful of samples (recall
    ~0.5% in practice) and the chosen point is statistical noise that does
    not transfer out-of-sample — the failure mode that left SELL at a
    degenerate threshold. Requiring real support trades a little ceiling
    precision for an operating point that generalises.
    """
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
        if tp + fp < min_signals:
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


def _fit_head(
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    x_cal: np.ndarray,
    y_cal: np.ndarray,
    target_precision: float,
    hyperparams: Hyperparameters,
) -> tuple[CalibratedClassifierCV, float, HistGradientBoostingClassifier]:
    """Fit HGB on the fit slice, isotonic-calibrate on the cal slice, pick a
    precision-targeted threshold on the cal slice. The single producer of a
    (model, threshold) pair — used by both the shipped split and every
    walk-forward fold so the two never drift apart.

    Also returns the uncalibrated base: isotonic produces large tie plateaus,
    so ranking by calibrated proba is unsafe for top-k diagnostics (argsort
    breaks ties by row order, collapsing the top-k onto a contiguous time
    block). The base's raw scores are tie-free and the faithful ranking.
    """
    base = HistGradientBoostingClassifier(**dataclasses.asdict(hyperparams))
    base.fit(x_fit, y_fit)
    calibrated = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
    calibrated.fit(x_cal, y_cal)
    cal_proba = calibrated.predict_proba(x_cal)[:, 1]
    min_signals = max(200, int(DEFAULT_MIN_SIGNAL_RATE * len(y_cal)))
    threshold = _pick_threshold(y_cal, cal_proba, target_precision, min_signals)
    return calibrated, threshold, base


def _eval_head(
    model: CalibratedClassifierCV, threshold: float, x: np.ndarray, y: np.ndarray
) -> tuple[float, float, float]:
    """(precision, recall, signal_rate) of `model` at `threshold` on (x, y)."""
    pred = model.predict_proba(x)[:, 1] >= threshold
    return _safe_precision(y, pred), _safe_recall(y, pred), float(pred.mean())


@dataclass
class FoldResult:
    fold: int
    threshold: float
    precision: float
    recall: float
    signal_rate: float
    prec_at_budget: float  # precision of the top-2% most-confident bars (skill, decoupled from the refuse/trade call)
    test_base_rate: float
    n_test: int
    test_start: str
    test_end: str


def _walk_forward_bounds(
    n: int, gap_rows: int, n_folds: int, initial_frac: float = 0.55
) -> list[tuple[int, int, int, int, int]]:
    """Integer (fit_end, cal_start, cal_end, test_start, test_end) per fold.

    Expanding-window layout, purged + embargoed by `gap_rows` at every seam so
    no 30-day label leaks across a boundary:

        [0 .. fit_end) gap [cal_start .. cal_end) gap [test_start .. test_end)

    The first `initial_frac` of rows is always training-side; the tail is cut
    into `n_folds` equal test blocks, each with an equal-length cal block.
    """
    test_region_start = int(n * initial_frac)
    block = (n - test_region_start) // n_folds
    bounds = []
    for i in range(n_folds):
        test_start = test_region_start + i * block
        test_end = n if i == n_folds - 1 else test_start + block
        cal_end = test_start - gap_rows
        cal_start = cal_end - block
        fit_end = cal_start - gap_rows
        if fit_end <= 0 or cal_start <= 0 or test_end - test_start < 1:
            continue
        bounds.append((fit_end, cal_start, cal_end, test_start, test_end))
    return bounds


def _walk_forward_eval(
    joined: pd.DataFrame,
    feat_cols: list[str],
    target_precision: float,
    hyperparams: Hyperparameters,
    gap_rows: int,
    n_folds: int,
) -> dict[str, list[FoldResult]]:
    """Regime-robust estimate: refit each head on every expanding fold and
    score it on that fold's out-of-sample (and out-of-regime) test block.
    """
    out: dict[str, list[FoldResult]] = {"buy": [], "sell": []}
    bounds = _walk_forward_bounds(len(joined), gap_rows, n_folds)
    for i, (fit_end, cal_start, cal_end, test_start, test_end) in enumerate(bounds):
        fit = joined.iloc[:fit_end]
        cal = joined.iloc[cal_start:cal_end]
        test = joined.iloc[test_start:test_end]
        for direction in ("buy", "sell"):
            model, threshold, base = _fit_head(
                fit[feat_cols].to_numpy(),
                fit[direction].to_numpy().astype(int),
                cal[feat_cols].to_numpy(),
                cal[direction].to_numpy().astype(int),
                target_precision,
                hyperparams,
            )
            x_test = test[feat_cols].to_numpy()
            y_test = test[direction].to_numpy().astype(int)
            pred = model.predict_proba(x_test)[:, 1] >= threshold
            prec, rec, sig = _safe_precision(y_test, pred), _safe_recall(y_test, pred), float(pred.mean())
            # rank by raw base scores (tie-free), not calibrated proba — see _fit_head
            budget = max(1, int(DEFAULT_MIN_SIGNAL_RATE * len(y_test)))
            top = np.argsort(base.predict_proba(x_test)[:, 1])[::-1][:budget]
            out[direction].append(FoldResult(
                fold=i,
                threshold=float(threshold),
                precision=prec,
                recall=rec,
                signal_rate=sig,
                prec_at_budget=float(y_test[top].mean()),
                test_base_rate=float(y_test.mean()),
                n_test=int(len(y_test)),
                test_start=str(test.index[0]),
                test_end=str(test.index[-1]),
            ))
        log.info(
            "walk-forward fold done",
            extra={"fold": i, "test_start": str(test.index[0]), "test_end": str(test.index[-1])},
        )
    return out


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


def _print_walk_forward(wf: dict[str, list[FoldResult]]) -> None:
    """Regime-robust summary: per-fold lines + mean across folds. Precision is
    only defined on folds that actually signalled, so it's averaged over those
    (and we say how many that was); recall/signal-rate average over all folds.
    """
    lines = ["", "=== Walk-forward (regime-robust, out-of-sample per fold) ==="]
    for direction, folds in wf.items():
        lines.append(f"\n[{direction.upper()}]  ({len(folds)} folds)")
        lines.append(
            "  fold  test window               base   thr     prec  signal%  prec@top2%"
        )
        for f in folds:
            window = f"{f.test_start[:10]}→{f.test_end[:10]}"
            prec = "  -  " if f.precision != f.precision else f"{f.precision:.3f}"  # nan check
            lines.append(
                f"  {f.fold:>3}  {window:24s}  {f.test_base_rate:>4.0%}  "
                f"{f.threshold:.3f}  {prec:>5}  {f.signal_rate:>6.2%}    {f.prec_at_budget:.3f}"
            )
        signalled = [f for f in folds if f.signal_rate > 0]
        mean_prec = (
            sum(f.precision for f in signalled) / len(signalled) if signalled else float("nan")
        )
        mean_budget = sum(f.prec_at_budget for f in folds) / len(folds) if folds else float("nan")
        base_avg = sum(f.test_base_rate for f in folds) / len(folds) if folds else float("nan")
        prec_str = "  -  " if mean_prec != mean_prec else f"{mean_prec:.3f}"
        lines.append(
            f"  MEAN  precision={prec_str} ({len(signalled)}/{len(folds)} folds signalled)  "
            f"prec@top2%={mean_budget:.3f}  vs base rate {base_avg:.3f}"
        )
    print("\n".join(lines))


if __name__ == "__main__":
    main()
