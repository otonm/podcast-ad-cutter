# CLAUDE.md — Podcast Ad Cutter

## Main instruction for all agents

To generate plans or proposals, to generate code that accesses APIs, or in other cases when a resource is accessed or used, ALWAYS check the documentation using provided MCP servers (context7) or search online for information. As a last resort, inspect the source code of the library in the virtual environment itself.

Never edit and change the contents of `CLAUDE.md` without discussing the change with the user.

---

## Project Overview

This project downloads the latest episode of a podcast, transcribes it, uses an LLM to identify advertisements, and exports a clean audio file with all ads removed.

```
RSS Feed → Download Episode → Preprocess Audio → Transcribe → Extract Topic → Detect Ads → Cut Audio → Export
```

---

## Project Structure

### Main Folder

Only main execution files (`main.py`, `webui.py`) and configuration files (`config.example.yaml`, `Containerfile`, `.env.example`, `pyproject.toml`, etc.) are present in the root of the folder. Other files are in subfolders.

### `pyproject.toml`

All project metadata, dependencies, and tool configuration live here. No `requirements.txt`, `setup.cfg`, `.flake8`, or `mypy.ini`.

---

## Setup

This project uses [uv](https://docs.astral.sh/uv/). Never use `pip` directly. `uv` should be installed on the system.

```bash
# Pin the project to Python 3.12 (writes .python-version — commit this file)
uv python pin 3.12

# Create virtualenv and install all dependencies from the lockfile
uv sync

# Run the app
uv run python main.py

# Run tests
uv run pytest

# Add a runtime dependency
uv add some-package

# Add a dev-only dependency
uv add --group dev some-package

# Regenerate the lockfile after manually editing pyproject.toml
uv lock
```

`ffmpeg` is a also a system dependency and is expected to be installed.

---

## Code Checking

After every new code change run `ruff check` and `mypy` and correct any reported error or warning.

## Configuration

Non-secret settings live in `config.yaml`. Secrets (API keys only) live in `.env`. Never put API keys in `config.yaml`.  

## LLM Client (`pipeline/llm_client.py`)

`llm_client.py` is the **only** module that imports `litellm`. All LLM calls go through `complete()` or `transcribe()`. This gives tests a single mock point and keeps provider logic out of business code.

### Supported providers

The only supported providers are `openai`, `groq` and `openrouter`.

### Provider routing

litellm routes to the correct backend based on the model string prefix. The corresponding env var must be set.

| Provider value | litellm prefix used | Notes                                                                   |
| -------------- | ------------------- | ----------------------------------------------------------------------- |
| `groq`         | `groq/`             | Requires `GROQ_API_KEY`                                                 |
| `openai`       | `openai/`           | Requires `OPENAI_API_KEY`; also used for Whisper                        |
| `openrouter`   | `openrouter/`       | Requires `OPENROUTER_API_KEY`; model can be any OpenRouter model string |

### Implementation

**JSON output:** `response_format={"type": "json_object"}` is not supported by all providers (Ollama, some Bedrock variants). Always instruct the model to return JSON via the system prompt *and* pass `response_format` when available. Log malformed responses at `WARNING`, skip the chunk, and continue — never raise.

---

## Pipeline Design

Each pipeline stage follows the same pattern: log `INFO` at entry and completion, log `DEBUG` for internal details, log `WARNING` for recoverable issues, and raise on unrecoverable errors.

### Audio preprocessing (`audio_preprocessor.py`)

Runs between Download and Transcription to reduce upload size and improve Whisper accuracy.

1. Convert the downloaded audio to mono 16 kHz 32 kbps MP3 via `pydub`.
2. Write the result into the `/tmp` folder.

The CBR 32 kbps mono format is sufficient for speech recognition and cuts upload size by ~80% vs a typical podcast file.

### Transcription (`transcriber.py`)

1. Check the `transcripts` table for a row matching `episode.guid`. If found return immediately.
2. Call `audio_preprocessor.prepare_for_transcription()` to produce a mono input file.
3. Call `llm_client.transcribe()`. The `verbose_json` response includes word-level timestamps required for precise cuts.
4. Delete the preprocessed file.
5. Persist the `Transcript` and all `Segment` rows to the database.

### Topic extraction (`topic_extractor.py`)

1. Send the first `cfg.interpretation.topic_excerpt_words` words of the transcript to `llm_client.complete()`.
2. The system prompt instructs the LLM to return `{"domain": str, "topic": str, "hosts": list[str], "notes": str}`.
3. The context is injected into every ad detection prompt to help ad detection.

### Ad detection (`ad_detector.py`)

1. If the chosen LLM models' context windows is large enough, the whole transcript is sent, otherwise it gets split into chunks.
2. The LLM identifies spans where the host promotes a sponsor. Key signals:
   - Promo codes or discount URLs
   - Content unrelated to the episode topic in a promotional register
   - Openers: "This episode is brought to you by…", "Our sponsor today is…"
   - Closers: "…link in the description", "Use code [X]", "Back to the show"
3. Required output for each schunk: `[{"start_sec": float, "end_sec": float, "confidence": float, "reason": str, "sponsor": str | null}]`
4. Persist all detected segments. Cut only segments where `confidence >= min_confidence`.

### Audio editing (`audio_editor.py`)

1. Compute keep-segments as the inverse of ad segments and concatenate them.
2. Convert the original file while cutting out segments that are marked as 
3. Export at audio as CBR to prevent timestamp drift.
4. Output the file into the output folder.

---

## Database

All structured data lives in SQLite at `paths.database`.

### Repository pattern

All SQL lives in `db/repositories/`. SQL anywhere else in the codebase is a bug.

### `db/connection.py`

- Opens the database file, creating parent directories if needed.
- Sets `PRAGMA foreign_keys = ON` and `PRAGMA journal_mode = WAL` on every new connection — both are per-session and do not persist.
- Applies pending migrations from `db/migrations/` via a `_migrations` tracking table. New schema changes go in a new numbered `.sql` file. Never edit an applied migration.

---

## Data Models

All models are frozen Pydantic `BaseModel` subclasses — immutable, validated at construction, and JSON-serialisable. Never use `@dataclass` for models that cross module boundaries or touch the database.

---

## LLM Prompts

The prompts for topic extraction and ad detection are configurable. Additional instructions (like JSON-structured output) are added during execution.

---

## Python Style

Target version: Python 3.12. No compatibility shims.

### Types

Every function — public and private — has full parameter and return type annotations. `mypy --strict` must pass with zero errors. No bare `Any` without a `# type: ignore[<code>]` comment explaining why.

### Enums

Use `enum.StrEnum` for any fixed-value set. Never compare against raw string literals.

```python
from enum import StrEnum

class AudioFormat(StrEnum):
    MP3 = "mp3"
    M4A = "m4a"
```

### Paths

All paths are `pathlib.Path` or `anyio.Path` in an asynchronous function. `os.path` and string concatenation for paths are forbidden.

```python
output_path = Path(cfg.paths.output_dir) / slug / "clean.mp3"
output_path.parent.mkdir(parents=True, exist_ok=True)
```

### Comprehensions and generators

Use comprehensions instead of `map()` or `filter()`. Use a generator expression (no `[]`) when the result is consumed once.

```python
keep = [s for s in segments if s.confidence >= threshold]
total_ms = sum(s.end_ms - s.start_ms for s in ad_segments)
```

### Walrus operator

Use `:=` in guard clauses that check and use a value in one step.

```python
if cached := await repo.get_by_episode_guid(episode.guid):
    return cached
```

### Pattern matching

Use `match`/`case` instead of `if/elif` chains for variant dispatch.

```python
match result:
    case {"status": "ok", "data": data}:
        return _parse(data)
    case {"status": "error", "message": msg}:
        raise AdDetectionError(msg)
    case _:
        raise AdDetectionError(f"Unexpected response: {result}")
```

### Function signatures

- Keyword-only parameters (after `*`) for any argument beyond the second positional.
- `None` sentinel instead of mutable default arguments.
- No `**kwargs` in internal functions — it removes type safety.

### Context managers

Every file, database connection, and HTTP client must be opened via `with` or `async with`. Calling `.close()` manually is forbidden.

```python
async with aiosqlite.connect(db_path) as conn:
    await repo.save(transcript, conn)

async with httpx.AsyncClient() as client:
    response = await client.get(url)
```

---

## Logging

### Setup (`main.py`)

Configure the root logger once at startup using `cfg.logging.level` and `cfg.logging.log_file`. All other modules acquire a module-level logger with `logging.getLogger(__name__)` and never configure handlers themselves.

### Use f-strings in log messages

Use f-strings in all log messages. They are more readable and Python's logging level check (`if logger.isEnabledFor(...)`) is only relevant for expensive-to-construct arguments; for typical string values the difference is negligible.

```python
logger.info(f"Detected {total} ad segments, cutting {above_threshold}")  # correct
logger.info("Detected %d ad segments, cutting %d", total, above_threshold)  # wrong
```

---

## Async

### Blocking code

All `pydub` and `ffmpeg` (heavy I/O) operations run via `asyncio.to_thread`. Calling blocking code directly in a coroutine is forbidden.

### Entry point

Only `main.py` calls `asyncio.run()`. Never call it inside a coroutine.

```python
# main.py
import asyncio
from pipeline.runner import run_pipeline

if __name__ == "__main__":
    asyncio.run(run_pipeline())
```

---

## Error Handling

All custom exceptions inherit from `PodcastAdCutterError` (`pipeline/exceptions.py`):
`ConfigError` · `DatabaseError` · `FeedFetchError` · `DownloadError` · `LLMError` · `TranscriptionError` · `AdDetectionError` · `AudioEditError`

---

## CLI

```bash
uv run python main.py                          # Process all enabled feeds in config.yaml
uv run python main.py --config ./alt.yaml      # Use a different config file
uv run python main.py --feed "My Podcast"      # Process one feed by name
uv run python main.py --output ./my_episodes   # Override output directory
uv run python main.py --min-confidence 0.8     # Override confidence threshold
uv run python main.py --use-cache              # Skip transcription if DB row exists
uv run python main.py --dry-run                # Detect ads but skip audio cutting
uv run python main.py -v                       # Set log level to DEBUG
```

## Web Frontend

A browser-based UI built with **FastAPI + HTMX + Tailwind CSS CDN**. Provides feed management, live pipeline log streaming (SSE), and settings editing without touching config.yaml manually.

```bash
uv run python webui.py                                  # Web UI on http://127.0.0.1:8000
uv run python webui.py --host 0.0.0.0 --port 8080      # Custom host/port
uv run python webui.py --reload                         # Dev mode with auto-reload
uv run python webui.py --config ./alt.yaml              # Use a different config file
uv run python webui.py -v                               # Set log level to DEBUG
```

---
