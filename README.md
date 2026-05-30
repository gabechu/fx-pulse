# fx-pulse

Real-time AUD/USD signal + dashboard, backed by OANDA.

MVP scope: stream AUD/USD from OANDA → store ticks → compute a simple heuristic signal → show it on a dashboard reachable from laptop and phone.

The data layer is vendor-agnostic — see [Swapping data providers](#swapping-data-providers).

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2
- An OANDA account (free **practice** account is fine for development)

Everything runs in Docker — no host Python or `uv` install needed.

### Get an OANDA API token (one-time)

1. Sign up at [oanda.com](https://www.oanda.com/) and open a **practice** (demo) account.
2. In the OANDA web app: *Manage API Access* → **Generate** a personal access token. Copy it.
3. Note your **Account ID** (looks like `XXX-XXX-XXXXXXXX-XXX`). Found under *My Funds* / account details.

## Setup

Create a `.env` file in the repo root (already gitignored, never copied into the image):

```
OANDA_API_TOKEN=your-token-here
OANDA_ACCOUNT_ID=XXX-XXX-XXXXXXXX-XXX
OANDA_ENV=practice
```

Use `OANDA_ENV=live` only when connecting to a funded live account.

Credentials are kept out of the image:
- `.env` is in [.dockerignore](.dockerignore) so it isn't part of the build context.
- The `Dockerfile` never `COPY`s `.env` and never sets `ENV OANDA_*`.
- `docker compose` injects them into the running container via `env_file`, so the token only exists in process environment, not in any image layer.

## Project layout

```
fx_pulse/
  __init__.py
  stream.py            # Step 2: live AUD/USD ticks → terminal
  providers/
    __init__.py        # get_provider() factory + Tick / TickStream re-exports
    base.py            # Tick dataclass + TickStream Protocol
    oanda.py           # OANDA v20 adapter
tests/                 # offline smoke tests — no network
pyproject.toml         # deps + project metadata
```

## How to run

Stream live AUD/USD ticks:

```bash
docker compose up --build
```

You should see prices as they tick like:
`2026-05-20T...Z  bid=0.66012  ask=0.66016`

Stop with `Ctrl-C` (or `docker compose down`).

## How to test

```bash
docker compose run --rm tests
```

This builds the `dev` stage of the [Dockerfile](Dockerfile) (which includes
`pytest` and the `tests/` directory) and runs the suite in a throwaway
container. Tests in `tests/` are offline — they exercise the provider layer
(Tick, Protocol conformance, env-var validation, factory error paths) without
contacting any vendor.

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
- [x] Step 2 — Stream live AUD/USD ticks to terminal
- [ ] Step 3 — Persist ticks to SQLite
- [ ] Step 4 — First heuristic signal
- [ ] Step 5 — Streamlit dashboard
- [ ] Step 6 — Phone access (LAN / tunnel)
