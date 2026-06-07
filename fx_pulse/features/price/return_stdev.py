"""Realised volatility: stdev of 1-minute log returns over a rolling window.

`return_stdev_<window>` is a simple realised-volatility proxy — how
choppy the last `<window>` of trading minutes has been.
"""
from __future__ import annotations

import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register
from fx_pulse.features.util import log_returns, sample_at

SOURCE = "oanda_ticks"

_HOUR = 60
_DAY = 1440
_WEEK = 5 * _DAY


def _make_compute(window_bars: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        one_min_returns = log_returns(raw[SOURCE], 1)
        rolling_std = one_min_returns.rolling(window_bars, min_periods=window_bars).std()
        return sample_at(rolling_std, timestamps)
    return fn


_WINDOWS = {
    "return_stdev_1h": _HOUR,
    "return_stdev_1d": _DAY,
    "return_stdev_1w": _WEEK,
}
for _name, _w in _WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=f"Stdev of 1-minute log returns over {_name.removeprefix('return_stdev_')}.",
        compute=_make_compute(_w),
    ))
