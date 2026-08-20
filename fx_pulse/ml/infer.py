"""Online inference: hybrid decision heads over one assembled feature row.

Live decisions come from rule heads (`fx_pulse.rules`, currently the
drop rule); each BUY row records which head fired in `decision_source`.

The ML buy head runs in *shadow*: its raw (uncalibrated) score is
compared against an adaptive threshold — the trailing 42-calendar-day
98th percentile of its own stored scores — and the would-be signal is
logged (`ml_buy_signal`), never traded. Absolute calibrated thresholds
are dead out-of-regime (walk-forward: 0/4 folds ever signalled) and
isotonic calibration collapses the served probability onto plateaus, so
the shadow head ranks on raw scores instead. Promotion to a live head is
a deliberate future change, justified by its logged precision.

The SELL head remains degenerate (see rates.py); its calibrated
probability is stored for diagnostics and never drives a decision.

`Predictor.load(model_dir)` returns a Predictor wrapping both calibrated
classifiers plus the unwrapped raw buy estimator.

`Predictor.predict_at(t, conn)` assembles features for one timestamp,
computes the shadow threshold from stored score history, and returns a
Prediction. `Predictor.predict_frame(features, ml_buy_threshold)` scores
an already-assembled frame with a caller-supplied threshold, so a
backfill can assemble many timestamps in one query.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import joblib
import pandas as pd
import psycopg

from fx_pulse import config, rules
from fx_pulse.features.assemble import assemble

DECISION_BUY = "BUY"
DECISION_NONE = "NO_DECISION"
Decision = Literal["BUY", "NO_DECISION"]

ML_SHADOW_QUANTILE = 0.98
ML_TRAIL = pd.Timedelta(days=42)  # ~30 trading days of score history
# Below this many trailing scores the shadow head abstains: a thin window
# (cold start, outage) makes the quantile a noise statistic.
ML_MIN_TRAIL_ROWS = 20_000


@dataclass(frozen=True)
class Prediction:
    decision: Decision
    decision_source: Optional[str]
    buy_proba: float
    sell_proba: float
    buy_raw_score: float
    ml_buy_threshold: Optional[float]
    ml_buy_signal: bool
    features_used: dict


def _unwrap_estimator(calibrated) -> object:
    """The raw HGB inside CalibratedClassifierCV(FrozenEstimator(...)).

    Isotonic output ties into plateaus; the raw score is the tie-free
    ranking the shadow threshold operates on.
    """
    inner = calibrated.calibrated_classifiers_[0].estimator
    while hasattr(inner, "estimator"):
        inner = inner.estimator
    return inner


class Predictor:
    def __init__(
        self,
        buy_model,
        sell_model,
        feature_columns: list[str],
        model_version: str,
    ) -> None:
        self._buy = buy_model
        self._buy_raw = _unwrap_estimator(buy_model)
        self._sell = sell_model
        self._feature_columns = feature_columns
        self.model_version = model_version

    @classmethod
    def load(cls, model_dir: Path) -> "Predictor":
        artifact = joblib.load(model_dir / "model.joblib")
        meta = json.loads((model_dir / "model_meta.json").read_text())
        return cls(
            buy_model=artifact["buy"],
            sell_model=artifact["sell"],
            feature_columns=artifact["feature_columns"],
            model_version=meta.get("model_version", "unknown"),
        )

    def predict_at(self, t: pd.Timestamp, conn: psycopg.Connection) -> Prediction:
        if t.tz is None:
            raise ValueError("timestamp must be tz-aware")
        threshold = self.ml_buy_threshold_at(t, conn)
        return self.predict_frame(assemble(pd.DatetimeIndex([t]), conn), threshold)[0]

    def ml_buy_threshold_at(
        self, t: pd.Timestamp, conn: psycopg.Connection
    ) -> Optional[float]:
        """Trailing quantile of this model's stored raw scores, or None if the
        window is too thin to trust. Weekend rows are excluded — their features
        are ffilled from Friday and would duplicate one score into the quantile.
        """
        threshold, n = conn.execute(
            "SELECT percentile_cont(%(q)s) WITHIN GROUP (ORDER BY buy_raw_score), "
            "count(*) FROM predictions "
            "WHERE instrument = %(instrument)s AND model_version = %(version)s "
            "AND feature_at >= %(start)s AND feature_at < %(end)s "
            "AND buy_raw_score IS NOT NULL "
            "AND EXTRACT(ISODOW FROM feature_at) < 6",
            {
                "q": ML_SHADOW_QUANTILE,
                "instrument": config.INSTRUMENT,
                "version": self.model_version,
                "start": t - ML_TRAIL,
                "end": t,
            },
        ).fetchone()
        if threshold is None or n < ML_MIN_TRAIL_ROWS:
            return None
        return float(threshold)

    def predict_frame(
        self, features: pd.DataFrame, ml_buy_threshold: Optional[float]
    ) -> list[Prediction]:
        if features.empty:
            return []
        x = features[self._feature_columns].to_numpy()
        buy_probas = self._buy.predict_proba(x)[:, 1]
        raw_scores = self._buy_raw.predict_proba(x)[:, 1]
        sell_probas = self._sell.predict_proba(x)[:, 1]
        out = []
        for (_, row), buy_p, raw, sell_p in zip(
            features.iterrows(), buy_probas, raw_scores, sell_probas
        ):
            rule_fired = rules.drop_rule_buy(row)
            out.append(
                Prediction(
                    decision=DECISION_BUY if rule_fired else DECISION_NONE,
                    decision_source=rules.SOURCE_DROP_RULE if rule_fired else None,
                    buy_proba=float(buy_p),
                    sell_proba=float(sell_p),
                    buy_raw_score=float(raw),
                    ml_buy_threshold=ml_buy_threshold,
                    ml_buy_signal=bool(
                        ml_buy_threshold is not None and raw >= ml_buy_threshold
                    ),
                    features_used={
                        k: (None if pd.isna(v) else float(v)) for k, v in row.items()
                    },
                )
            )
        return out


def write_prediction(
    conn: psycopg.Connection,
    pred: Prediction,
    predicted_at: pd.Timestamp,
    feature_at: pd.Timestamp,
    instrument: str,
    model_version: str,
) -> None:
    """Append a prediction row. Uses ON CONFLICT to make re-runs idempotent."""
    conn.execute(
        "INSERT INTO predictions "
        "(predicted_at, feature_at, instrument, model_version, "
        " decision, decision_source, buy_proba, sell_proba, "
        " buy_raw_score, ml_buy_threshold, ml_buy_signal, features_used) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (instrument, predicted_at, model_version) DO NOTHING",
        (
            predicted_at.to_pydatetime(),
            feature_at.to_pydatetime(),
            instrument,
            model_version,
            pred.decision,
            pred.decision_source,
            pred.buy_proba,
            pred.sell_proba,
            pred.buy_raw_score,
            pred.ml_buy_threshold,
            pred.ml_buy_signal,
            json.dumps(pred.features_used),
        ),
    )