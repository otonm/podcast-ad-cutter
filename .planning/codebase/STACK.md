# Technology Stack

**Analysis Date:** 2026-05-14

## Languages

**Primary:**
- Python 3.12 — entire application. Pinned via `.python-version` (value: `3.12`).

## Runtime

**Environment:**
- CPython 3.12.13 (system-installed at time of analysis)

**Package Manager:**
- uv 0.10.12
- Lockfile: `uv.lock` (present — committed)

## Frameworks

**Core:**
- None — the app is a standalone async pipeline with no web framework.

**Configuration / Validation:**
- `pydantic>=2` — schema validation for all config models (`config/config_loader.py`)
- `pydantic-settings>=2` — env-var binding for `Credentials` class (`config/config_loader.py`)

**Build/Dev:**
- `ruff>=0.15.7` — linting and style enforcement. Config in `pyproject.toml` `[tool.ruff]`.
  - `line-length = 120`, `target-version = "py312"`, `select = ["ALL"]` with explicit ignores.
- `mypy>=1.19.1` — static type checking. Config in `pyproject.toml` `[tool.mypy]`, `strict = true`.

**Testing:**
- `pytest>=9.0.2` — test runner. Config in `pyproject.toml` `[tool.pytest.ini_options]`. `testpaths = ["tests"]`, `asyncio_mode = "auto"`.
- `pytest-asyncio>=0.24` — async test support.
- `pytest-cov>=7.0.0` — coverage reporting. Coverage `omit` in `[tool.coverage.run]`.
- `aioresponses>=0.7` — mock `aiohttp` responses in tests.

## Key Dependencies

**Critical:**
- `litellm>=1.83.0` — unified LLM API client. Used for speech-to-text (`litellm.atranscription`) and chat completions (`litellm.acompletion`) across all AI pipeline stages. See `components/episode_transcriptor.py`, `components/ad_detector.py`, `components/topic_extractor.py`, `utils/llm.py`.
- `aiohttp>=3` — async HTTP client. Used for downloading podcast RSS feeds (`components/feed_downloader.py`) and episode audio files (`components/episode_downloader.py`).
- `aiosqlite>=0.22.1` — async SQLite client. All persistence lives in SQLite via this driver. See `database/connection.py` and every `database/*.py` store.

**Infrastructure:**
- `pyyaml>=6` — parses `config.yaml` at startup (`config/config_loader.py`).
- `python-dotenv>=1` — loads `.env` before credentials are read (`config/config_loader.py`: `load_dotenv()`).
- `python-slugify>=8` — generates URL-safe feed and episode slugs in `components/pipeline.py` and `utils/episode_log.py`.

## System Dependencies (external binaries)

- `ffmpeg` — audio editing: cutting ad segments, re-encoding output, chunking oversized audio for transcription. Invoked as a subprocess by `utils/ffmpeg.py`.
- `ffprobe` — audio probing: reading duration and metadata. Used by `components/audio_prober.py`.
- `supercronic` v0.2.33 (pinned) — cron scheduler inside the Docker container. Invoked from `entrypoint.sh`.
- `gosu` — privilege-drop helper in the container (`entrypoint.sh` drops from root to `app` user).

## Configuration

**Application config:**
- YAML file (default `config.yaml`; override via `--config` CLI flag).
- Template: `config.example.yaml`.
- Validated against `AppConfig` Pydantic model at startup.

**Secrets / credentials:**
- `.env` file (loaded by `python-dotenv`). Template: `.env.example`.
- Env vars bound to `Credentials` (pydantic-settings): `GROQ_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`.

**Key configurable settings:**
- `models.transcription` / `models.context_extraction` / `models.ad_detection` — per-stage LLM provider + model.
- `paths.output_dir`, `paths.cache_dir`, `paths.data_dir`, `paths.log_dir`.
- `ad_detection.min_duration`, `ad_detection.min_confidence`.
- `output.file_type`, `output.bitrate`.
- `log.*` — level, file output, rotation, per-episode logs.

**Build:**
- `pyproject.toml` is the single source of truth for metadata, dependencies, and all tool config (ruff, mypy, pytest, coverage).

## Platform Requirements

**Development:**
- Python 3.12+
- uv package manager
- `ffmpeg` and `ffprobe` in PATH
- At least one LLM provider API key in `.env`

**Production:**
- Docker container: base image `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` (Debian Bookworm slim)
- System packages in container: `ffmpeg`, `curl`, `gosu` (installed via apt in `Dockerfile`)
- `supercronic` v0.2.33 handles cron scheduling inside the container (`CRON_SCHEDULE` env var, default `0 * * * *`)
- Published image: `ghcr.io/otonm/podcast-ad-cutter:latest` (pushed by GitHub Actions on every `main` push)
- Output RSS feed served externally — no built-in HTTP server; `base_url` in `config.yaml` must point to the host serving `paths.output_dir`

---

*Stack analysis: 2026-05-14*
