# fx-pulse

Real-time AUD/USD signal + dashboard, backed by Interactive Brokers (IBKR).

MVP scope: stream AUD/USD from IBKR → store ticks → compute a simple heuristic signal → show it on a dashboard reachable from laptop and phone.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- IBKR account
- IB Gateway (lightweight) or TWS (full platform) — installed and running
- **Recommended for development:** an IBKR paper-trading account, so you can stream market data without any risk of accidental orders against your live account.

### Enable paper trading (one-time)

1. Log in to [Client Portal](https://www.interactivebrokers.com/sso/Login).
2. *Settings → Account Settings → Paper Trading Account* → request one. Approval is usually same-day.
3. Once approved, you get a separate username; log in to IB Gateway/TWS with the paper credentials when developing.

## Setup

```bash
uv sync
```

This creates `.venv/` and installs locked dependencies.

## Project layout

```
fx_pulse/        # python package (will grow each step)
pyproject.toml   # deps + project metadata
```

## Roadmap (one step per commit)

- [x] Step 1 — Foundation
- [ ] Step 2 — Stream live AUD/USD ticks to terminal
- [ ] Step 3 — Persist ticks to SQLite
- [ ] Step 4 — First heuristic signal
- [ ] Step 5 — Streamlit dashboard
- [ ] Step 6 — Phone access (LAN / tunnel)
