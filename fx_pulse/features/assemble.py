"""Point-in-time feature assembly on a Hamilton DAG.

`assemble(timestamps, conn, [feature_names])` returns a DataFrame indexed
by `timestamps` with one column per requested feature (default: all).
The exact same call shape works for training (1M+ historical timestamps)
and online serving (one current timestamp) — train/serve parity by
construction.

Feature functions (`price.py`, `rates.py`) map full-history source data
to full-history series. This module loads each source the requested
features need once from Postgres, sliced to
`[min(timestamps) - lookback, max(timestamps)]`, runs the DAG, and
samples every output at `timestamps` with a backward fill. That sampling
is the point-in-time rule: the value at t reflects only source rows at or
before t, and a t before the first row is NaN ("we knew nothing yet").

The caller owns the connection (open it via `fx_pulse.db.open_connection`
and pass it in). That keeps this layer unit-testable with a stub
connection and lets callers reuse a long-lived connection across many
`assemble()` calls in a training run.
"""
from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd
import psycopg
from hamilton import base, driver

from fx_pulse import config
from fx_pulse.features import price, rates


def _load_mid(
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


# DAG input node → (loader, history loaded before the earliest timestamp).
# Keep each lookback >= the longest feature window on that source; the
# rba one also covers long policy holds + the 90d momentum window.
# Adding a data source = add a loader above and a row here; feature
# functions take it as a parameter named after the input node.
_SOURCES = {
    "mid": (_load_mid, pd.Timedelta(minutes=3 * price.YEAR)),
    "rba_cash_rate": (_load_rba_cash_rate, pd.Timedelta(days=800)),
}

_driver = (
    driver.Builder()
    .with_modules(price, rates)
    .with_adapter(base.SimplePythonGraphAdapter(base.DictResult()))
    .build()
)

FEATURES: dict[str, str] = {
    v.name: v.documentation or ""
    for v in sorted(_driver.list_available_variables(), key=lambda v: v.name)
    if not v.is_external_input
}


def assemble(
    timestamps: pd.DatetimeIndex,
    conn: psycopg.Connection,
    feature_names: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Materialise features for the given timestamps."""
    if timestamps.tz is None:
        raise ValueError("timestamps must be tz-aware (UTC recommended)")
    names = list(feature_names) if feature_names is not None else list(FEATURES)

    end = timestamps.max() + pd.Timedelta(seconds=1)
    inputs = {}
    for v in _driver.what_is_upstream_of(*names):
        if v.is_external_input:
            loader, lookback = _SOURCES[v.name]
            inputs[v.name] = loader(conn, timestamps.min() - lookback, end)

    outputs = _driver.execute(names, inputs=inputs)
    return pd.DataFrame(
        {name: outputs[name].reindex(timestamps, method="ffill") for name in names},
        index=timestamps,
    )