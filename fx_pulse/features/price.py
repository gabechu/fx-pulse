"""AUD/USD price-derived features.

The raw source is a 1-minute mid-price Series indexed by event_time
(loaded from the `ticks` table by `assemble._load_oanda_ticks`).

Every compute fn:
1. Reads the full mid Series from `raw["oanda_ticks"]`.
2. Computes its statistic over the entire Series (pandas rolling).
3. Reindexes onto the requested `timestamps`, forward-filling so a
   timestamp that doesn't coincide exactly with a 1-minute bar still gets
   the most recent known value (point-in-time correct — never peeks ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register

SOURCE = "oanda_ticks"


def _at(series: pd.Series, timestamps: pd.DatetimeIndex) -> pd.Series:
    # reindex with method='ffill' samples the most-recent value at-or-before
    # each requested timestamp. tolerance=None means a target timestamp
    # before the first available bar yields NaN, which is the right answer.
    return series.reindex(timestamps, method="ffill")


def _log_returns(mid: pd.Series, periods: int) -> pd.Series:
    return np.log(mid).diff(periods)


def _make_return_fn(periods: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        mid = raw[SOURCE]
        return _at(_log_returns(mid, periods), timestamps)
    return fn


def _make_vol_fn(window: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        mid = raw[SOURCE]
        ret = np.log(mid).diff(1)
        return _at(ret.rolling(window, min_periods=window).std(), timestamps)
    return fn


def _make_zscore_fn(window: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        mid = raw[SOURCE]
        mean = mid.rolling(window, min_periods=window).mean()
        std = mid.rolling(window, min_periods=window).std()
        return _at((mid - mean) / std, timestamps)
    return fn


def _make_dist_high_fn(window: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        mid = raw[SOURCE]
        hi = mid.rolling(window, min_periods=window).max()
        return _at((mid - hi) / hi, timestamps)
    return fn


def _make_dist_low_fn(window: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        mid = raw[SOURCE]
        lo = mid.rolling(window, min_periods=window).min()
        return _at((mid - lo) / lo, timestamps)
    return fn


def _rsi(price: pd.Series, period: int) -> pd.Series:
    delta = price.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _rsi_14(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    mid = raw[SOURCE]
    return _at(_rsi(mid, 14), timestamps)


def _bollinger_pos(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    mid = raw[SOURCE]
    w = 1440  # 1 day
    ma = mid.rolling(w, min_periods=w).mean()
    sd = mid.rolling(w, min_periods=w).std()
    return _at((mid - ma) / (2.0 * sd), timestamps)


# Windows expressed in 1-minute bars. FX weekend gaps drop out of the bar
# count, so "1d" here means 1440 *available* bars, i.e. ~1 trading day.
_MIN = 1
_HOUR = 60
_DAY = 1440
_WEEK = 5 * _DAY        # 5 trading days
_MONTH = 20 * _DAY      # 20 trading days
_QUARTER = 60 * _DAY    # 60 trading days
_YEAR = 250 * _DAY      # ~250 trading days
_3Y = 3 * _YEAR

_RETURN_HORIZONS = {
    "ret_5m": 5 * _MIN,
    "ret_15m": 15 * _MIN,
    "ret_1h": _HOUR,
    "ret_4h": 4 * _HOUR,
    "ret_1d": _DAY,
    "ret_1w": _WEEK,
    "ret_1mo": _MONTH,
    "ret_3mo": _QUARTER,
    "ret_1y": _YEAR,
}
for _name, _h in _RETURN_HORIZONS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_h),
        description=f"Log return over {_name.split('_', 1)[1]}.",
        compute=_make_return_fn(_h),
    ))

_VOL_WINDOWS = {"vol_1h": _HOUR, "vol_1d": _DAY, "vol_1w": _WEEK}
for _name, _w in _VOL_WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=f"Stdev of 1-min log returns over {_name.split('_', 1)[1]}.",
        compute=_make_vol_fn(_w),
    ))

_ZSCORE_WINDOWS = {
    "z_1h": _HOUR, "z_1d": _DAY, "z_1w": _WEEK, "z_1mo": _MONTH, "z_1y": _YEAR,
}
for _name, _w in _ZSCORE_WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=f"Price z-score vs. {_name.split('_', 1)[1]} rolling mean/std.",
        compute=_make_zscore_fn(_w),
    ))

_HIGH_WINDOWS = {
    "dist_high_1d": _DAY, "dist_high_1w": _WEEK, "dist_high_1mo": _MONTH,
    "dist_high_3mo": _QUARTER, "dist_high_1y": _YEAR, "dist_high_3y": _3Y,
}
for _name, _w in _HIGH_WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=f"(price - rolling-max-{_name.rsplit('_', 1)[1]}) / rolling-max.",
        compute=_make_dist_high_fn(_w),
    ))

_LOW_WINDOWS = {
    "dist_low_1d": _DAY, "dist_low_1w": _WEEK, "dist_low_1mo": _MONTH,
    "dist_low_3mo": _QUARTER, "dist_low_1y": _YEAR, "dist_low_3y": _3Y,
}
for _name, _w in _LOW_WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=f"(price - rolling-min-{_name.rsplit('_', 1)[1]}) / rolling-min.",
        compute=_make_dist_low_fn(_w),
    ))

register(FeatureSpec(
    name="rsi_14",
    source=SOURCE,
    lookback=pd.Timedelta(minutes=14 * _MIN * 10),  # EWM convergence buffer
    description="14-period RSI on 1-minute mid price.",
    compute=_rsi_14,
))

register(FeatureSpec(
    name="bollinger_pos",
    source=SOURCE,
    lookback=pd.Timedelta(minutes=_DAY),
    description="(price - 1d MA) / (2 * 1d stdev). Position inside 1-day Bollinger band.",
    compute=_bollinger_pos,
))
