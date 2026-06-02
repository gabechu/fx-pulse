"""Online inference: load trained artifacts, predict BUY or NO_DECISION.

Currently BUY-only: the SELL head is degenerate at high precision targets
on the 2026 regime-shifted test set (val threshold saturates at 1.0). We
still load and store its probability for diagnostics, but it never drives
a decision until SELL is fixed.

`Predictor.load(model_dir)` returns a Predictor wrapping the calibrated
buy classifier and its precision-targeted threshold.

`Predictor.predict_at(t)` calls `features.assemble(t)` for one timestamp,
runs the classifier, returns BUY if buy_proba >= buy_threshold else
NO_DECISION.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import joblib
import pandas as pd
import psycopg

from fx_pulse.features.assemble import assemble

Decision = Literal["BUY", "NO_DECISION"]


@dataclass(frozen=True)
class Prediction:
    decision: Decision
    buy_proba: float
    sell_proba: float
    features_used: dict


class Predictor:
    def __init__(
        self,
        buy_model,
        sell_model,
        feature_columns: list[str],
        buy_threshold: float,
        sell_threshold: float,
        model_version: str,
    ) -> None:
        self._buy = buy_model
        self._sell = sell_model
        self._feature_columns = feature_columns
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold
        self.model_version = model_version

    @classmethod
    def load(cls, model_dir: Path) -> "Predictor":
        artifact = joblib.load(model_dir / "model.joblib")
        meta = json.loads((model_dir / "model_meta.json").read_text())
        return cls(
            buy_model=artifact["buy"],
            sell_model=artifact["sell"],
            feature_columns=artifact["feature_columns"],
            buy_threshold=meta["results"]["buy"]["threshold"],
            sell_threshold=meta["results"]["sell"]["threshold"],
            model_version=meta.get("model_version", "unknown"),
        )

    def predict_at(self, t: pd.Timestamp) -> Prediction:
        if t.tz is None:
            raise ValueError("timestamp must be tz-aware")
        timestamps = pd.DatetimeIndex([t])
        feature_row = assemble(timestamps).iloc[0]
        x = feature_row[self._feature_columns].to_numpy().reshape(1, -1)
        buy_p = float(self._buy.predict_proba(x)[0, 1])
        sell_p = float(self._sell.predict_proba(x)[0, 1])
        decision: Decision = "BUY" if buy_p >= self._buy_threshold else "NO_DECISION"
        return Prediction(
            decision=decision,
            buy_proba=buy_p,
            sell_proba=sell_p,
            features_used={k: (None if pd.isna(v) else float(v)) for k, v in feature_row.items()},
        )


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
        " decision, buy_proba, sell_proba, features_used) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (instrument, predicted_at, model_version) DO NOTHING",
        (
            predicted_at.to_pydatetime(),
            feature_at.to_pydatetime(),
            instrument,
            model_version,
            pred.decision,
            pred.buy_proba,
            pred.sell_proba,
            json.dumps(pred.features_used),
        ),
    )


