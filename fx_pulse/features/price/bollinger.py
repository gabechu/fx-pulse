"""Position of the current price inside a 1-day Bollinger band.

`bollinger_pos_1d` = (mid - 1d MA) / (2 * 1d stdev). 0 at the mean,
+/-1 at the band edges.
"""
from __future__ import annotations

import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register
from fx_pulse.features.util import sample_at

SOURCE = "oanda_ticks"
_DAY = 1440


def _compute(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    mid = raw[SOURCE]
    rolling_mean = mid.rolling(_DAY, min_periods=_DAY).mean()
    rolling_std = mid.rolling(_DAY, min_periods=_DAY).std()
    return sample_at((mid - rolling_mean) / (2.0 * rolling_std), timestamps)


register(FeatureSpec(
    name="bollinger_pos_1d",
    source=SOURCE,
    lookback=pd.Timedelta(minutes=_DAY),
    description=(
        "(mid - 1d MA) / (2 * 1d stdev). Position inside the 1-day Bollinger band; "
        "0 at the mean, +/-1 at the band edges."
    ),
    compute=_compute,
))
