FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./

FROM base AS app
RUN uv sync --frozen --no-install-project --no-dev
# supercronic: small static cron daemon for containers. Used by the scheduler
# service. ${TARGETARCH} resolves to amd64/arm64 so the same Dockerfile builds
# across architectures (e.g. Apple Silicon dev boxes and amd64/arm64 servers).
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.29
ADD --chmod=755 https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
COPY fx_pulse ./fx_pulse
RUN uv sync --frozen --no-dev
# Run the venv python directly so it is PID 1 and receives docker stop's
# SIGTERM; `uv run` as PID 1 does not forward signals to its child.
CMD ["/app/.venv/bin/python", "-m", "fx_pulse.stream"]

FROM base AS dev
RUN uv sync --frozen --no-install-project
COPY fx_pulse ./fx_pulse
COPY tests ./tests
RUN uv sync --frozen
CMD ["uv", "run", "pytest"]
