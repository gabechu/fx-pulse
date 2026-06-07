"""Generic helpers shared across feature modules.

Nothing here is source-specific — everything operates on a plain
time-indexed `pd.Series`. Source-specific compute functions live in
their own module (e.g. `price.py` for `oanda_ticks`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sample_at(series: pd.Series, timestamps: pd.DatetimeIndex) -> pd.Series:
    """Return `series` sampled at `timestamps`, point-in-time correct.

    For each `t` in `timestamps`, returns the value at the most recent
    index entry <= t (forward-fill). A `t` before the first available
    index entry yields NaN, which is the correct answer ("we knew nothing
    yet"). Never peeks ahead.
    """
    return series.reindex(timestamps, method="ffill")


def log_returns(series: pd.Series, periods: int) -> pd.Series:
    """log(series_t / series_{t-periods}) over the full series."""
    return np.log(series).diff(periods)


def rsi(series: pd.Series, period: int) -> pd.Series:
    """Wilder-smoothed RSI on `series`. Returns values in [0, 100]."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))
