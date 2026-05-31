"""Per-test Postgres isolation via private schemas.

Each test gets a fresh schema on the shared Postgres server (the `postgres`
service in docker-compose). The DSN exposed via `pg_dsn` pins the connection's
`search_path` to that schema, so the production code paths see a clean
namespace without knowing they're in a test.

Schemas are dropped on teardown. Choosing schema-per-test over database-per-test
keeps CREATE/DROP cheap (no template clone, no fsync of a new cluster file).
"""
from __future__ import annotations

import os
import uuid
from typing import Iterator
from urllib.parse import quote, urlparse, urlunparse

import psycopg
import pytest


def _dsn_with_search_path(base_dsn: str, schema: str) -> str:
    """Return a libpq URI that pins every new connection's search_path to `schema`.

    libpq URIs use percent-encoding (RFC 3986). urlencode would emit `+` for
    space, which libpq passes through verbatim and rejects as a malformed
    setting name. quote(..., safe="") gives us %20.
    """
    parts = urlparse(base_dsn)
    options = quote(f"-c search_path={schema}", safe="")
    new_query = f"options={options}"
    if parts.query:
        new_query = f"{parts.query}&{new_query}"
    return urlunparse(parts._replace(query=new_query))


@pytest.fixture
def pg_dsn() -> Iterator[str]:
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL not set; integration tests need a Postgres")
    schema = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield _dsn_with_search_path(base, schema)
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
