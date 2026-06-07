"""Distance above the rolling-window low.

`pct_above_low_<window>` = (mid - rolling_min) / rolling_min. Always
>= 0; = 0 when the current price is the rolling-window low.
"""
from __future__ import annotations

import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register
from fx_pulse.features.util import sample_at

SOURCE = "oanda_ticks"

_DAY = 1440
_WEEK = 5 * _DAY
_MONTH = 20 * _DAY
_QUARTER = 60 * _DAY
_YEAR = 250 * _DAY
_3Y = 3 * _YEAR


def _make_compute(window_bars: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        mid = raw[SOURCE]
        rolling_min = mid.rolling(window_bars, min_periods=window_bars).min()
        return sample_at((mid - rolling_min) / rolling_min, timestamps)
    return fn


_WINDOWS = {
    "pct_above_low_1d": _DAY,
    "pct_above_low_1w": _WEEK,
    "pct_above_low_1mo": _MONTH,
    "pct_above_low_3mo": _QUARTER,
    "pct_above_low_1y": _YEAR,
    "pct_above_low_3y": _3Y,
}
for _name, _w in _WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=(
            f"(mid - rolling-min-{_name.removeprefix('pct_above_low_')}) / rolling-min. "
            "Always >= 0; 0 means at the window low."
        ),
        compute=_make_compute(_w),
    ))
