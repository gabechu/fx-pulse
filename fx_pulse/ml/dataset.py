"""Load tick data from Postgres and resample to a 1-minute mid-price series.

Backfill rows are stored as one row per minute already; live rows arrive at
sub-minute cadence. Resampling to the 1-minute close gives one canonical row
per bar regardless of source, so features and labels are computed on the
same grid the streamer will produce at inference time.
"""
from __future__ import annotations

import os

import pandas as pd
import psycopg


def load_mid_prices(
    instrument: str = "AUD_USD",
    dsn: str | None = None,
) -> pd.DataFrame:
    """Return a DataFrame indexed by UTC minute with a single `mid` column.

    Gaps (weekends, market closures) are left as gaps — we do NOT
    forward-fill across them. Feature/label code must tolerate non-contiguous
    indexes.
    """
    dsn = dsn or os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        df = pd.read_sql(
            "SELECT time, bid, ask FROM ticks "
            "WHERE instrument = %(instrument)s "
            "ORDER BY time",
            conn,
            params={"instrument": instrument},
            parse_dates=["time"],
        )
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df = df.set_index("time")[["mid"]]
    # Resample to 1-minute bars using last observation in each minute.
    # `last()` drops minutes with no observations, so gaps stay as gaps.
    return df["mid"].resample("1min").last().dropna().to_frame()
