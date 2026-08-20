"""Feature catalog and point-in-time assembly, built on a Hamilton DAG.

Feature modules (`price.py`, `rates.py`) hold plain functions: the
function name is the feature name, parameters name the source inputs (or
other features), and `@parameterize` fans one function out across
windows. `assemble.assemble(timestamps, conn, [names])` loads the needed
sources from Postgres, runs the DAG, and samples every feature at the
requested timestamps — the same call for training and online serving, so
train/serve parity holds by construction.

`FEATURES` maps feature name → description for every feature in the DAG.

Labels are training-only (you can't compute them at inference without
the future) and live in `fx_pulse.ml.label`, not here.

How to add a feature on an existing source: add a function (or a window
entry in a `@parameterize` dict) in `price.py` / `rates.py` — nothing to
register. To add a new source: add a loader + `_SOURCES` row in
`assemble.py`, then write functions taking a parameter named after it.
"""
from fx_pulse.features.assemble import FEATURES, assemble

__all__ = ["FEATURES", "assemble"]