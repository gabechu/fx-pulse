"""Price stream providers.

The rest of the app should use `get_provider()` and the re-exported
`Tick` / `TickStream` types — never import vendor modules directly.

Adding a new vendor:
    1. Create `fx_pulse/providers/<name>.py` with a class implementing `TickStream`.
    2. Add a branch to `get_provider()` below.
"""
from __future__ import annotations

import os

from fx_pulse.providers.base import TickStream, Tick

__all__ = ["TickStream", "Tick", "get_provider"]


def get_provider() -> TickStream:
    name = os.getenv("FX_PULSE_PROVIDER", "oanda").lower()
    if name == "oanda":
        from fx_pulse.providers.oanda import OandaTickStream
        return OandaTickStream.from_env()
    raise ValueError(f"unknown FX_PULSE_PROVIDER: {name!r}")
