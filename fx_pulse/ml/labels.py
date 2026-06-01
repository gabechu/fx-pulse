"""Forward-looking labels for oversold/overbought classification.

For each bar t, compare mid at t and at t+HORIZON_BARS:

- `buy`  = 1 if mid[t+H] / mid[t] - 1 >=  THRESHOLD
- `sell` = 1 if mid[t+H] / mid[t] - 1 <= -THRESHOLD

I.e. "30 trading days from now, is the position in the black by at least
0.5%?" Tighter than a max-in-window definition: a window-max label has a
~90% positive rate on AUD/USD at this threshold, leaving the model no
useful signal to learn. Point-in-time forward return gives a near-50% base
rate per direction.

THRESHOLD = 0.5%: enough to clear typical OANDA spread and leave real profit.
HORIZON = 30 trading days in minute-bars (FX weekend gaps drop out of the
row count, so this is ~30 actual trading days regardless of calendar).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON_BARS = 30 * 1440  # 30 trading days of 1-min bars
PROFIT_THRESHOLD = 0.005  # 0.5%


def compute_labels(
    mid: pd.Series,
    horizon_bars: int = HORIZON_BARS,
    threshold: float = PROFIT_THRESHOLD,
) -> pd.DataFrame:
    """Return a DataFrame with `buy` and `sell` integer labels aligned to mid.

    Bars without a full forward horizon are excluded.
    """
    future_mid = mid.shift(-horizon_bars)
    forward_return = future_mid / mid - 1.0

    buy = (forward_return >= threshold).astype(np.int8)
    sell = (forward_return <= -threshold).astype(np.int8)

    out = pd.DataFrame({"buy": buy, "sell": sell}, index=mid.index)
    return out[forward_return.notna()]
