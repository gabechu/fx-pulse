"""Feature catalog and point-in-time assembly.

The registry (`fx_pulse.features.registry.FEATURES`) is the single source
of truth: every feature is a `FeatureSpec` declaring its source name, the
lookback window it needs, and a `compute` function that returns a Series
at the requested timestamps.

`assemble.assemble(timestamps, conn, [names])` runs the registry against
raw sources loaded from Postgres and returns a wide DataFrame. The same
function is used by training (over many historical timestamps) and online
serving (one current timestamp). Train/serve parity is by construction.

Labels are training-only (you can't compute them at inference without
the future) and live in `fx_pulse.ml.label`, not here.

How the data flows:
    timestamps + conn
        → assemble() loads each needed source once (e.g. `oanda_ticks`
          from `ticks` table) into a `RawData` dict
        → each FeatureSpec.compute(timestamps, raw) returns one Series
        → wide DataFrame indexed by timestamps, one column per feature

How to add a feature on an existing source:
    1. Open the source's module (e.g. `price.py` for `oanda_ticks`).
    2. Write a compute function: `(timestamps, raw) -> Series`. It must
       only read past data — see `registry.py` for the point-in-time rule.
    3. `register(FeatureSpec(name=..., source=..., lookback=..., ...))`.
    4. Done — `assemble()` picks it up automatically.

How to add a feature on a new source (e.g. RBA decisions, CPI prints):
    1. Add a Postgres migration in `fx_pulse/migrations/` for the new table.
    2. In `assemble.py`, write a `_load_<source>(conn, start, end)` loader
       returning whatever shape your features expect (Series / DataFrame).
    3. Register the loader in `_SOURCE_LOADERS` with a short source name.
    4. Create a new feature module (e.g. `rates.py`) and import it from
       this `__init__.py` so its features auto-register on import.

Importing each feature submodule has the side effect of registering its
features. Add a `from fx_pulse.features import <new_module>` line below
when you add one.
"""
from fx_pulse.features import price  # noqa: F401 — side-effect import
from fx_pulse.features.registry import FEATURES, FeatureSpec

__all__ = ["FEATURES", "FeatureSpec"]
