"""Structured logging + lightweight in-process metrics for long-running services.

Logs are emitted as single-line JSON to stdout: CloudWatch metric filters and
ad-hoc `jq` both work without further parsing. Metrics are accumulated
in-process and emitted as a periodic INFO summary line — no Prometheus or
StatsD dependency until volume justifies one.

Env vars:
    LOG_LEVEL  stdlib level name; default INFO
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fx_pulse import config

# Stdlib LogRecord has a fixed set of attributes; anything else on the record
# was added by the caller via `extra=` and should be promoted into the JSON
# payload. Snapshot the built-ins once at import time.
_BUILTIN_LOGRECORD_ATTRS = set(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
) | {"message", "asctime"}


_MAX_LOG_FIELD_CHARS = 500


def _bound(text: str) -> str:
    """Keep log fields single-line-sized regardless of who logged them.

    Vendor libraries (oandapyV20 among them) log entire HTTP response
    bodies on failure — during an OANDA outage that is a ~24KB HTML error
    page per retry. Markup is elided from the first tag so the useful
    prefix (URL, status code) survives; anything else is truncated.
    """
    lower = text.lower()
    idx = min((i for i in (lower.find("<!doctype"), lower.find("<html")) if i != -1), default=-1)
    if idx != -1:
        text = f"{text[:idx].rstrip()} [{len(text) - idx} chars of html elided]"
    if len(text) > _MAX_LOG_FIELD_CHARS:
        text = f"{text[:_MAX_LOG_FIELD_CHARS]}... [{len(text) - _MAX_LOG_FIELD_CHARS} chars truncated]"
    return text


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": _bound(record.getMessage()),
        }
        for key, val in record.__dict__.items():
            if key in _BUILTIN_LOGRECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = _bound(val) if isinstance(val, str) else val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        root = logging.getLogger()
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        root.handlers.clear()
        root.addHandler(handler)
        root.setLevel(config.log_level())
        _configured = True
    return logging.getLogger(name)


def _parse_tick_time(s: str) -> Optional[datetime]:
    try:
        s = s.rstrip("Z")
        if "." in s:
            head, _, frac = s.partition(".")
            s = f"{head}.{frac[:6]}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@dataclass
class Metrics:
    """Accumulates per-interval counters and flushes an INFO summary line.

    Designed for the streamer's hot path: `record_tick` is a few field
    increments; `maybe_flush` is called per tick and emits at most once per
    `interval_seconds`.
    """
    logger: logging.Logger
    interval_seconds: float = 60.0
    _ticks: int = 0
    _reconnects: int = 0
    _write_errors: int = 0
    _lag_ms_sum: float = 0.0
    _lag_ms_max: float = 0.0
    _lag_samples: int = 0
    _window_start: float = field(default_factory=time.monotonic)

    def record_tick(self, tick_time: str) -> None:
        self._ticks += 1
        parsed = _parse_tick_time(tick_time)
        if parsed is not None:
            lag_ms = (datetime.now(timezone.utc) - parsed).total_seconds() * 1000.0
            self._lag_ms_sum += lag_ms
            if lag_ms > self._lag_ms_max:
                self._lag_ms_max = lag_ms
            self._lag_samples += 1

    def record_reconnect(self) -> None:
        self._reconnects += 1

    def record_write_error(self) -> None:
        self._write_errors += 1

    def maybe_flush(self) -> None:
        if time.monotonic() - self._window_start >= self.interval_seconds:
            self.flush()

    def flush(self) -> None:
        now = time.monotonic()
        elapsed = now - self._window_start
        avg_lag = self._lag_ms_sum / self._lag_samples if self._lag_samples else None
        self.logger.info(
            "metrics summary",
            extra={
                "window_s": round(elapsed, 1),
                "ticks": self._ticks,
                "reconnects": self._reconnects,
                "write_errors": self._write_errors,
                "lag_ms_avg": round(avg_lag, 1) if avg_lag is not None else None,
                "lag_ms_max": round(self._lag_ms_max, 1) if self._lag_samples else None,
            },
        )
        self._ticks = 0
        self._reconnects = 0
        self._write_errors = 0
        self._lag_ms_sum = 0.0
        self._lag_ms_max = 0.0
        self._lag_samples = 0
        self._window_start = now


