"""AUD/USD price-derived features.

Input `mid` is the 1-minute mid-price Series loaded by `assemble`. Each
function maps it to a full-history feature series; `assemble` samples the
result at the requested timestamps. `@parameterize` fans one function out
into one feature per named window — the output name is the feature name.

All rolling windows are in *trading* 1-minute bars: the loader drops
empty minutes, so e.g. `DAY` = 1440 means ~1 trading day, not 1
wall-clock day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from hamilton.function_modifiers import parameterize, value

MIN = 1
HOUR = 60
DAY = 1440
WEEK = 5 * DAY
MONTH = 20 * DAY
QUARTER = 60 * DAY
YEAR = 250 * DAY


def _windows(family: str, bars_by_suffix: dict) -> dict:
    return {f"{family}_{suffix}": {"w": value(bars)} for suffix, bars in bars_by_suffix.items()}


@parameterize(**_windows("log_return", {
    "5m": 5 * MIN, "15m": 15 * MIN, "1h": HOUR, "4h": 4 * HOUR, "1d": DAY,
    "1w": WEEK, "1mo": MONTH, "3mo": QUARTER, "1y": YEAR,
}))
def log_return(mid: pd.Series, w: int) -> pd.Series:
    """log(mid_t / mid_{t-w}). Symmetric and additive across periods; the
    standard return measure for FX."""
    return np.log(mid).diff(w)


@parameterize(**_windows("price_zscore", {
    "1h": HOUR, "1d": DAY, "1w": WEEK, "1mo": MONTH, "1y": YEAR,
}))
def price_zscore(mid: pd.Series, w: int) -> pd.Series:
    """(mid - rolling mean) / rolling std. How far the current price is
    from its recent mean, in stdevs."""
    r = mid.rolling(w, min_periods=w)
    return (mid - r.mean()) / r.std()


@parameterize(**_windows("pct_below_high", {
    "1d": DAY, "1w": WEEK, "1mo": MONTH, "3mo": QUARTER, "1y": YEAR, "3y": 3 * YEAR,
}))
def pct_below_high(mid: pd.Series, w: int) -> pd.Series:
    """(mid - rolling max) / rolling max. Always <= 0; 0 at the window high."""
    high = mid.rolling(w, min_periods=w).max()
    return (mid - high) / high


@parameterize(**_windows("pct_above_low", {
    "1d": DAY, "1w": WEEK, "1mo": MONTH, "3mo": QUARTER, "1y": YEAR, "3y": 3 * YEAR,
}))
def pct_above_low(mid: pd.Series, w: int) -> pd.Series:
    """(mid - rolling min) / rolling min. Always >= 0; 0 at the window low."""
    low = mid.rolling(w, min_periods=w).min()
    return (mid - low) / low


@parameterize(**_windows("return_stdev", {"1h": HOUR, "1d": DAY, "1w": WEEK}))
def return_stdev(mid: pd.Series, w: int) -> pd.Series:
    """Stdev of 1-minute log returns over the window — a simple
    realised-volatility proxy for how choppy the window has been."""
    return np.log(mid).diff().rolling(w, min_periods=w).std()


def bollinger_pos_1d(mid: pd.Series) -> pd.Series:
    """(mid - 1d MA) / (2 * 1d stdev). Position inside the 1-day Bollinger
    band; 0 at the mean, +/-1 at the band edges."""
    r = mid.rolling(DAY, min_periods=DAY)
    return (mid - r.mean()) / (2.0 * r.std())


def rsi_14(mid: pd.Series) -> pd.Series:
    """14-period Wilder RSI in [0, 100]; values near 30/70 are
    conventionally oversold/overbought."""
    delta = mid.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))