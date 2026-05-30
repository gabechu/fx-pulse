"""Offline tests for the MA crossover signal — no network, no clock."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from fx_pulse.providers import Tick
from fx_pulse.signal import MACrossover, Signal


def _tick(second: int, mid: float, spread: float = 0.0002) -> Tick:
    half = spread / 2
    h, rem = divmod(second, 3600)
    m, s = divmod(rem, 60)
    return Tick(
        instrument="AUD_USD",
        time=f"2026-05-21T{h:02d}:{m:02d}:{s:02d}Z",
        bid=mid - half,
        ask=mid + half,
    )


def test_signal_is_immutable():
    s = Signal(instrument="AUD_USD", time="2026-05-21T00:00:00Z", short_ma=0.66, long_ma=0.66, label="flat")
    with pytest.raises(FrozenInstanceError):
        s.label = "long"  # type: ignore[misc]


def test_rejects_inverted_windows():
    with pytest.raises(ValueError, match="strictly shorter"):
        MACrossover(short_seconds=300, long_seconds=60)


def test_no_emission_during_warmup():
    crossover = MACrossover(short_seconds=2, long_seconds=10)
    # Feed 9 seconds of data — still inside the 10s warmup, must not emit.
    emissions = [crossover.update(_tick(s, 0.66)) for s in range(10)]
    assert emissions[:-1] == [None] * 9
    # The 10th tick (t=9s elapsed) is still < 10s, so still warmup.
    assert emissions[-1] is None


def test_emits_after_warmup():
    crossover = MACrossover(short_seconds=2, long_seconds=10)
    for s in range(10):
        crossover.update(_tick(s, 0.66))
    out = crossover.update(_tick(10, 0.66))
    assert out is not None
    assert out.label == "flat"
    assert out.short_ma == pytest.approx(0.66)
    assert out.long_ma == pytest.approx(0.66)


def test_rising_price_labels_long():
    # Long window holds 11 seconds of history; short window holds 3.
    # Price ramps up, so the short MA (recent) sits above the long MA.
    crossover = MACrossover(short_seconds=3, long_seconds=11)
    out = None
    for s in range(12):
        out = crossover.update(_tick(s, 0.66 + 0.0001 * s))
    assert out is not None
    assert out.label == "long"
    assert out.short_ma > out.long_ma


def test_falling_price_labels_short():
    crossover = MACrossover(short_seconds=3, long_seconds=11)
    out = None
    for s in range(12):
        out = crossover.update(_tick(s, 0.66 - 0.0001 * s))
    assert out is not None
    assert out.label == "short"
    assert out.short_ma < out.long_ma


def test_old_ticks_are_evicted_from_windows():
    # After a long quiet gap, only the most recent tick should remain in
    # either window — proves eviction is time-based, not count-based.
    crossover = MACrossover(short_seconds=2, long_seconds=5)
    for s in range(6):
        crossover.update(_tick(s, 0.66))
    out = crossover.update(_tick(3600, 0.70))  # one tick, an hour later
    assert out is not None
    assert out.short_ma == pytest.approx(0.70)
    assert out.long_ma == pytest.approx(0.70)
