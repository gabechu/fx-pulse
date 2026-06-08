"""RBA cash-rate features (source: `rba_cash_rate`).

Monetary policy is a primary driver of AUD/USD, so these give the model a
macro view that price-derived features can't see. We only have the AUD
(RBA) side, not the US side, so the rate *level* alone is close to a "which
year is it" proxy over our short span and risks regime memorisation against
the 30-day-forward label. The features below lead with policy *dynamics*
(time since the last move, the last move's direction/size, recent momentum),
which recur across regimes; the raw level is included but is the first
candidate to drop if it doesn't earn its place on the test split.

All values are point-in-time correct: each ffills from the most recent
published daily row (`date <= t`).
"""
from __future__ import annotations

import pandas as pd

from fx_pulse.features.registry import FeatureSpec, RawData, register
from fx_pulse.features.util import sample_at

SOURCE = "rba_cash_rate"
_LOOKBACK = pd.Timedelta(days=800)  # cover long policy holds + 90d momentum window


def _daily(raw: RawData) -> pd.DataFrame:
    """Daily frame with a calendar-day index (ffilled), for consistent diffs."""
    df = raw[SOURCE]
    level = df["cash_rate_target"].ffill()
    full = pd.date_range(level.index.min(), level.index.max(), freq="D", tz="UTC")
    return pd.DataFrame(
        {"level": level.reindex(full, method="ffill"), "change": df["change_in_cash_rate_target"]},
        index=full,
    )


def _level(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    return sample_at(_daily(raw)["level"], timestamps)


def _momentum_3mo(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    level = _daily(raw)["level"]
    return sample_at(level - level.shift(90), timestamps)


def _last_change(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    """Signed size of the most recent rate move (hike > 0, cut < 0)."""
    change = raw[SOURCE]["change_in_cash_rate_target"]
    moved = change.fillna(0.0) != 0.0
    return sample_at(change.where(moved).ffill(), timestamps)


def _days_since_change(timestamps: pd.DatetimeIndex, raw: RawData) -> pd.Series:
    df = raw[SOURCE]
    moved = df["change_in_cash_rate_target"].fillna(0.0) != 0.0
    dates = df.index.to_series()
    last_move = dates.where(moved).ffill()
    days = (dates - last_move).dt.days.astype("float64")
    return sample_at(days, timestamps)


_ALL = {
    "rba_rate_level": (_level, "RBA cash-rate target, ffilled."),
    "rba_rate_mom_3mo": (_momentum_3mo, "Cash-rate change over the trailing 90 calendar days."),
    "rba_last_change": (_last_change, "Signed size of the most recent cash-rate move."),
    "rba_days_since_change": (_days_since_change, "Calendar days since the last cash-rate move."),
}
# All four ship. Level and last-change are absolute-regime features: the test
# split sits in an RBA cutting cycle whose rate levels never appear in train,
# so on their own they let the SELL head manufacture a non-transferable signal
# (verified: SELL test precision fell to 0.25 with rates enabled). They also,
# however, give the operational BUY head its biggest test-recall lift. We keep
# them and rely on the threshold support floor (`train.DEFAULT_MIN_SIGNAL_RATE`)
# to keep the still-degenerate SELL head safely silent — SELL only clears the
# precision target at a ~1.2% signal rate, well under the floor, so it refuses
# rather than trading on regime noise.
_FEATURES = dict(_ALL)
for _name, (_fn, _desc) in _FEATURES.items():
    register(FeatureSpec(
        name=_name,
        source=SOURCE,
        lookback=_LOOKBACK,
        description=_desc,
        compute=_fn,
    ))
