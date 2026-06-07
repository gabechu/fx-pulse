"""14-period Wilder RSI on the 1-minute mid price.

Standard momentum indicator; values near 30/70 are conventionally
"oversold" / "overbought".
"""
from __future__ import annotations

import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register
from fx_pulse.features.util import rsi, sample_at

SOURCE = "oanda_ticks"
PERIOD = 14


def _compute(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    return sample_at(rsi(raw[SOURCE], PERIOD), timestamps)


register(FeatureSpec(
    name=f"rsi_{PERIOD}",
    source=SOURCE,
    lookback=pd.Timedelta(minutes=PERIOD * 10),  # EWM convergence buffer
    description=f"{PERIOD}-period Wilder RSI on 1-minute mid price.",
    compute=_compute,
))
