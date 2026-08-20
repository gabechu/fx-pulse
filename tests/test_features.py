"""Tests for the Hamilton-backed feature assembly.

Postgres is stubbed at the `pd.read_sql` seam, so these pin down the pure
parts: the catalog names the DAG exposes, the wide-frame shape `assemble`
returns, and the point-in-time sampling rule.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fx_pulse.features import FEATURES, assemble


@pytest.fixture
def stub_sources(monkeypatch):
    minutes = pd.date_range("2026-05-01", periods=4 * 1440, freq="1min", tz="UTC")
    mid = pd.Series(np.linspace(0.6500, 0.6600, len(minutes)), index=minutes)

    dates = pd.bdate_range("2024-01-01", "2026-05-05")
    rba = pd.DataFrame(
        {
            "date": dates.tz_localize(None),
            "cash_rate_target": 4.35,
            "change_in_cash_rate_target": np.nan,
        }
    )
    rba.loc[rba.index[-10], "change_in_cash_rate_target"] = -0.25
    rba.loc[rba.index[-10]:, "cash_rate_target"] = 4.10

    def fake_read_sql(sql, conn, params=None, parse_dates=None):
        if "FROM ticks" in sql:
            df = pd.DataFrame(
                {"time": minutes, "bid": mid - 5e-5, "ask": mid + 5e-5}
            )
            return df[(df["time"] >= params["start"]) & (df["time"] < params["end"])]
        if "FROM rba_cash_rate" in sql:
            keep = (rba["date"].dt.date >= params["start"]) & (rba["date"].dt.date < params["end"])
            return rba[keep].reset_index(drop=True)
        raise AssertionError(f"unexpected sql: {sql}")

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    return mid


def test_catalog_covers_price_and_rates():
    assert {"log_return_5m", "price_zscore_1d", "rsi_14", "rba_rate_level"} <= FEATURES.keys()
    assert all(name.isidentifier() for name in FEATURES)


def test_assemble_returns_requested_columns(stub_sources):
    timestamps = pd.DatetimeIndex(
        [pd.Timestamp("2026-05-03 12:00", tz="UTC"), pd.Timestamp("2026-05-04 12:00", tz="UTC")]
    )
    frame = assemble(timestamps, conn=object())
    assert list(frame.columns) == list(FEATURES)
    assert list(frame.index) == list(timestamps)


def test_log_return_is_point_in_time(stub_sources):
    mid = stub_sources
    t = pd.Timestamp("2026-05-03 12:00", tz="UTC")
    frame = assemble(pd.DatetimeIndex([t]), conn=object(), feature_names=["log_return_5m"])
    expected = np.log(mid[t]) - np.log(mid[t - pd.Timedelta(minutes=5)])
    assert frame.loc[t, "log_return_5m"] == pytest.approx(expected)


def test_rba_reflects_latest_decision(stub_sources):
    t = pd.Timestamp("2026-05-04 12:00", tz="UTC")
    frame = assemble(
        pd.DatetimeIndex([t]), conn=object(), feature_names=["rba_rate_level", "rba_last_change"]
    )
    assert frame.loc[t, "rba_rate_level"] == pytest.approx(4.10)
    assert frame.loc[t, "rba_last_change"] == pytest.approx(-0.25)


def test_naive_timestamps_rejected():
    with pytest.raises(ValueError, match="tz-aware"):
        assemble(pd.DatetimeIndex([pd.Timestamp("2026-05-03 12:00")]), conn=object())
