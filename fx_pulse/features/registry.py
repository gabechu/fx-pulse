"""FeatureSpec dataclass + global FEATURES registry.

Each feature is a pure function of raw source data plus the timestamps we
want it evaluated at. Sources are identified by short names ("oanda_ticks",
"rba_decisions", "cpi_releases"); assemble() loads each requested source
once and passes it to every feature that needs it.

A feature's `compute` must be point-in-time correct: for any timestamp t in
the input, the returned value at t may only depend on rows with
`event_time <= t`. Violations are label leakage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


# A raw-data bundle: keyed by source name, value depends on the source
# (a Series of mid prices, a DataFrame of RBA decisions, etc.). Features
# read whatever keys they declared in their `source` field.
RawData = dict[str, object]
ComputeFn = Callable[[pd.DatetimeIndex, RawData], pd.Series]


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    source: str  # which key in RawData this feature reads
    lookback: pd.Timedelta  # max history needed before the earliest target timestamp
    description: str
    compute: ComputeFn


FEATURES: dict[str, FeatureSpec] = {}


def register(spec: FeatureSpec) -> None:
    if spec.name in FEATURES:
        raise ValueError(f"feature already registered: {spec.name}")
    FEATURES[spec.name] = spec
