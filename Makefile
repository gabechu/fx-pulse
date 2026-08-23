# Common dev workflows. `make help` lists targets.

INSTRUMENT  ?= AUD_USD
FROM        ?= 2023-05-30T00:00:00Z
TO          ?= 2026-05-30T00:00:00Z
GRANULARITY ?= M1

.PHONY: help app predict grafana test backfill backfill-predictions train-model build stop teardown backup scheduler scheduler-logs ingest-rba fill-day

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

app: grafana predict
	docker compose up --build app

predict: ## Run the per-minute BUY classifier (needs a trained model in data/models)
	docker compose --profile predict up -d --build predict

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

fill-day: ## Manually run the S5 backfill for one UTC day. Default: yesterday. Override: DATE=YYYY-MM-DD
	docker compose run --rm --build app uv run python -m fx_pulse.fill_day $(if $(DATE),--date $(DATE))

backfill-predictions: ## Backfill predictions. Requires SINCE=RFC3339; UNTIL= defaults to now
	docker compose run --rm --build -v $(CURDIR)/data:/app/data:ro app \
		uv run python -m fx_pulse.backfill_predictions \
		--from $(SINCE) $(if $(UNTIL),--to $(UNTIL))

scheduler: ## Run the periodic-job scheduler (supercronic reading ops/crontab)
	docker compose --profile scheduler up -d --build scheduler
	@echo "Scheduler up; tail with 'make scheduler-logs'"

scheduler-logs: ## Tail the scheduler container logs
	docker compose logs -f scheduler

build: ## Rebuild the app image without running anything
	docker compose build app

stop: ## Stop and remove all containers, including profiled services (keeps data volumes)
	docker compose --profile predict --profile scheduler --profile test down --remove-orphans

backup: ## Dump the database to backups/ (starts postgres if needed)
	@mkdir -p backups
	docker compose up -d postgres
	docker compose exec postgres pg_dump -U fx_pulse -Fc fx_pulse \
		> backups/fx_pulse_$$(date +%Y%m%d_%H%M%S).dump
	@ls -lh backups/ | tail -1

teardown: ## Stop all services and wipe the database (clean slate)
	docker compose --profile predict --profile scheduler --profile test down -v --remove-orphans
	rm -rf data/pgdata
