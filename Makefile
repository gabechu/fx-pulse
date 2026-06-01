# Common dev workflows. `make help` lists targets.

INSTRUMENT  ?= AUD_USD
FROM        ?= 2023-05-30T00:00:00Z
TO          ?= 2026-05-30T00:00:00Z
GRANULARITY ?= M1

.PHONY: help stream grafana test backfill build teardown

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

stream: ## Run the live tick streamer (rebuilds image)
	docker compose up --build app

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

build: ## Rebuild the app image without running anything
	docker compose build app

teardown: ## Stop all services and wipe the Postgres volume (clean slate)
	docker compose down -v --remove-orphans
