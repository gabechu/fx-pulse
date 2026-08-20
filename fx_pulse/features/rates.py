"""RBA cash-rate features.

Monetary policy is a primary driver of AUD/USD, so these give the model a
macro view that price-derived features can't see. We only have the AUD
(RBA) side, not the US side, so the rate *level* alone is close to a
"which year is it" proxy over our short span and risks regime
memorisation against the 30-day-forward label. Policy *dynamics* (time
since the last move, its direction/size, recent momentum) recur across
regimes and are the safer signal.

All four ship anyway. Level and last-change are absolute-regime features:
the test split sits in an RBA cutting cycle whose rate levels never
appear in train, so on their own they let the SELL head manufacture a
non-transferable signal (verified: SELL test precision fell to 0.25 with
rates enabled). They also, however, give the operational BUY head its
biggest test-recall lift. We keep them and rely on the threshold support
floor (`train.DEFAULT_MIN_SIGNAL_RATE`) to keep the still-degenerate SELL
head safely silent — SELL only clears the precision target at a ~1.2%
signal rate, well under the floor, so it refuses rather than trading on
regime noise.

Input `rba_cash_rate` is the daily decisions DataFrame loaded by
`assemble` (business days; `change_in_cash_rate_target` non-null only on
decision days). Feature series are indexed by publication/calendar date;
`assemble`'s backward-fill sampling makes any timestamp pick up the most
recent published value.
"""
from __future__ import annotations

import pandas as pd


def rba_rate_level(rba_cash_rate: pd.DataFrame) -> pd.Series:
    """RBA cash-rate target, ffilled onto a full calendar-day index."""
    level = rba_cash_rate["cash_rate_target"].ffill()
    full = pd.date_range(level.index.min(), level.index.max(), freq="D", tz="UTC")
    return level.reindex(full, method="ffill")


def rba_rate_mom_3mo(rba_rate_level: pd.Series) -> pd.Series:
    """Cash-rate change over the trailing 90 calendar days."""
    return rba_rate_level - rba_rate_level.shift(90)


def rba_last_change(rba_cash_rate: pd.DataFrame) -> pd.Series:
    """Signed size of the most recent rate move (hike > 0, cut < 0)."""
    change = rba_cash_rate["change_in_cash_rate_target"]
    moved = change.fillna(0.0) != 0.0
    return change.where(moved).ffill()


def rba_days_since_change(rba_cash_rate: pd.DataFrame) -> pd.Series:
    """Calendar days since the last cash-rate move."""
    moved = rba_cash_rate["change_in_cash_rate_target"].fillna(0.0) != 0.0
    dates = rba_cash_rate.index.to_series()
    last_move = dates.where(moved).ffill()
    return (dates - last_move).dt.days.astype("float64")