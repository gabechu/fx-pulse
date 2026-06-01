# Production-readiness backlog

Tracked separately from the feature [Roadmap](README.md#roadmap-one-step-per-commit) in the README. The roadmap is *what the app does*; this list is *what it needs before running unattended in ECS*.

Tick off as we go. Ordering inside each section is rough priority.

## Reliability & fault tolerance

- [ ] **Reconnect loop with backoff in the OANDA stream.** Today any exception from `self._api.request(request)` in [fx_pulse/providers/oanda.py:32](fx_pulse/providers/oanda.py#L32) kills the process and relies on `restart: unless-stopped` to recover — no backoff, no jitter, no distinction between fatal (401, bad account) and transient (5xx, network). Bad creds = restart thrash.
- [ ] **Heartbeat / staleness watchdog.** OANDA emits `HEARTBEAT` ~every 5s; we drop them at [oanda.py:33](fx_pulse/providers/oanda.py#L33) without recording. A silently-dead TCP socket looks identical to "market is quiet." Force-reconnect after N seconds of silence.
- [ ] **SIGTERM handler in `stream.main`.** [stream.py:32](fx_pulse/stream.py#L32) only catches `KeyboardInterrupt`. ECS sends `SIGTERM` then `SIGKILL`. WAL + `synchronous=NORMAL` survives crash, but we lose the clean-shutdown log event and any future in-flight buffering.
- [ ] **Tighten dedup-key contract.** [storage.py:29](fx_pulse/storage.py#L29) hashes `instrument|time` only. If OANDA ever emits two `PRICE` events at the same microsecond, the second is silently dropped. Document the assumption (vendor timestamps unique per instrument) or extend the key.
- [ ] **README crash-safety wording.** "Most you'll lose on abrupt shutdown is the in-flight tick" is true for process crash; power loss with `synchronous=NORMAL` can lose the last group of commits since the last WAL checkpoint. Minor.
- [ ] **Fill tick gaps from local-streamer downtime.** Laptop isn't up 24/7, so `ticks` has windows missing during market hours. Find them with a `LAG(time) OVER (ORDER BY time)` query against `ticks` (excluding the Fri 22:00 → Sun 22:00 UTC weekend close), then re-run [fx_pulse/backfill.py](fx_pulse/backfill.py) for each gap window — `ON CONFLICT (tick_id) DO NOTHING` makes it idempotent. Treat live and candle-filled rows as one pool downstream; the `source` column stays as forensic provenance only, not a logical divide. Worth scripting as a one-shot `python -m fx_pulse.fill_gaps` once the manual flow has been used a couple of times.

## Observability

- [x] **Replace `print()` with structured logging.** JSON-to-stdout in [fx_pulse/obs.py](fx_pulse/obs.py); used by `stream`, `backfill`, and the OANDA provider.
- [x] **Periodic metrics summary.** `Metrics` in [obs.py](fx_pulse/obs.py) accumulates ticks / reconnects / write errors / lag (avg+max ms) and flushes an INFO line every 60s. The `record_reconnect()` hook is wired ahead of the reconnect/backoff work.
- [x] **Liveness signal for ECS.** `touch_liveness()` in [obs.py](fx_pulse/obs.py); opt-in via `LIVENESS_FILE` env var, called after each successful tick write in [stream.py](fx_pulse/stream.py).

## Scalability

- [ ] **Batched writes when volume justifies it.** Currently one INSERT per tick in autocommit mode ([storage.py:42,48-53](fx_pulse/storage.py#L42)). Fine for AUD/USD; revisit when adding more instruments or backfills.
- [ ] **Store `time` as epoch micros alongside ISO string.** TEXT sorts correctly (ISO-8601-Z) but pandas/dashboard queries will re-parse repeatedly. Add an INTEGER column when the dashboard lands.
- [ ] **Slim the streamer image.** `pandas` and `streamlit` are in app deps ([pyproject.toml:9-11](pyproject.toml#L9-L11)) but unused until Steps 4–5. Move them to a `dashboard` extra or a separate Docker stage.

## Maintainability

- [ ] **Centralise config.** Env reads are scattered: `OANDA_*` in [oanda.py:20-27](fx_pulse/providers/oanda.py#L20-L27), `FX_PULSE_PROVIDER` in [providers/__init__.py:20](fx_pulse/providers/__init__.py#L20), `DATABASE_URL` in [stream.py](fx_pulse/stream.py) / [backfill.py](fx_pulse/backfill.py) / [dashboard.py](fx_pulse/dashboard.py). A small frozen `Settings` dataclass loaded once at startup makes "what env vars does this app need" a one-place answer — important for the planned SSM Parameter Store wiring.
- [ ] **Ruff + mypy (or pyright) in CI.** Cheap insurance; the codebase already has the annotations to make a type checker useful immediately.
- [ ] **Non-root `USER` in the Dockerfile.** ECS Fargate prefers non-root and it's free.
- [ ] **Tests for the stream loop.** Nothing exercises reconnect/backoff/SIGTERM. When adding those, factor the loop to take an injectable provider + a stop signal so a fake provider can raise mid-stream in tests.
