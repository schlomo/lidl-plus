# Project OCI labels (pyproject.toml). Tag locally with:
#   docker build -t ghcr.io/schlomo/lidl-plus .
FROM python:3.14-alpine AS builder

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
COPY lidlplus ./lidlplus

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen --no-editable

FROM python:3.14-alpine

# Keep in sync with pyproject.toml [project]
LABEL org.opencontainers.image.title="lidl-plus" \
    org.opencontainers.image.description="Lidl Plus API client, incremental receipt backup, and searchable local archive" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.vendor="Schlomo Schapiro"

ENV PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv

WORKDIR /data

ENV PATH="/app/.venv/bin:$PATH"

VOLUME ["/data"]

ENTRYPOINT ["lidl-plus"]
CMD ["backup", "sync", "--data-dir", "/data"]
