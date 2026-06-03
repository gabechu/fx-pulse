"""External-data ingestion jobs (RBA cash rate, CPI, etc.).

Each module here is an independently runnable periodic job:

    uv run python -m fx_pulse.ingest.<source>

Membership rule: this package is for ingesting *external* time series
into their own tables. The tick pipeline (live stream + OANDA backfill)
lives outside — those modules share the `ticks` table and the
`providers` abstraction with each other but not with this family.
"""
