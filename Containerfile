# Stage 1: builder — install Python deps via uv
FROM python:3.12-slim AS builder

# Copy uv binary from the official image (no apt install needed)
COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /usr/local/bin/uv

WORKDIR /app

# Copy lockfile and metadata first — layer is cached if these don't change
COPY pyproject.toml uv.lock .python-version ./

# Install dependencies into .venv (excludes dev deps, excludes the project itself)
RUN uv sync --frozen --no-dev --no-install-project

# Copy source code
COPY . .

# Stage 2: runtime — lean image with ffmpeg and the pre-built venv
FROM python:3.12-slim AS runtime

# ffmpeg is required by pydub for audio processing
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the venv and all source from the builder
COPY --from=builder /app /app

# Prepend .venv/bin to PATH so 'python' resolves to the venv interpreter
ENV PATH="/app/.venv/bin:$PATH"

# No default CMD — Quadlet's Exec= appends runtime flags (--host --config ...)
ENTRYPOINT ["python", "webui.py"]
