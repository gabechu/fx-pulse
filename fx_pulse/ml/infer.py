"""Online inference: load trained artifacts, predict BUY / SELL / NO_DECISION.

Loader returns a `Predictor` whose `.predict(mid_history)` takes a 1-minute
mid-price Series ending at the bar you want to classify (at least
`LOOKBACK_BARS` rows of history; extra is fine) and returns one of:
    "BUY", "SELL", "NO_DECISION"
along with the underlying probabilities.

If both directions fire we return NO_DECISION — that means the model thinks
the entry is volatile enough to plausibly go either way, which is not the
high-precision regime we trained for.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import joblib
import pandas as pd

from fx_pulse.ml.features import LOOKBACK_BARS, compute_features

Decision = Literal["BUY", "SELL", "NO_DECISION"]


@dataclass(frozen=True)
class Prediction:
    decision: Decision
    buy_proba: float
    sell_proba: float


class Predictor:
    def __init__(
        self,
        buy_model,
        sell_model,
        feature_columns: list[str],
        buy_threshold: float,
        sell_threshold: float,
    ) -> None:
        self._buy = buy_model
        self._sell = sell_model
        self._feature_columns = feature_columns
        self._buy_threshold = buy_threshold
        self._sell_threshold = sell_threshold

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
        )

    def predict(self, mid_history: pd.Series) -> Prediction:
        if len(mid_history) < LOOKBACK_BARS:
            return Prediction("NO_DECISION", float("nan"), float("nan"))
        features = compute_features(mid_history)
        if features.empty:
            return Prediction("NO_DECISION", float("nan"), float("nan"))
        last = features.iloc[[-1]][self._feature_columns].to_numpy()
        buy_p = float(self._buy.predict_proba(last)[0, 1])
        sell_p = float(self._sell.predict_proba(last)[0, 1])
        buy_fires = buy_p >= self._buy_threshold
        sell_fires = sell_p >= self._sell_threshold
        if buy_fires and not sell_fires:
            decision: Decision = "BUY"
        elif sell_fires and not buy_fires:
            decision = "SELL"
        else:
            decision = "NO_DECISION"
        return Prediction(decision, buy_p, sell_p)
