"""Point-in-time feature assembly.

`assemble(timestamps, [feature_names])` returns a DataFrame indexed by
`timestamps` with one column per requested feature (default: all). The
exact same call shape works for training (1M+ historical timestamps) and
online serving (one current timestamp).

Loads each needed source once from Postgres, sliced to
`[min(timestamps) - max_lookback, max(timestamps)]`, then dispatches to each
feature's `compute` function.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

import pandas as pd
import psycopg

from fx_pulse.features.registry import FEATURES, RawData


def assemble(
    timestamps: pd.DatetimeIndex,
    feature_names: Optional[Iterable[str]] = None,
    dsn: Optional[str] = None,
    instrument: str = "AUD_USD",
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

    dsn = dsn or os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn:
        raw: RawData = {}
        for src, lookback in max_lookback_by_source.items():
            start = timestamps.min() - lookback
            end = timestamps.max() + pd.Timedelta(seconds=1)
            raw[src] = _SOURCE_LOADERS[src](conn, start, end, instrument)

    return pd.DataFrame({s.name: s.compute(timestamps, raw) for s in specs}, index=timestamps)


def _load_oanda_ticks(
    conn: psycopg.Connection, start: pd.Timestamp, end: pd.Timestamp, instrument: str
) -> pd.Series:
    """1-min mid-price Series, indexed by minute, no forward-fill across gaps."""
    df = pd.read_sql(
        "SELECT time, bid, ask FROM ticks "
        "WHERE instrument = %(instrument)s AND time >= %(start)s AND time < %(end)s "
        "ORDER BY time",
        conn,
        params={"instrument": instrument, "start": start, "end": end},
        parse_dates=["time"],
    )
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df = df.set_index("time")["mid"]
    return df.resample("1min").last().dropna()


def _load_rba_decisions(
    conn: psycopg.Connection, start: pd.Timestamp, end: pd.Timestamp, _instrument: str
) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT event_time, rate_pct, change_bps FROM rba_decisions "
        "WHERE event_time >= %(start)s AND event_time < %(end)s "
        "ORDER BY event_time",
        conn,
        params={"start": start, "end": end},
        parse_dates=["event_time"],
    )
    if not df.empty:
        df["rate_pct"] = df["rate_pct"].astype(float)
        df["change_bps"] = df["change_bps"].astype(float)
    return df


def _load_cpi_releases(
    conn: psycopg.Connection, start: pd.Timestamp, end: pd.Timestamp, _instrument: str
) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT event_time, actual_yoy_pct, forecast_yoy_pct FROM cpi_releases "
        "WHERE event_time >= %(start)s AND event_time < %(end)s "
        "ORDER BY event_time",
        conn,
        params={"start": start, "end": end},
        parse_dates=["event_time"],
    )
    if not df.empty:
        df["actual_yoy_pct"] = df["actual_yoy_pct"].astype(float)
        df["forecast_yoy_pct"] = df["forecast_yoy_pct"].astype(float)
    return df


_SOURCE_LOADERS = {
    "oanda_ticks": _load_oanda_ticks,
    "rba_decisions": _load_rba_decisions,
    "cpi_releases": _load_cpi_releases,
}
