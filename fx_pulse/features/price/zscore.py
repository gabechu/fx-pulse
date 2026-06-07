"""Price z-score against a rolling mean/std baseline.

`price_zscore_<window>` = (mid - rolling_mean) / rolling_std. How far the
current price is from its recent mean, in stdevs.
"""
from __future__ import annotations

import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register
from fx_pulse.features.util import sample_at

SOURCE = "oanda_ticks"

_HOUR = 60
_DAY = 1440
_WEEK = 5 * _DAY
_MONTH = 20 * _DAY
_YEAR = 250 * _DAY


def _make_compute(window_bars: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        mid = raw[SOURCE]
        rolling_mean = mid.rolling(window_bars, min_periods=window_bars).mean()
        rolling_std = mid.rolling(window_bars, min_periods=window_bars).std()
        return sample_at((mid - rolling_mean) / rolling_std, timestamps)
    return fn


_WINDOWS = {
    "price_zscore_1h": _HOUR,
    "price_zscore_1d": _DAY,
    "price_zscore_1w": _WEEK,
    "price_zscore_1mo": _MONTH,
    "price_zscore_1y": _YEAR,
}
for _name, _w in _WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=f"Price z-score vs. {_name.removeprefix('price_zscore_')} rolling mean/std.",
        compute=_make_compute(_w),
    ))
