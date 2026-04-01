# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        gosu \
    && rm -rf /var/lib/apt/lists/*

# supercronic — pin version for reproducible builds
# Check https://github.com/aptible/supercronic/releases for updates
ARG SUPERCRONIC_VERSION=0.2.33
RUN curl -fsSL \
    "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    -o /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid app --shell /bin/sh --create-home app

WORKDIR /app

# Copy lockfiles first — uv sync layer is a cache-hit on code-only changes
COPY --chown=app:app pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Put the venv on PATH so 'python' resolves without 'uv run'
ENV PATH="/app/.venv/bin:$PATH"

COPY --chown=app:app . .
COPY --chown=app:app entrypoint.sh run.sh /app/
RUN chmod +x /app/entrypoint.sh /app/run.sh

# Create volume mount points with correct ownership
RUN mkdir -p /output /data /logs /cache /config \
    && chown -R app:app /output /data /logs /cache /config

ENTRYPOINT ["/app/entrypoint.sh"]
