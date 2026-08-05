# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.12.1

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

FROM python:${PYTHON_VERSION}-slim-bookworm AS python-base

ARG APP_UID=10001
ARG APP_GID=10001

ENV PATH="/app/.venv/bin:/usr/local/bin:${PATH}" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chown app:app /app

FROM python-base AS builder

COPY --from=uv-bin /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python-base AS runtime

LABEL org.opencontainers.image.source="https://github.com/ryancswallace/economic-evidence-graph" \
      org.opencontainers.image.licenses="LicenseRef-Proprietary" \
      org.opencontainers.image.description="A versioned, auditable evidence layer for economic research and policy synthesis"

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app README.md LICENSE ./
COPY --chown=app:app config ./config

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends --yes rsync \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data/artifacts \
    && chown -R app:app /data

USER app

CMD ["python", "-c", "import economic_evidence_graph; print(economic_evidence_graph.__version__)"]

FROM builder AS test

COPY . .

# This image only runs pytest. Keep type-checking and other development tools
# out of it so their transitive runtimes do not expand the image attack surface.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --group test \
    && chown -R app:app /app

USER app

CMD ["python", "-m", "pytest", "-q"]
