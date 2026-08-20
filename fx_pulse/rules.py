"""Rule-based decision heads.

Rules are explicit, dashboard-inspectable trading conditions evaluated on
the same assembled feature row the ML head sees. Each head returns a bool
and has a provenance name that is stored on the prediction row, so every
live decision is attributable to the head that made it.

The drop rule buys week-scale dips: walk-forward it beat the base rate in
all four folds (2025-02 → 2026-07, +9 to +21 pts precision), the only
evaluated rule to do so in every regime, and it fired at the 2026-07-29
trough. The ML head is anti-correlated on dips (it scores them low), so
rules and model run as independent heads rather than gating each other.
"""
from __future__ import annotations

import pandas as pd

SOURCE_DROP_RULE = "drop_rule"

DROP_RULE_FEATURE = "log_return_1w"
DROP_RULE_THRESHOLD = -0.01


def drop_rule_buy(features: pd.Series) -> bool:
    """BUY when the 1-week log return is below the drop threshold. NaN → no signal."""
    value = features.get(DROP_RULE_FEATURE)
    return value is not None and pd.notna(value) and value < DROP_RULE_THRESHOLD