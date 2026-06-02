"""Price stream providers.

The rest of the app should use `get_provider()` and the re-exported
`Tick` / `TickStream` types — never import vendor modules directly.

Adding a new vendor:
    1. Create `fx_pulse/providers/<name>.py` with a class implementing `TickStream`.
    2. Add a branch to `get_provider()` below.
"""
from __future__ import annotations

from fx_pulse import config
from fx_pulse.providers.base import HistoricalSource, Tick, TickStream

__all__ = ["TickStream", "HistoricalSource", "Tick", "get_provider", "get_historical"]


def get_provider() -> TickStream:
    name = config.provider_name()
    if name == "oanda":
        from fx_pulse.providers.oanda import OandaTickStream
        return OandaTickStream.from_env()
    raise ValueError(f"unknown FX_PULSE_PROVIDER: {name!r}")


def get_historical() -> HistoricalSource:
    name = config.provider_name()
    if name == "oanda":
        from fx_pulse.providers.oanda import OandaHistory
        return OandaHistory.from_env()
    raise ValueError(f"unknown FX_PULSE_PROVIDER: {name!r}")
