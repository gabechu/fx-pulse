"""Hybrid decision heads: drop rule, ML shadow signal, threshold query, storage."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator

from fx_pulse import rules
from fx_pulse.db import open_connection
from fx_pulse.ml import infer
from fx_pulse.ml.infer import Predictor, write_prediction

FEATURE_COLUMNS = ["log_return_1w", "price_zscore_1d"]


def test_drop_rule_fires_below_threshold():
    assert rules.drop_rule_buy(pd.Series({"log_return_1w": -0.011}))
    assert not rules.drop_rule_buy(pd.Series({"log_return_1w": -0.01}))
    assert not rules.drop_rule_buy(pd.Series({"log_return_1w": 0.02}))


def test_drop_rule_abstains_on_missing_or_nan():
    assert not rules.drop_rule_buy(pd.Series({"log_return_1w": np.nan}))
    assert not rules.drop_rule_buy(pd.Series({"price_zscore_1d": 1.0}))


def _tiny_predictor() -> Predictor:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, len(FEATURE_COLUMNS)))
    y = (x[:, 0] + 0.1 * rng.normal(size=400) > 0).astype(int)
    base = HistGradientBoostingClassifier(max_iter=20, random_state=0).fit(x, y)
    cal = CalibratedClassifierCV(FrozenEstimator(base), method="isotonic")
    cal.fit(x, y)
    return Predictor(
        buy_model=cal,
        sell_model=cal,
        feature_columns=FEATURE_COLUMNS,
        model_version="test",
    )


def test_decision_comes_from_rule_head_with_provenance():
    predictor = _tiny_predictor()
    features = pd.DataFrame(
        {"log_return_1w": [-0.02, -0.002], "price_zscore_1d": [0.0, 0.0]},
        index=pd.DatetimeIndex(["2026-01-05", "2026-01-06"], tz="UTC"),
    )
    fired, quiet = predictor.predict_frame(features, ml_buy_threshold=None)
    assert fired.decision == "BUY"
    assert fired.decision_source == rules.SOURCE_DROP_RULE
    assert quiet.decision == "NO_DECISION"
    assert quiet.decision_source is None


def test_ml_shadow_signal_never_drives_decision():
    predictor = _tiny_predictor()
    features = pd.DataFrame(
        {"log_return_1w": [0.005], "price_zscore_1d": [3.0]},
        index=pd.DatetimeIndex(["2026-01-05"], tz="UTC"),
    )
    (with_thr,) = predictor.predict_frame(features, ml_buy_threshold=0.0)
    assert with_thr.ml_buy_signal  # raw score always >= 0.0
    assert with_thr.decision == "NO_DECISION"  # shadow head must not trade
    (no_thr,) = predictor.predict_frame(features, ml_buy_threshold=None)
    assert not no_thr.ml_buy_signal
    assert no_thr.ml_buy_threshold is None


def test_raw_score_is_uncalibrated_and_tie_free_source():
    predictor = _tiny_predictor()
    features = pd.DataFrame(
        {"log_return_1w": [-0.02, 0.01], "price_zscore_1d": [0.0, 1.0]},
        index=pd.DatetimeIndex(["2026-01-05", "2026-01-06"], tz="UTC"),
    )
    a, b = predictor.predict_frame(features, ml_buy_threshold=None)
    assert 0.0 <= a.buy_raw_score <= 1.0
    assert a.buy_raw_score != b.buy_raw_score


def _write_row(conn, predictor, t: pd.Timestamp, raw: float) -> None:
    pred = infer.Prediction(
        decision="NO_DECISION",
        decision_source=None,
        buy_proba=0.1,
        sell_proba=0.1,
        buy_raw_score=raw,
        ml_buy_threshold=None,
        ml_buy_signal=False,
        features_used={},
    )
    write_prediction(
        conn, pred, predicted_at=t, feature_at=t,
        instrument="AUD_USD", model_version=predictor.model_version,
    )


def test_write_prediction_roundtrips_head_columns(pg_dsn):
    predictor = _tiny_predictor()
    with open_connection(pg_dsn) as conn:
        _write_row(conn, predictor, pd.Timestamp("2026-01-05 10:00", tz="UTC"), 0.42)
        row = conn.execute(
            "SELECT decision, decision_source, buy_raw_score, "
            "ml_buy_threshold, ml_buy_signal FROM predictions"
        ).fetchone()
    assert row == ("NO_DECISION", None, 0.42, None, False)


def test_ml_buy_threshold_at(pg_dsn, monkeypatch):
    monkeypatch.setattr(infer, "ML_MIN_TRAIL_ROWS", 5)
    predictor = _tiny_predictor()
    asof = pd.Timestamp("2026-02-02 00:00", tz="UTC")
    with open_connection(pg_dsn) as conn:
        # Mon 2026-01-05 .. Fri 2026-01-09, scores 0.0 .. 0.4
        for i in range(5):
            t = pd.Timestamp("2026-01-05 10:00", tz="UTC") + pd.Timedelta(days=i)
            _write_row(conn, predictor, t, i / 10)
        # A weekend outlier must be excluded from the quantile
        _write_row(conn, predictor, pd.Timestamp("2026-01-10 10:00", tz="UTC"), 0.99)

        threshold = predictor.ml_buy_threshold_at(asof, conn)
        assert threshold == pytest.approx(np.percentile([0.0, 0.1, 0.2, 0.3, 0.4], 98))

        monkeypatch.setattr(infer, "ML_MIN_TRAIL_ROWS", 6)
        assert predictor.ml_buy_threshold_at(asof, conn) is None