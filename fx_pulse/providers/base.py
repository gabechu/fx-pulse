"""Vendor-agnostic price stream interface.

Anything downstream of a data source (storage, signals, dashboard) should
import `Tick` and `TickStream` from here — never vendor-specific types.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Tick:
    instrument: str
    time: str
    bid: float
    ask: float


@runtime_checkable
class TickStream(Protocol):
    def stream(
        self,
        instruments: Iterable[str],
        *,
        on_reconnect: Optional[Callable[[], None]] = None,
        stop: Optional[threading.Event] = None,
    ) -> Iterator[Tick]: ...


@runtime_checkable
class HistoricalSource(Protocol):
    def fetch(
        self, instrument: str, start: str, end: str, granularity: str
    ) -> Iterator[Tick]: ...
