"""Heuristic signal: short-vs-long moving-average crossover on mid-price.

First-cut signal for Step 4. Time-based windows (not count-based) so the
semantics stay honest when tick rate varies between busy sessions and the
overnight AU lull.

Emits one of LABEL_LONG / LABEL_FLAT / LABEL_SHORT once both windows are
warm. Callers seeing `None` (during warmup) should log as LABEL_WARMUP.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Optional, Tuple

from fx_pulse.providers import Tick


SHORT_WINDOW_SECONDS = 60
LONG_WINDOW_SECONDS = 300

LABEL_LONG = "long"
LABEL_SHORT = "short"
LABEL_FLAT = "flat"
LABEL_WARMUP = "warmup"  # exposed for log consumers; never set on a Signal


@dataclass(frozen=True)
class Signal:
    instrument: str
    time: str
    short_ma: float
    long_ma: float
    label: str  # one of LABEL_LONG / LABEL_FLAT / LABEL_SHORT


def _to_epoch_seconds(iso_time: str) -> float:
    # OANDA emits RFC3339 with trailing Z; fromisoformat handles Z natively
    # only from 3.11+, so normalise for 3.10 compatibility.
    return datetime.fromisoformat(iso_time.replace("Z", "+00:00")).timestamp()


class MACrossover:
    """Single-instrument crossover. Caller feeds ticks in order; we emit a
    Signal per tick once both windows have accumulated their full duration.
    """

    def __init__(
        self,
        short_seconds: int = SHORT_WINDOW_SECONDS,
        long_seconds: int = LONG_WINDOW_SECONDS,
    ) -> None:
        if short_seconds >= long_seconds:
            raise ValueError("short window must be strictly shorter than long")
        self._short_seconds = short_seconds
        self._long_seconds = long_seconds
        self._long: Deque[Tuple[float, float]] = deque()
        self._short: Deque[Tuple[float, float]] = deque()
        self._first_ts: Optional[float] = None

    def update(self, tick: Tick) -> Optional[Signal]:
        ts = _to_epoch_seconds(tick.time)
        mid = (tick.bid + tick.ask) / 2.0

        if self._first_ts is None:
            self._first_ts = ts

        self._long.append((ts, mid))
        self._short.append((ts, mid))
        self._evict(self._long, ts - self._long_seconds)
        self._evict(self._short, ts - self._short_seconds)

        # Warmup: until we've seen long_seconds of data, the "long" MA is
        # really a "however-much-we've-got" MA and the crossover is noise.
        if ts - self._first_ts < self._long_seconds:
            return None

        short_ma = sum(p for _, p in self._short) / len(self._short)
        long_ma = sum(p for _, p in self._long) / len(self._long)
        if short_ma > long_ma:
            label = LABEL_LONG
        elif short_ma < long_ma:
            label = LABEL_SHORT
        else:
            label = LABEL_FLAT
        return Signal(
            instrument=tick.instrument,
            time=tick.time,
            short_ma=short_ma,
            long_ma=long_ma,
            label=label,
        )

    @staticmethod
    def _evict(window: Deque[Tuple[float, float]], cutoff: float) -> None:
        while window and window[0][0] < cutoff:
            window.popleft()
