"""Feature engineering on 1-minute mid-price bars.

Every feature uses ONLY information available at the bar close (no peeking
at future bars). The same function runs at train time on a full DataFrame
and at inference time on a recent slice — the contract is "give me at least
LOOKBACK_BARS rows ending at the bar you want a prediction for, get back
one feature row for the last bar."

Windows are in minute-bars, so "1 day" = 1440 bars of *available* data; with
FX weekend gaps the wall-clock window is roughly Mon-Fri. That's fine — we
want session-relative behaviour, not calendar-relative.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Number of past bars needed to compute the longest feature.
# 20 trading days * 1440 min ~ 28800. Round up a little.
LOOKBACK_BARS = 30_000

# Lookback windows (in 1-minute bars) used across features.
_RETURN_HORIZONS = {
    "ret_5m": 5,
    "ret_15m": 15,
    "ret_1h": 60,
    "ret_4h": 240,
    "ret_1d": 1440,
    "ret_5d": 7200,
    "ret_20d": 28800,
}
_VOL_WINDOWS = {
    "vol_1h": 60,
    "vol_1d": 1440,
    "vol_5d": 7200,
}
_ZSCORE_WINDOWS = {
    "z_1h": 60,
    "z_1d": 1440,
    "z_5d": 7200,
    "z_20d": 28800,
}
_EXTREMA_WINDOWS = {
    "dist_high_1d": 1440,
    "dist_high_5d": 7200,
    "dist_high_20d": 28800,
    "dist_low_1d": 1440,
    "dist_low_5d": 7200,
    "dist_low_20d": 28800,
}
_RSI_PERIOD = 14
_BOLLINGER_WINDOW = 1440  # 1 day


def feature_columns() -> list[str]:
    cols = list(_RETURN_HORIZONS)
    cols += list(_VOL_WINDOWS)
    cols += list(_ZSCORE_WINDOWS)
    cols += list(_EXTREMA_WINDOWS)
    cols += ["rsi_14", "bollinger_pos"]
    return cols


def compute_features(mid: pd.Series) -> pd.DataFrame:
    """Compute the feature matrix aligned with `mid`'s index.

    Rows where any feature is undefined (insufficient history) are dropped.
    """
    log_mid = np.log(mid)
    out: dict[str, pd.Series] = {}

    for name, h in _RETURN_HORIZONS.items():
        out[name] = log_mid.diff(h)

    # Volatility = stdev of 1-minute log returns over the window.
    one_min_ret = log_mid.diff(1)
    for name, w in _VOL_WINDOWS.items():
        out[name] = one_min_ret.rolling(w, min_periods=w).std()

    for name, w in _ZSCORE_WINDOWS.items():
        mean = mid.rolling(w, min_periods=w).mean()
        std = mid.rolling(w, min_periods=w).std()
        out[name] = (mid - mean) / std

    for name, w in _EXTREMA_WINDOWS.items():
        if name.startswith("dist_high_"):
            extremum = mid.rolling(w, min_periods=w).max()
        else:
            extremum = mid.rolling(w, min_periods=w).min()
        out[name] = (mid - extremum) / extremum

    out["rsi_14"] = _rsi(mid, _RSI_PERIOD)

    ma = mid.rolling(_BOLLINGER_WINDOW, min_periods=_BOLLINGER_WINDOW).mean()
    sd = mid.rolling(_BOLLINGER_WINDOW, min_periods=_BOLLINGER_WINDOW).std()
    out["bollinger_pos"] = (mid - ma) / (2.0 * sd)

    return pd.DataFrame(out).dropna()


def _rsi(price: pd.Series, period: int) -> pd.Series:
    delta = price.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing approximated with ewm(alpha=1/period).
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))
