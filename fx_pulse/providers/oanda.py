"""OANDA v20 streaming + historical adapters."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

import requests.exceptions
from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.pricing import PricingStream
from oandapyV20.exceptions import V20Error

from fx_pulse.obs import get_logger
from fx_pulse.providers.base import Tick

log = get_logger("fx_pulse.providers.oanda")

# OANDA candle granularities → seconds. W and M (month) intentionally omitted
# (irregular durations; add when a caller actually needs them).
_GRANULARITY_SECONDS = {
    "S5": 5, "S10": 10, "S15": 15, "S30": 30,
    "M1": 60, "M2": 120, "M4": 240, "M5": 300,
    "M10": 600, "M15": 900, "M30": 1800,
    "H1": 3600, "H2": 7200, "H3": 10800, "H4": 14400,
    "H6": 21600, "H8": 28800, "H12": 43200,
    "D": 86400,
}
_CANDLES_PER_PAGE = 5000  # OANDA hard cap


class OandaTickStream:
    def __init__(self, token: str, account_id: str, env: str = "practice") -> None:
        self._api = API(access_token=token, environment=env)
        self._account_id = account_id

    @classmethod
    def from_env(cls) -> "OandaTickStream":
        token = os.environ.get("OANDA_API_TOKEN")
        account_id = os.environ.get("OANDA_ACCOUNT_ID")
        if not token or not account_id:
            raise RuntimeError(
                "set OANDA_API_TOKEN and OANDA_ACCOUNT_ID "
                "(e.g. in a .env file, then run with `uv run --env-file .env ...`)"
            )
        return cls(token, account_id, os.getenv("OANDA_ENV", "practice"))

    def stream(self, instruments: Iterable[str]) -> Iterator[Tick]:
        # OANDA's pricing stream is a long-lived chunked HTTP response and is
        # documented as non-permanent — drops are expected. Reconnect forever
        # with capped exponential backoff; let non-retryable errors propagate.
        params = {"instruments": ",".join(instruments)}
        attempt = 0
        while True:
            request = PricingStream(accountID=self._account_id, params=params)
            try:
                for msg in self._api.request(request):
                    attempt = 0
                    if msg.get("type") != "PRICE":
                        continue
                    yield Tick(
                        instrument=msg["instrument"],
                        time=msg["time"],
                        bid=float(msg["bids"][0]["price"]),
                        ask=float(msg["asks"][0]["price"]),
                    )
                log.warning("OANDA stream closed cleanly; reconnecting")
            except Exception as exc:
                if not _is_retryable(exc):
                    raise
                log.warning(
                    "OANDA stream dropped; reconnecting",
                    extra={"error": repr(exc), "attempt": attempt + 1},
                )
            time.sleep(_backoff_delay(attempt, _RETRY_BASE_SECONDS, _STREAM_BACKOFF_CAP_SECONDS))
            attempt += 1


class OandaHistory:
    def __init__(self, token: str, env: str = "practice") -> None:
        self._api = API(access_token=token, environment=env)

    @classmethod
    def from_env(cls) -> "OandaHistory":
        token = os.environ.get("OANDA_API_TOKEN")
        if not token:
            raise RuntimeError(
                "set OANDA_API_TOKEN "
                "(e.g. in a .env file, then run with `uv run --env-file .env ...`)"
            )
        return cls(token, os.getenv("OANDA_ENV", "practice"))

    def fetch(
        self, instrument: str, start: str, end: str, granularity: str
    ) -> Iterator[Tick]:
        """Yield completed candle Ticks in the half-open interval [start, end).

        `start` and `end` are RFC3339 UTC timestamps (e.g. "2024-01-15T00:00:00Z"
        or with sub-second precision; nanoseconds are accepted but trimmed to
        microseconds). Missing tzinfo is treated as UTC. End is exclusive:
        candles whose time is >= end are not yielded.

        If OANDA returns an empty page mid-range (e.g. weekend gap or upstream
        outage), the iterator stops and logs a warning to stderr — the caller
        sees fewer rows than expected rather than a silent zero-row result.
        """
        if granularity not in _GRANULARITY_SECONDS:
            raise ValueError(
                f"unsupported granularity {granularity!r}; "
                f"choose one of {sorted(_GRANULARITY_SECONDS)}"
            )
        cursor = _parse_oanda_time(start)
        end_dt = _parse_oanda_time(end)
        step = timedelta(seconds=_GRANULARITY_SECONDS[granularity])

        while cursor < end_dt:
            # OANDA rejects (from + to + count) together. Use (from + count)
            # and bound the upper edge client-side.
            params = {
                "from": _format_oanda_time(cursor),
                "granularity": granularity,
                "price": "BA",
                "count": _CANDLES_PER_PAGE,
            }
            request = InstrumentsCandles(instrument=instrument, params=params)
            candles = self._request_with_retry(request).get("candles", [])
            if not candles:
                log.warning(
                    "OANDA returned no candles (weekend gap or upstream outage)",
                    extra={"from": _format_oanda_time(cursor)},
                )
                return
            last_complete: datetime | None = None
            reached_end = False
            for c in candles:
                candle_time = _parse_oanda_time(c["time"])
                if candle_time >= end_dt:
                    reached_end = True
                    break
                if not c.get("complete"):
                    continue
                last_complete = candle_time
                yield Tick(
                    instrument=instrument,
                    time=c["time"],
                    bid=float(c["bid"]["c"]),
                    ask=float(c["ask"]["c"]),
                )
            if reached_end or last_complete is None:
                return
            cursor = last_complete + step

    def _request_with_retry(self, request: Any) -> Any:
        for attempt in range(_MAX_RETRIES):
            try:
                return self._api.request(request)
            except Exception as exc:
                if attempt + 1 == _MAX_RETRIES or not _is_retryable(exc):
                    raise
                delay = _backoff_delay(attempt, _RETRY_BASE_SECONDS, _HISTORY_BACKOFF_CAP_SECONDS)
                log.warning(
                    "OANDA request failed; retrying",
                    extra={"error": repr(exc), "delay_s": round(delay, 1), "attempt": attempt + 1},
                )
                time.sleep(delay)
        raise RuntimeError("unreachable")  # pragma: no cover


_MAX_RETRIES = 3
_RETRY_BASE_SECONDS = 1.0
_HISTORY_BACKOFF_CAP_SECONDS = 30.0
_STREAM_BACKOFF_CAP_SECONDS = 30.0


def _backoff_delay(attempt: int, base: float, cap: float) -> float:
    return min(base * (2 ** attempt), cap)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
    )):
        return True
    if isinstance(exc, V20Error):
        try:
            code = int(exc.code)
        except (TypeError, ValueError):
            return False
        return code == 429 or 500 <= code < 600
    return False


def _parse_oanda_time(s: str) -> datetime:
    # OANDA returns e.g. "2024-01-15T00:00:00.000000000Z"; fromisoformat handles
    # microseconds, not nanoseconds, so trim the fraction.
    s = s.rstrip("Z")
    if "." in s:
        head, _, frac = s.partition(".")
        s = f"{head}.{frac[:6]}"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_oanda_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
