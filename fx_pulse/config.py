"""Process configuration. One module reads the environment; everything
else imports typed accessors from here.

Read-on-call (not at import) so tests can monkeypatch env vars per case
and so a missing required var fails at the use site, not at import time.

Required values raise RuntimeError if unset, with a message naming the
env var. Optional values fall back to documented defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

# The only instrument we predict on. See project memory: oil and other
# series are *inputs* (features), AUD/USD is the prediction target.
INSTRUMENT = "AUD_USD"

# Stride for online prediction (one prediction per N-minute UTC boundary).
PREDICTION_STRIDE_MIN = 1


def database_url() -> str:
    val = os.environ.get("DATABASE_URL")
    if not val:
        raise RuntimeError("DATABASE_URL is not set")
    return val


def model_dir() -> Path:
    return Path(os.environ.get("FX_PULSE_MODEL_DIR", "data/models"))


def provider_name() -> str:
    return os.getenv("FX_PULSE_PROVIDER", "oanda").lower()


def log_level() -> str:
    return os.environ.get("LOG_LEVEL", "INFO").upper()


def oanda_env() -> str:
    return os.getenv("OANDA_ENV", "practice")


def oanda_token() -> str:
    val = os.environ.get("OANDA_API_TOKEN")
    if not val:
        raise RuntimeError(
            "set OANDA_API_TOKEN "
            "(e.g. in a .env file, then run with `uv run --env-file .env ...`)"
        )
    return val


def oanda_account_id() -> str:
    val = os.environ.get("OANDA_ACCOUNT_ID")
    if not val:
        raise RuntimeError(
            "set OANDA_ACCOUNT_ID "
            "(e.g. in a .env file, then run with `uv run --env-file .env ...`)"
        )
    return val
