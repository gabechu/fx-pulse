# Common dev workflows. `make help` lists targets.

INSTRUMENT  ?= AUD_USD
FROM        ?= 2023-05-30T00:00:00Z
TO          ?= 2026-05-30T00:00:00Z
GRANULARITY ?= M1

.PHONY: help stream predict grafana test backfill train-model build teardown scheduler scheduler-logs ingest-rba fill-yesterday

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

stream: ## Run the live tick streamer (rebuilds image)
	docker compose up --build app

predict: ## Run the per-minute BUY classifier (needs a trained model in data/models)
	docker compose --profile predict up --build predict

grafana: ## Run Grafana with the provisioned AUD/USD dashboard
	docker compose up -d grafana
	@echo "Grafana up at http://localhost:3000  (anonymous viewer; admin/admin to edit)"

test: ## Run the offline test suite
	docker compose run --rm tests

backfill: ## Backfill historical candles. Override: INSTRUMENT=, FROM=, TO=, GRANULARITY=
	docker compose run --rm --build app uv run python -m fx_pulse.backfill \
		--instrument $(INSTRUMENT) \
		--from $(FROM) --to $(TO) \
		--granularity $(GRANULARITY)

train-model: ## Train the oversold/overbought classifier on tick data in Postgres
	docker compose run --rm --build -v $(CURDIR)/data:/app/data app \
		uv run python -m fx_pulse.ml.train

ingest-rba: ## Manually run the RBA cash-rate ingest (writes rba_cash_rate + job_runs)
	docker compose run --rm --build app uv run python -m fx_pulse.ingest.rba_cash_rate

fill-yesterday: ## Manually run the daily S5 backfill for yesterday's UTC window
	docker compose run --rm --build app uv run python -m fx_pulse.fill_yesterday

scheduler: ## Run the periodic-job scheduler (supercronic reading ops/crontab)
	docker compose --profile scheduler up -d --build scheduler
	@echo "Scheduler up; tail with 'make scheduler-logs'"

scheduler-logs: ## Tail the scheduler container logs
	docker compose logs -f scheduler

build: ## Rebuild the app image without running anything
	docker compose build app

teardown: ## Stop all services and wipe the Postgres volume (clean slate)
	docker compose down -v --remove-orphans
