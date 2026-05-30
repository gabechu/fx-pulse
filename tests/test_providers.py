"""Offline smoke tests for the provider layer — no network calls."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from fx_pulse.providers import TickStream, Tick, get_provider
from fx_pulse.providers.oanda import OandaTickStream


def test_tick_is_immutable():
    t = Tick(instrument="AUD_USD", time="2026-05-21T00:00:00Z", bid=0.66012, ask=0.66016)
    with pytest.raises(FrozenInstanceError):
        t.bid = 0.7  # type: ignore[misc]


def test_oanda_adapter_satisfies_tick_stream_protocol():
    adapter = OandaTickStream(token="x", account_id="y")
    assert isinstance(adapter, TickStream)


def test_oanda_from_env_raises_when_credentials_missing(monkeypatch):
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_ACCOUNT_ID", raising=False)
    with pytest.raises(RuntimeError, match="OANDA_API_TOKEN"):
        OandaTickStream.from_env()


def test_get_provider_raises_for_unknown_name(monkeypatch):
    monkeypatch.setenv("FX_PULSE_PROVIDER", "nonexistent")
    with pytest.raises(ValueError, match="nonexistent"):
        get_provider()
