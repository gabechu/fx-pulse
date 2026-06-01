# fx-pulse

Real-time AUD/USD signal + dashboard, backed by OANDA.

Stream AUD/USD → store ticks in Postgres → compute a heuristic signal → show
it on a dashboard. The data layer is vendor-agnostic — see [Swapping data
providers](#swapping-data-providers).

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose v2
- An OANDA account (free **practice** account is fine for development)

### Get an OANDA API token (one-time)

1. Sign up at [oanda.com](https://www.oanda.com/) and open a **practice** account.
2. In the OANDA web app: *Manage API Access* → **Generate** a token.
3. Note your **Account ID** (looks like `XXX-XXX-XXXXXXXX-XXX`, under *My Funds*).

## Setup

Create a `.env` file in the repo root (gitignored, never copied into the image):

```
OANDA_API_TOKEN=your-token-here
OANDA_ACCOUNT_ID=XXX-XXX-XXXXXXXX-XXX
OANDA_ENV=practice
```

Use `OANDA_ENV=live` only when connecting to a funded live account.

## How to run

Stream live AUD/USD ticks:

```bash
docker compose up --build
```

Confirm ticks are landing (in another terminal):

```bash
docker compose exec postgres psql -U fx_pulse -d fx_pulse \
    -c "SELECT COUNT(*), MAX(time) FROM ticks;"
```

Stop with `Ctrl-C` or `docker compose down`. Data persists in the `pgdata`
volume; `docker compose down -v` wipes it.

### Dashboard

```bash
make grafana
```

Open <http://localhost:3000> (anonymous viewer; `admin` / `admin` to edit).
Provisioned dashboard `fx-pulse · AUD/USD` shows the latest signal label,
recent tick count, source breakdown, and a bid/ask price chart.
Auto-refreshes every 5s; use the time-range picker top-right for
1h / 6h / 1d / 1w / 30d windows.

## Observability

JSON logs to stdout. The streamer emits a `metrics summary` line every 60s
(ticks, reconnects, write errors, lag avg/max ms).

- `LOG_LEVEL` — stdlib level name; default `INFO`.
- `LIVENESS_FILE` — opt-in path the streamer touches after each successful
  write. For an ECS healthcheck, point at e.g. `/tmp/fx_pulse_alive` and
  check the mtime is recent.

## How to test

```bash
docker compose run --rm tests
```

Offline suite — no network, no vendor calls.

## Swapping data providers

Downstream code only sees normalized `Tick` events from the `TickStream`
Protocol — never vendor payloads. To add a provider (IBKR, Polygon, Databento…):

1. Create `fx_pulse/providers/<name>.py` exposing `stream(instruments) -> Iterator[Tick]`.
2. Add a branch to `get_provider()` in [providers/__init__.py](fx_pulse/providers/__init__.py).
3. Select at runtime: `FX_PULSE_PROVIDER=<name>`.

## Roadmap (one step per commit)

- [x] Step 1 — Foundation
- [x] Step 2 — Stream live AUD/USD ticks to terminal
- [x] Step 3 — Persist ticks to Postgres
- [x] Step 4 — First heuristic signal
- [x] Step 5 — Grafana dashboard
- [ ] Step 6 — Phone access (LAN / tunnel)

Production-readiness work (reconnect/backoff, SIGTERM, config consolidation,
etc.) is tracked separately in [BACKLOG.md](BACKLOG.md).
