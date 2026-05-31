"""Streamlit dashboard for AUD/USD ticks + heuristic signal.

Reads the same Postgres the streamer writes to. Postgres MVCC means readers
never block writers, so backfill/stream/dashboard can all run concurrently
without coordination.

Run: `docker compose up dashboard` then open http://localhost:8501
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

DSN = os.environ.get("DATABASE_URL", "")
INSTRUMENT = "AUD_USD"
REFRESH_INTERVAL = "5s"

# Maps the sidebar selector label to a timedelta. None = no time filter.
LOOKBACK_OPTIONS: dict[str, Optional[timedelta]] = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
    "30d": timedelta(days=30),
    "all": None,
}

_LABEL_COLOR = {"long": "#16a34a", "flat": "#737373", "short": "#dc2626"}


def _load_ticks(conn: psycopg.Connection, since: Optional[datetime]) -> pd.DataFrame:
    if since is None:
        return pd.read_sql_query(
            "SELECT time, bid, ask, source FROM ticks WHERE instrument = %s ORDER BY time",
            conn,
            params=(INSTRUMENT,),
        )
    return pd.read_sql_query(
        "SELECT time, bid, ask, source FROM ticks "
        "WHERE instrument = %s AND time >= %s ORDER BY time",
        conn,
        params=(INSTRUMENT, since),
    )


def _load_signals(conn: psycopg.Connection, since: Optional[datetime]) -> pd.DataFrame:
    if since is None:
        return pd.read_sql_query(
            "SELECT time, short_ma, long_ma, label FROM signals "
            "WHERE instrument = %s ORDER BY time",
            conn,
            params=(INSTRUMENT,),
        )
    return pd.read_sql_query(
        "SELECT time, short_ma, long_ma, label FROM signals "
        "WHERE instrument = %s AND time >= %s ORDER BY time",
        conn,
        params=(INSTRUMENT, since),
    )


st.set_page_config(page_title="fx-pulse", layout="wide")
st.title(f"fx-pulse · {INSTRUMENT}")

lookback = st.sidebar.selectbox(
    "Lookback", list(LOOKBACK_OPTIONS.keys()), index=len(LOOKBACK_OPTIONS) - 1
)
st.caption(f"Lookback: {lookback} · refreshes every {REFRESH_INTERVAL}")


@st.fragment(run_every=REFRESH_INTERVAL)
def render() -> None:
    if not DSN:
        st.warning("DATABASE_URL is not set.")
        return

    delta = LOOKBACK_OPTIONS[lookback]
    since = None if delta is None else datetime.now(timezone.utc) - delta
    try:
        conn = psycopg.connect(DSN)
    except psycopg.OperationalError as e:
        st.warning(f"Can't reach Postgres yet: {e}")
        return
    try:
        ticks = _load_ticks(conn, since)
        signals = _load_signals(conn, since)
    finally:
        conn.close()

    left, mid, right = st.columns(3)
    if signals.empty:
        left.metric("Signal", "warming up")
    else:
        latest = signals.iloc[-1]
        color = _LABEL_COLOR.get(latest["label"], "#737373")
        left.markdown(
            f"#### Signal\n# <span style='color:{color}'>{latest['label'].upper()}</span>",
            unsafe_allow_html=True,
        )

    if ticks.empty:
        mid.metric("Rows", 0)
        right.metric("Latest", "—")
        st.info(f"No ticks in the `{lookback}` window.")
        return

    by_source = ticks["source"].value_counts().to_dict()
    breakdown = " · ".join(f"{k}: {v}" for k, v in sorted(by_source.items()))
    mid.metric("Rows", f"{len(ticks)}", help=breakdown)
    right.metric("Latest", ticks.iloc[-1]["time"].strftime("%Y-%m-%d %H:%M:%SZ"))

    ticks["mid"] = (ticks["bid"] + ticks["ask"]) / 2.0
    chart_df = ticks.assign(
        live=ticks["mid"].where(ticks["source"] == "live"),
        backfill=ticks["mid"].where(ticks["source"] == "candle"),
    ).set_index("time")[["live", "backfill"]]
    if not signals.empty:
        chart_df = chart_df.join(
            signals.set_index("time")[["short_ma", "long_ma"]], how="outer"
        )

    # st.line_chart forces y through zero, which flattens FX prices into a
    # straight line. Altair with scale=zero=False auto-fits to the data range.
    long_df = (
        chart_df.reset_index()
        .melt("time", var_name="series", value_name="price")
        .dropna(subset=["price"])
    )
    chart = (
        alt.Chart(long_df)
        .mark_line()
        .encode(
            x=alt.X("time:T", title=None),
            y=alt.Y("price:Q", title=None, scale=alt.Scale(zero=False)),
            color=alt.Color("series:N", title=None),
        )
    )
    st.altair_chart(chart, width="stretch")


render()
