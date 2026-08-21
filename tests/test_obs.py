"""Tests for the JSON log formatter's field bounding.

The formatter is the one choke point every log record passes through —
including third-party loggers like oandapyV20's, which log entire HTTP
response bodies on failure. These pin down that no field can flood the
log, whoever emitted it.
"""
from __future__ import annotations

import json
import logging

from fx_pulse.obs import _JsonFormatter, _MAX_LOG_FIELD_CHARS


def _format(msg: str, **extra) -> dict:
    record = logging.LogRecord("oandapyV20.oandapyV20", logging.ERROR, "", 0, msg, None, None)
    for key, val in extra.items():
        setattr(record, key, val)
    return json.loads(_JsonFormatter().format(record))


def test_html_body_is_elided_keeping_the_prefix():
    msg = "request https://stream.example/v3/pricing failed [502,<!DOCTYPE html>" + "x" * 24_000
    out = _format(msg)
    assert "chars of html elided" in out["msg"]
    assert out["msg"].startswith("request https://stream.example/v3/pricing failed [502,")
    assert len(out["msg"]) < _MAX_LOG_FIELD_CHARS + 100


def test_long_plain_message_is_truncated():
    out = _format("y" * 20_000)
    assert "chars truncated" in out["msg"]
    assert len(out["msg"]) < _MAX_LOG_FIELD_CHARS + 100


def test_short_message_and_extras_pass_through_unchanged():
    out = _format("OANDA stream dropped; reconnecting", attempt=2, delay_s=1.5)
    assert out["msg"] == "OANDA stream dropped; reconnecting"
    assert out["attempt"] == 2
    assert out["delay_s"] == 1.5


def test_string_extras_are_bounded_too():
    out = _format("retrying", error="V20Error(502, '<html>" + "z" * 30_000 + "')")
    assert "chars of html elided" in out["error"]
    assert len(out["error"]) < _MAX_LOG_FIELD_CHARS + 100