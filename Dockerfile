# syntax=docker/dockerfile:1.7
#
# Data Copilot API image.
#
#   docker build --target api       -t data-copilot-api .
#   docker build --target pipelines -t data-copilot-pipelines .   # + Dagster/dbt for scripts/refresh.py
#
# Dependencies are installed from uv.lock by uv, the same source
# scripts/install_deps.sh uses, so the image cannot drift from the local venv.

FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-slim-bookworm AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app

# Cache seam: this layer only reruns when the manifest or the lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM builder AS builder-pipelines
RUN uv sync --locked --no-dev --no-install-project --extra pipelines

FROM python:3.12-slim-bookworm AS api

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

# uid/gid 1000: the container never runs as root, and bind mounts keep working
# on a Linux host whose user is also 1000.
RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --chown=app:app src ./src
COPY --chown=app:app configs ./configs
COPY --chown=app:app examples ./examples
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app dbt ./dbt
COPY --chown=app:app pyproject.toml uv.lock requirements.lock.txt requirements.pipelines.lock.txt ./

# Writable state. Sessions and metric definitions live here unless DATABASE_URL
# points at a server; uploads are temporary by design. Both are mounts in
# docker-compose.yml, so they survive a container recreate.
RUN mkdir -p /app/data/episodic_memory /app/data/semantic /app/tmp/uploads /app/.dagster \
    && chown -R app:app /app/data /app/tmp /app/.dagster

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8000'), timeout=4).read()"

CMD ["sh", "-c", "exec uvicorn src.backend.server:app --host 0.0.0.0 --port ${PORT:-8000}"]

# Warehouse image: adds the pipeline extras and the refresh entry point.
# Runs the same API by default; override the command to run scripts/refresh.py.
FROM api AS pipelines

COPY --from=builder-pipelines /app/.venv /app/.venv
COPY --chown=app:app pipelines ./pipelines

ENV DAGSTER_HOME=/app/.dagster
