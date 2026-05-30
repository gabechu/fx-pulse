FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

FROM base AS app
RUN uv sync --frozen --no-install-project --no-dev
COPY fx_pulse ./fx_pulse
RUN uv sync --frozen --no-dev
CMD ["uv", "run", "python", "-m", "fx_pulse.stream"]

FROM base AS dev
RUN uv sync --frozen --no-install-project
COPY fx_pulse ./fx_pulse
COPY tests ./tests
RUN uv sync --frozen
CMD ["uv", "run", "pytest"]
