"""Log returns over multiple horizons.

`log_return_<window>` = log(mid_t / mid_{t-window}). Symmetric and
additive across periods; the standard return measure for FX.
"""
from __future__ import annotations

import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register
from fx_pulse.features.util import log_returns, sample_at

SOURCE = "oanda_ticks"

_MIN = 1
_HOUR = 60
_DAY = 1440
_WEEK = 5 * _DAY
_MONTH = 20 * _DAY
_QUARTER = 60 * _DAY
_YEAR = 250 * _DAY


def _make_compute(window_bars: int):
    def fn(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
        return sample_at(log_returns(raw[SOURCE], window_bars), timestamps)
    return fn


_HORIZONS = {
    "log_return_5m": 5 * _MIN,
    "log_return_15m": 15 * _MIN,
    "log_return_1h": _HOUR,
    "log_return_4h": 4 * _HOUR,
    "log_return_1d": _DAY,
    "log_return_1w": _WEEK,
    "log_return_1mo": _MONTH,
    "log_return_3mo": _QUARTER,
    "log_return_1y": _YEAR,
}
for _name, _w in _HORIZONS.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=pd.Timedelta(minutes=_w),
        description=f"Log return over {_name.removeprefix('log_return_')}.",
        compute=_make_compute(_w),
    ))
