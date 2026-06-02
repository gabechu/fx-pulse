"""Forward 30-day-return labels for the AUD/USD oversold/overbought model.

`compute_labels(timestamps, conn)` returns a DataFrame with `buy` and
`sell` Int8 labels at each timestamp:

- `buy`  = 1 if mid[t + 30d] / mid[t] - 1 >=  PROFIT_THRESHOLD
- `sell` = 1 if mid[t + 30d] / mid[t] - 1 <= -PROFIT_THRESHOLD

Rows where the forward window runs past available data are dropped.

Labels are training-only — they need data from the future and so cannot
be computed at serve time. That's why they live in `fx_pulse.ml`, not
`fx_pulse.features`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg

from fx_pulse import config

HORIZON = pd.Timedelta(days=30 * 7 / 5)  # ~30 trading days in calendar time
HORIZON_BARS = 30 * 1440  # 30 trading days of 1-min bars (FX gaps drop out of count)
PROFIT_THRESHOLD = 0.005


def compute_labels(
    timestamps: pd.DatetimeIndex,
    conn: psycopg.Connection,
    horizon_bars: int = HORIZON_BARS,
    threshold: float = PROFIT_THRESHOLD,
) -> pd.DataFrame:
    """Return a DataFrame with `buy` and `sell` Int8 labels aligned to timestamps."""
    if timestamps.tz is None:
        raise ValueError("timestamps must be tz-aware (UTC recommended)")
    start = timestamps.min()
    end = timestamps.max() + pd.Timedelta(days=60)  # generous calendar buffer for horizon_bars
    df = pd.read_sql(
        "SELECT time, bid, ask FROM ticks "
        "WHERE instrument = %(instrument)s AND time >= %(start)s AND time < %(end)s "
        "ORDER BY time",
        conn,
        params={"instrument": config.INSTRUMENT, "start": start, "end": end},
        parse_dates=["time"],
    )
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    mid = df.set_index("time")["mid"].resample("1min").last().dropna()

    future_mid = mid.shift(-horizon_bars)
    forward_return = future_mid / mid - 1.0

    buy = (forward_return >= threshold).astype(np.int8)
    sell = (forward_return <= -threshold).astype(np.int8)

    # Reindex to the requested timestamps (point-in-time correct ffill)
    buy_at = buy.reindex(timestamps, method="ffill")
    sell_at = sell.reindex(timestamps, method="ffill")
    valid = forward_return.reindex(timestamps, method="ffill").notna()

    out = pd.DataFrame({"buy": buy_at, "sell": sell_at}, index=timestamps)
    return out[valid].astype(np.int8)
