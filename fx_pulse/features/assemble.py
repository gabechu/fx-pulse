"""Point-in-time feature assembly.

`assemble(timestamps, conn, [feature_names])` returns a DataFrame indexed
by `timestamps` with one column per requested feature (default: all).
The exact same call shape works for training (1M+ historical timestamps)
and online serving (one current timestamp).

Loads each needed source once from Postgres, sliced to
`[min(timestamps) - max_lookback, max(timestamps)]`, then dispatches to each
feature's `compute` function.

The caller owns the connection (open it via `fx_pulse.db.open_connection`
and pass it in). That keeps this layer unit-testable with a stub
connection and lets callers reuse a long-lived connection across many
`assemble()` calls in a training run.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd
import psycopg

from fx_pulse import config
from fx_pulse.features.registry import FEATURES, RawData


def assemble(
    timestamps: pd.DatetimeIndex,
    conn: psycopg.Connection,
    feature_names: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Materialise features for the given timestamps."""
    if timestamps.tz is None:
        raise ValueError("timestamps must be tz-aware (UTC recommended)")
    names = list(feature_names) if feature_names is not None else list(FEATURES)
    specs = [FEATURES[n] for n in names]

    sources_needed = {s.source for s in specs}
    max_lookback_by_source = {
        src: max(s.lookback for s in specs if s.source == src) for src in sources_needed
    }

    raw: RawData = {}
    for src, lookback in max_lookback_by_source.items():
        start = timestamps.min() - lookback
        end = timestamps.max() + pd.Timedelta(seconds=1)
        raw[src] = _SOURCE_LOADERS[src](conn, start, end)

    return pd.DataFrame({s.name: s.compute(timestamps, raw) for s in specs}, index=timestamps)


def _load_oanda_ticks(
    conn: psycopg.Connection, start: pd.Timestamp, end: pd.Timestamp
) -> pd.Series:
    """1-min mid-price Series, indexed by minute, no forward-fill across gaps."""
    df = pd.read_sql(
        "SELECT time, bid, ask FROM ticks "
        "WHERE instrument = %(instrument)s AND time >= %(start)s AND time < %(end)s "
        "ORDER BY time",
        conn,
        params={"instrument": config.INSTRUMENT, "start": start, "end": end},
        parse_dates=["time"],
    )
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df = df.set_index("time")["mid"]
    return df.resample("1min").last().dropna()


def _load_rba_cash_rate(
    conn: psycopg.Connection, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """RBA cash-rate target as a daily, tz-aware DataFrame (business days only).

    Indexed at date 00:00 UTC; `cash_rate_target` is the prevailing rate and
    `change_in_cash_rate_target` is non-null only on decision days. Features
    ffill from this, so a timestamp picks up the most recent published rate.
    (Decisions are announced intraday; at daily granularity against a 30-day
    label that few-hour offset is immaterial.)
    """
    df = pd.read_sql(
        "SELECT date, cash_rate_target, change_in_cash_rate_target FROM rba_cash_rate "
        "WHERE date >= %(start)s AND date < %(end)s ORDER BY date",
        conn,
        params={"start": start.date(), "end": end.date()},
        parse_dates=["date"],
    )
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")


# Source name → loader. Adding a new external data source = add a loader
# above and a row here. Feature compute functions read raw[source_name].
_SOURCE_LOADERS = {
    "oanda_ticks": _load_oanda_ticks,
    "rba_cash_rate": _load_rba_cash_rate,
}
