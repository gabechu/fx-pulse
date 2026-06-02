"""Feature catalog, point-in-time assembly, and label generation.

The registry (`fx_pulse.features.registry.FEATURES`) is the single source
of truth: every feature is a `FeatureSpec` declaring its source, lookback,
and a `compute` function that produces a Series at the requested timestamps.

`assemble.assemble(timestamps, [names])` runs the registry against raw
sources loaded from Postgres and returns a wide DataFrame. The exact same
function is used by training (over many historical timestamps) and online
serving (over one current timestamp). Train/serve parity is by reuse.

Labels are training-only (you can't compute them at inference without the
future) and live in `fx_pulse.ml.label`, not here.

Importing the submodules below has the side-effect of registering features.
"""
from fx_pulse.features import price  # noqa: F401 — side-effect import
from fx_pulse.features.registry import FEATURES, FeatureSpec

__all__ = ["FEATURES", "FeatureSpec"]
