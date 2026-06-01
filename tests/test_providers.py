"""Offline smoke tests for the provider layer — no network calls."""
from __future__ import annotations

import itertools
import threading
from dataclasses import FrozenInstanceError

import pytest
import requests.exceptions

from fx_pulse.providers import TickStream, Tick, get_provider
from fx_pulse.providers import oanda as oanda_mod
from fx_pulse.providers.oanda import OandaTickStream


def _price_msg(time_: str, bid: str, ask: str) -> dict:
    return {
        "type": "PRICE",
        "instrument": "AUD_USD",
        "time": time_,
        "bids": [{"price": bid}],
        "asks": [{"price": ask}],
    }


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


def test_oanda_stream_reconnects_after_dropped_connection(monkeypatch):
    monkeypatch.setattr(oanda_mod.time, "sleep", lambda *_: None)

    def first_response():
        yield _price_msg("2026-06-01T06:17:23.849503422Z", "0.71851", "0.71859")
        raise requests.exceptions.ChunkedEncodingError("upstream dropped")

    def second_response():
        yield _price_msg("2026-06-01T06:17:24.109128845Z", "0.71850", "0.71856")

    responses = iter([first_response, second_response])

    class FakeAPI:
        def request(self, _req):
            return next(responses)()

    adapter = OandaTickStream(token="x", account_id="y")
    adapter._api = FakeAPI()

    ticks = list(itertools.islice(adapter.stream(["AUD_USD"]), 2))
    assert [t.bid for t in ticks] == [0.71851, 0.71850]


def test_oanda_stream_propagates_non_retryable_error(monkeypatch):
    monkeypatch.setattr(oanda_mod.time, "sleep", lambda *_: None)

    class FakeAPI:
        def request(self, _req):
            raise ValueError("config bug, not a network blip")

    adapter = OandaTickStream(token="x", account_id="y")
    adapter._api = FakeAPI()

    with pytest.raises(ValueError, match="config bug"):
        next(adapter.stream(["AUD_USD"]))


def test_oanda_stream_invokes_on_reconnect_per_drop(monkeypatch):
    monkeypatch.setattr(oanda_mod.time, "sleep", lambda *_: None)

    def first_response():
        yield _price_msg("2026-06-01T06:17:23.849503422Z", "0.71851", "0.71859")
        raise requests.exceptions.ChunkedEncodingError("upstream dropped")

    def second_response():
        yield _price_msg("2026-06-01T06:17:24.109128845Z", "0.71850", "0.71856")
        raise requests.exceptions.ConnectionError("dropped again")

    def third_response():
        yield _price_msg("2026-06-01T06:17:25.000000000Z", "0.71849", "0.71855")

    responses = iter([first_response, second_response, third_response])

    class FakeAPI:
        def request(self, _req):
            return next(responses)()

    adapter = OandaTickStream(token="x", account_id="y")
    adapter._api = FakeAPI()

    reconnects = 0

    def bump():
        nonlocal reconnects
        reconnects += 1

    list(itertools.islice(adapter.stream(["AUD_USD"], on_reconnect=bump), 3))
    assert reconnects == 2  # two drops between three connects


def test_oanda_stream_stops_when_event_set_during_backoff(monkeypatch):
    stop = threading.Event()

    def fake_wait(timeout):
        stop.set()
        return True  # mimic Event.wait returning True when set

    monkeypatch.setattr(stop, "wait", fake_wait)

    class FakeAPI:
        def request(self, _req):
            raise requests.exceptions.ConnectionError("flaky")

    adapter = OandaTickStream(token="x", account_id="y")
    adapter._api = FakeAPI()

    # Without the stop hook this would reconnect forever; with it we expect
    # the generator to exhaust after the first backoff.
    ticks = list(adapter.stream(["AUD_USD"], stop=stop))
    assert ticks == []
    assert stop.is_set()
