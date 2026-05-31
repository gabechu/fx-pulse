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
  stream.py            # Step 2/3/4: ticks → terminal + Postgres + signal
  storage.py           # Step 3/4: TickStore + SignalStore
  signal.py            # Step 4: MA crossover heuristic
  dashboard.py         # Step 5: Streamlit dashboard
  db.py                # Connection + forward-only migration runner
  migrations/          # NNN_*.sql files, applied in version order
  providers/
    __init__.py        # get_provider() factory + Tick / TickStream re-exports
    base.py            # Tick dataclass + TickStream Protocol
    oanda.py           # OANDA v20 adapter
tests/                 # offline smoke tests — no network
pyproject.toml         # deps + project metadata
```

## How to run

Stream live AUD/USD ticks (Steps 2–3):

```bash
docker compose up --build
```

You should see prices as they tick like:
`2026-05-20T...Z  bid=0.66012  ask=0.66016`

Ticks are written to a Postgres 16 container managed by compose (data persists
in the `pgdata` named volume). Postgres MVCC means concurrent readers (the
dashboard, ad-hoc `psql`) never block writers.

In another terminal, confirm ticks are landing:

```bash
docker compose exec postgres psql -U fx_pulse -d fx_pulse \
    -c "SELECT COUNT(*), MAX(time) FROM ticks;"
```

Stop with `Ctrl-C` (or `docker compose down`). Postgres persists between
runs via the named volume; `docker compose down -v` wipes it.

### Dashboard (Step 5)

In another terminal, bring up the Streamlit dashboard:

```bash
docker compose up dashboard
```

Open <http://localhost:8501>. The dashboard shows the latest signal label,
recent tick count, and a chart of mid-price with the short/long MAs overlaid.
It auto-refreshes every 5 seconds.

The dashboard reads the same Postgres the streamer writes to. MVCC lets the
dashboard query freely while backfill and the live streamer are writing — no
locking, no coordination. You can start the streamer and dashboard in either
order.

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
- [x] Step 3 — Persist ticks to Postgres
- [x] Step 4 — First heuristic signal
- [x] Step 5 — Streamlit dashboard
- [ ] Step 6 — Phone access (LAN / tunnel)

## Production-readiness backlog

The roadmap above is *what the app does*. Separately, [BACKLOG.md](BACKLOG.md)
tracks *what it needs before running unattended in ECS* — reconnect/backoff,
structured logging, SIGTERM handling, healthchecks, config consolidation, etc.
Work through it as we go.
