"""Vendor-agnostic price stream interface.

Anything downstream of a data source (storage, signals, dashboard) should
import `Tick` and `TickStream` from here — never vendor-specific types.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class Tick:
    instrument: str
    time: str
    bid: float
    ask: float


@runtime_checkable
class TickStream(Protocol):
    def stream(self, instruments: Iterable[str]) -> Iterator[Tick]: ...


@runtime_checkable
class HistoricalSource(Protocol):
    def fetch(
        self, instrument: str, start: str, end: str, granularity: str
    ) -> Iterator[Tick]: ...
