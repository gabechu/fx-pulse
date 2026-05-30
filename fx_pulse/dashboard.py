"""Streamlit dashboard for AUD/USD ticks + heuristic signal.

Reads the same SQLite file the streamer writes (FX_PULSE_DB_PATH contract).
Opens read-only by enforcement (PRAGMA query_only) rather than URI mode=ro,
because SQLite WAL readers must still write the -shm sidecar — mode=ro forbids
that and refuses to open WAL databases.

Run: `docker compose up dashboard` then open http://localhost:8501
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = os.getenv("FX_PULSE_DB_PATH", "fx_pulse.db")
INSTRUMENT = "AUD_USD"
LOOKBACK_MINUTES = 60
REFRESH_INTERVAL = "5s"

_LABEL_COLOR = {"long": "#16a34a", "flat": "#737373", "short": "#dc2626"}


def _open_readonly(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _load_ticks(conn: sqlite3.Connection, since_iso: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT time, bid, ask FROM ticks WHERE instrument = ? AND time >= ? ORDER BY time",
        conn,
        params=(INSTRUMENT, since_iso),
        parse_dates=["time"],
    )


def _load_signals(conn: sqlite3.Connection, since_iso: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT time, short_ma, long_ma, label FROM signals "
        "WHERE instrument = ? AND time >= ? ORDER BY time",
        conn,
        params=(INSTRUMENT, since_iso),
        parse_dates=["time"],
    )


st.set_page_config(page_title="fx-pulse", layout="wide")
st.title(f"fx-pulse · {INSTRUMENT}")
st.caption(f"Lookback: last {LOOKBACK_MINUTES} min · refreshes every {REFRESH_INTERVAL}")


@st.fragment(run_every=REFRESH_INTERVAL)
def render() -> None:
    if not Path(DB_PATH).exists():
        st.warning(f"No DB at `{DB_PATH}` yet — waiting for the streamer to start.")
        return

    since = (datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK_MINUTES)).isoformat()
    conn = _open_readonly(DB_PATH)
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
    mid.metric(f"Ticks (last {LOOKBACK_MINUTES}m)", len(ticks))
    if ticks.empty:
        right.metric("Latest tick", "—")
    else:
        right.metric("Latest tick", ticks.iloc[-1]["time"].strftime("%H:%M:%SZ"))

    if ticks.empty:
        st.info("No ticks in the lookback window yet.")
        return

    ticks["mid"] = (ticks["bid"] + ticks["ask"]) / 2.0
    chart = ticks.set_index("time")[["mid"]]
    if not signals.empty:
        chart = chart.join(
            signals.set_index("time")[["short_ma", "long_ma"]], how="outer"
        )
    st.line_chart(chart)


render()
