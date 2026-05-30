# fx-pulse

Real-time AUD/USD signal + dashboard, backed by OANDA.

MVP scope: stream AUD/USD from OANDA → store ticks → compute a simple heuristic signal → show it on a dashboard reachable from laptop and phone.

The data layer is vendor-agnostic — see [Swapping data providers](#swapping-data-providers).

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- An OANDA account (free **practice** account is fine for development)

### Get an OANDA API token (one-time)

1. Sign up at [oanda.com](https://www.oanda.com/) and open a **practice** (demo) account.
2. In the OANDA web app: *Manage API Access* → **Generate** a personal access token. Copy it.
3. Note your **Account ID** (looks like `XXX-XXX-XXXXXXXX-XXX`). Found under *My Funds* / account details.

## Setup

```bash
uv sync
```

This creates `.venv/` and installs locked dependencies.

## Project layout

```
fx_pulse/
  __init__.py
  providers/
    __init__.py        # get_provider() factory + Tick / TickStream re-exports
    base.py            # Tick dataclass + TickStream Protocol
    oanda.py           # OANDA v20 adapter
tests/                 # offline smoke tests — no network
pyproject.toml         # deps + project metadata
```

## Swapping data providers

Downstream code (storage, signals, dashboard) only ever sees normalized
`Tick` events from the `TickStream` Protocol — never vendor payloads.

To add a new provider (e.g. IBKR, Polygon, Databento):

1. Create `fx_pulse/providers/<name>.py` with a class exposing `stream(instruments) -> Iterator[Tick]`.
2. Add a branch to `get_provider()` in [providers/__init__.py](fx_pulse/providers/__init__.py).
3. Select it at runtime: `FX_PULSE_PROVIDER=<name> uv run ...`.

No other code changes are needed.

## Roadmap (one step per commit)

- [x] Step 1 — Foundation
- [ ] Step 2 — Stream live AUD/USD ticks to terminal
- [ ] Step 3 — Persist ticks to SQLite
- [ ] Step 4 — First heuristic signal
- [ ] Step 5 — Streamlit dashboard
- [ ] Step 6 — Phone access (LAN / tunnel)
