"""Distance below the rolling-window high.

`pct_below_high_<window>` = (mid - rolling_max) / rolling_max. Always
<= 0; = 0 when the current price is the rolling-window high.
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
        rolling_max = mid.rolling(window_bars, min_periods=window_bars).max()
        return sample_at((mid - rolling_max) / rolling_max, timestamps)
    return fn


_WINDOWS = {
    "pct_below_high_1d": _DAY,
    "pct_below_high_1w": _WEEK,
    "pct_below_high_1mo": _MONTH,
    "pct_below_high_3mo": _QUARTER,
    "pct_below_high_1y": _YEAR,
    "pct_below_high_3y": _3Y,
}
for _name, _w in _WINDOWS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=(
            f"(mid - rolling-max-{_name.removeprefix('pct_below_high_')}) / rolling-max. "
            "Always <= 0; 0 means at the window high."
        ),
        compute=_make_compute(_w),
    ))
