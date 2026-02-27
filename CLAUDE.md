# CLAUDE.md — Podcast Ad Cutter

## Main instruction for all agents

To generate plans or proposals, to generate code that accesses APIs, or in other cases when a resource is accessed or used, ALWAYS check the documentation using provided MCP servers (context7) or seearch online fo information. As a last resort, inspect the source code of the library in the virtual einvironment itself.

## Project Overview

This project downloads the latest episode of a podcast, transcribes it, uses an LLM to identify advertisements, and exports a clean audio file with all ads removed.

```
RSS Feed → Download Episode → Preprocess Audio → Transcribe → Extract Topic → Detect Ads → Cut Audio → Export
```

---

## Project Structure

```
podcast-ad-cutter/
├── CLAUDE.md
├── README.md
├── Containerfile               # Container build definition
├── pyproject.toml              # Single source of truth: deps, metadata, tool config
├── .python-version             # Contains "3.12" — pins the interpreter for uv and pyenv
├── uv.lock                     # Committed lockfile — never edit manually
├── .env                        # API keys — never commit
├── .env.example                # Committed template
├── config.yaml                 # All non-secret runtime settings
├── config.example.yaml         # Committed template with documented defaults
├── main.py                     # CLI entry point — only place asyncio.run() is called
├── web.py                      # Web UI launcher (uvicorn + argparse)
│
├── config/
│   ├── __init__.py
│   └── config_loader.py        # Loads config.yaml → AppConfig; fails fast on errors
│
├── pipeline/
│   ├── __init__.py
│   ├── llm_client.py           # Only module that imports litellm
│   ├── runner.py               # Orchestrates the full pipeline
│   ├── rss.py                  # RSS feed parsing and episode discovery
│   ├── downloader.py           # Streaming audio download
│   ├── audio_preprocessor.py   # Converts audio to mono 16 kHz 32 kbps MP3 for transcription
│   ├── transcriber.py          # Audio → timestamped Transcript
│   ├── topic_extractor.py      # Transcript excerpt → TopicContext
│   ├── ad_detector.py          # TopicContext + Transcript → AdSegment list
│   ├── audio_editor.py         # Cut AdSegments from audio, export clean file
│   └── exceptions.py           # All custom exception classes
│
├── frontend/
│   ├── __init__.py
│   ├── app.py                  # FastAPI factory + Jinja2Templates with slugify filter
│   ├── state.py                # Pipeline running flag + FeedStatus enum
│   ├── config_editor.py        # Read/mutate/write config.yaml via PyYAML
│   ├── config_cache.py         # Cached AppConfig loader for web routes
│   ├── sse.py                  # QueueLogHandler + async SSE generator
│   └── routes/
│       ├── __init__.py
│       ├── pages.py            # Main page rendering
│       ├── feeds.py            # Feed management endpoints
│       ├── settings.py         # Settings form endpoints
│       └── pipeline.py         # Pipeline start/stop + SSE log stream
│
├── models/
│   ├── __init__.py
│   ├── episode.py              # Episode
│   ├── transcript.py           # Transcript, Segment
│   └── ad_segment.py           # AdSegment, TopicContext
│
├── db/
│   ├── __init__.py
│   ├── schema.sql              # DDL — single source of truth for all tables
│   ├── migrations/
│   │   └── 001_initial.sql
│   ├── connection.py           # get_db() context manager; runs pragmas + migrations
│   └── repositories/
│       ├── __init__.py
│       ├── episode_repo.py
│       ├── transcript_repo.py
│       └── ad_segment_repo.py
│
├── docs/                       # Project documentation
├── deployment/                 # Deployment configs
│
├── tests/
│   ├── conftest.py             # In-memory SQLite fixture, test AppConfig
│   ├── test_rss.py
│   ├── test_transcriber.py
│   ├── test_ad_detector.py
│   ├── test_transcript_repo.py
│   └── fixtures/               # Short audio clips, mock RSS XML, mock transcripts
│
└── output/                     # Gitignored — final audio files only
```

---

## Setup

This project uses [uv](https://docs.astral.sh/uv/). Never use `pip` directly.

```bash
# Install uv (once, globally)
curl -LsSf https://astral.sh/uv/install.sh | sh

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

System dependency: `ffmpeg` — `brew install ffmpeg` or `apt install ffmpeg`.

---

## `pyproject.toml`

All project metadata, dependencies, and tool config live here. No `requirements.txt`, `setup.cfg`, `.flake8`, or `mypy.ini`.

```toml
[project]
name = "podcast-ad-cutter"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "feedparser>=6.0",
    "httpx>=0.27",
    "litellm>=1.40",
    "openai>=1.30",          # required by litellm for atranscription
    "pydub>=0.25",
    "ffmpeg-python>=0.2",
    "aiosqlite>=0.20",
    "pyyaml>=6.0",
    "pydantic>=2.7",
    "tenacity>=8.3",
    "rich>=13.7",
    "python-dotenv>=1.0",
]

[dependency-groups]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
    "mypy>=1.10",
    "ruff>=0.4",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ANN"]
# E/F = pycodestyle/pyflakes  I = isort  UP = pyupgrade  B = bugbear  SIM = simplify  ANN = annotations

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["ANN"]

[tool.mypy]
python_version = "3.12"
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

## Configuration

Non-secret settings live in `config.yaml`. Secrets (API keys only) live in `.env`. Never put API keys in `config.yaml`.

### `config.yaml`

```yaml
feeds:
  - name: "My Favorite Podcast"
    url: "https://feeds.example.com/podcast.rss"
    enabled: true

paths:
  output_dir: "./output"
  database: "./data/podcasts.db"   # Created automatically on first run

# How many recent episodes to retain in the database per feed (older are pruned)
episodes_to_keep: 5

transcription:
  # Supported providers: groq, openai, openrouter
  provider: "groq"
  # Model name without the provider prefix (prefix is added automatically from provider field)
  # Examples: "whisper-large-v3" (Groq/OpenAI), "whisper-1" (OpenAI)
  model: "whisper-large-v3"
  language: "en"                   # null = auto-detect
  api_base: null                    # Override API endpoint, e.g. "http://localhost:11434"

interpretation:
  # Supported providers: groq, openai, openrouter
  provider: "openrouter"
  # Model name without the provider prefix
  # Examples: "anthropic/claude-opus-4-5", "gpt-4o", "llama-3.1-70b-versatile"
  model: "anthropic/claude-opus-4-5"
  api_base: null                   # Override API endpoint, e.g. "http://localhost:11434"
  temperature: 0
  max_tokens: 2048
  topic_excerpt_words: 2000

ad_detection:
  chunk_duration_sec: 300          # Transcript chunk size (~5 min)
  chunk_overlap_sec: 30            # Overlap to catch ads spanning chunk boundaries
  min_confidence: 0.75             # Segments below this threshold are logged but not cut
  merge_gap_sec: 5                 # Merge adjacent ad segments within this gap

audio:
  output_format: "mp3"             # mp3 | m4a
  cbr_bitrate: "192k"              # Constant bitrate — prevents VBR timestamp drift

logging:
  level: "INFO"                    # DEBUG | INFO | WARNING | ERROR
  log_file: null                   # null = stdout only

retry:
  max_attempts: 3
  backoff_factor: 2

# Optional: override the LLM instruction text for each stage.
# The JSON schema suffix is always appended automatically — do not include it here.
prompts:
  ad_detection: |
    Identify advertisements in this podcast transcript segment.
    An ad is any span where the host or another person promotes a product, service, or sponsor.
    Exclude brand mentions that are naturally part of the episode content.
  topic_extraction: |
    Analyze the opening of this podcast transcript.
```

### `.env`

```bash
# litellm reads these from the environment automatically.
# Fill in only the keys for providers you're using.
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...              # Required for Whisper transcription
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-...
AWS_ACCESS_KEY_ID=...              # For Bedrock
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-east-1
# Ollama: no key needed — set interpretation.api_base in config.yaml
```

### `config/config_loader.py` contract

- Loads `config.yaml` with PyYAML, validates against `AppConfig` (Pydantic v2), and calls `python-dotenv` to populate the environment.
- Raises `ConfigError` on any missing required field, invalid value, or API key pattern found inside the YAML.
- Fails fast — all validation runs at startup, before the pipeline executes.
- litellm reads API keys directly from the environment; `AppConfig` does not store them.
- Accepts `config_path: Path` so tests can pass `tests/fixtures/test_config.yaml`.
- `TranscriptionConfig` and `InterpretationConfig` both inherit from `LLMProviderConfig(provider, model, api_base)`.
- `SUPPORTED_PROVIDERS = frozenset({"groq", "openai", "openrouter"})` — validated at load time.
- `LLMProviderConfig.provider_model` is a computed field that returns `f"{provider}/{model}"` for use as the litellm model string.

```python
from config.config_loader import load_config

cfg = load_config(Path("config.yaml"))
cfg.transcription.provider      # "groq"
cfg.transcription.model         # "whisper-large-v3"
cfg.transcription.provider_model  # "groq/whisper-large-v3"  ← pass to litellm
cfg.interpretation.provider     # "openrouter"
cfg.interpretation.model        # "anthropic/claude-opus-4-5"
cfg.interpretation.provider_model  # "openrouter/anthropic/claude-opus-4-5"
cfg.interpretation.api_base     # None
cfg.paths.database              # Path("./data/podcasts.db")
cfg.episodes_to_keep            # 5
```

---

## LLM Client (`pipeline/llm_client.py`)

`llm_client.py` is the **only** module that imports `litellm`. All LLM calls go through `complete()` or `transcribe()`. This gives tests a single mock point and keeps provider logic out of business code.

### Provider routing

litellm routes to the correct backend based on the model string prefix. The corresponding env var must be set.

| Provider value | litellm prefix used | Notes |
|---|---|---|
| `groq` | `groq/` | Requires `GROQ_API_KEY` |
| `openai` | `openai/` | Requires `OPENAI_API_KEY`; also used for Whisper |
| `openrouter` | `openrouter/` | Requires `OPENROUTER_API_KEY`; model can be any OpenRouter model string |

`SUPPORTED_PROVIDERS = frozenset({"groq", "openai", "openrouter"})` — config validation rejects anything else at startup.

### Implementation

```python
import logging
from pathlib import Path
from typing import Any

import litellm
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from config.config_loader import InterpretationConfig, TranscriptionConfig
from pipeline.exceptions import LLMError, TranscriptionError

logger = logging.getLogger(__name__)
litellm.suppress_debug_info = True


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def complete(
    messages: list[dict[str, str]],
    cfg: InterpretationConfig,
    *,
    response_format: dict[str, str] | None = None,
) -> str:
    """Return the text of the first completion choice. Retries up to 3× with backoff."""
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.api_base:
        kwargs["api_base"] = cfg.api_base
    if response_format:
        kwargs["response_format"] = response_format

    logger.debug("LLM request model=%s messages=%d", cfg.model, len(messages))
    try:
        response = await litellm.acompletion(**kwargs)
    except litellm.APIError as exc:
        raise LLMError(f"LLM call failed: {exc}") from exc

    content: str = response.choices[0].message.content or ""
    logger.debug(
        "LLM response model=%s prompt_tokens=%s completion_tokens=%s",
        cfg.model,
        response.usage.prompt_tokens,
        response.usage.completion_tokens,
    )
    return content


async def transcribe(audio_path: Path, cfg: TranscriptionConfig) -> dict[str, Any]:
    """Transcribe audio via litellm.atranscription. Returns verbose JSON with word timestamps."""
    logger.info("Transcribing %s with model=%s", audio_path.name, cfg.model)
    with audio_path.open("rb") as f:
        try:
            result = await litellm.atranscription(  # type: ignore[no-any-return]
                model=cfg.model,
                file=f,
                language=cfg.language,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        except litellm.APIError as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

    logger.info("Transcription complete segments=%d", len(result.get("words", [])))
    return result
```

**JSON output:** `response_format={"type": "json_object"}` is not supported by all providers (Ollama, some Bedrock variants). Always instruct the model to return JSON via the system prompt *and* pass `response_format` when available. Log malformed responses at `WARNING`, skip the chunk, and continue — never raise.

---

## Pipeline Design

Each pipeline stage follows the same pattern: log `INFO` at entry and completion, log `DEBUG` for internal details, log `WARNING` for recoverable issues, and raise on unrecoverable errors.

### Audio preprocessing (`audio_preprocessor.py`)

Runs between Download and Transcription to reduce upload size and improve Whisper accuracy.

1. Convert the downloaded audio to mono 16 kHz 32 kbps MP3 via `pydub` (inside `asyncio.to_thread`).
2. Write the result as `transcription_input.mp3` alongside the source file.
3. Log `INFO` ("Pre-processing audio for transcription: {source} → {output}").
4. Return the output path. **Caller (`transcriber.py`) is responsible for deleting it after use.**

The CBR 32 kbps mono format is sufficient for speech recognition and cuts upload size by ~80% vs a typical podcast file.

### Transcription (`transcriber.py`)

1. Check the `transcripts` table for a row matching `episode.guid`. If found, log `INFO` ("Transcript cache hit for {guid}") and return immediately.
2. Call `audio_preprocessor.prepare_for_transcription()` to produce a lean input file.
3. Log `INFO` ("Starting transcription for {guid}"). Call `llm_client.transcribe()`. The `verbose_json` response includes word-level timestamps required for precise cuts.
4. Delete the preprocessed file.
5. Persist the `Transcript` and all `Segment` rows to the database.
6. Log `INFO` ("Transcription saved: {n} segments, {duration}s").

### Topic extraction (`topic_extractor.py`)

1. Send the first `cfg.interpretation.topic_excerpt_words` words of the transcript to `llm_client.complete()`.
2. The system prompt instructs the LLM to return `{"domain": str, "topic": str, "hosts": list[str], "notes": str}`. Parse into `TopicContext` and persist it.
3. Log `INFO` ("Topic extracted: domain={domain} topic={topic}").
4. `TopicContext` is injected into every ad detection prompt to prevent false positives from incidental brand mentions.

### Ad detection (`ad_detector.py`)

1. Split the transcript into chunks of `chunk_duration_sec` with `chunk_overlap_sec` overlap. Log `INFO` ("Detecting ads in {n} chunks").
2. Send all chunks to the LLM concurrently via `asyncio.TaskGroup`, bounded by `_LLM_SEMAPHORE`. Each chunk carries `TopicContext`.
3. Log `DEBUG` for each chunk: ("Chunk {i}/{n}: {start_sec:.1f}–{end_sec:.1f}s").
4. If the LLM returns malformed JSON for a chunk, log `WARNING` ("Skipping chunk {i}: invalid JSON response") and continue.
5. The LLM identifies spans where the host promotes a sponsor. Key signals:
   - Promo codes or discount URLs
   - Content unrelated to the episode topic in a promotional register
   - Openers: "This episode is brought to you by…", "Our sponsor today is…"
   - Closers: "…link in the description", "Use code [X]", "Back to the show"
6. Required per-chunk output: `[{"start_sec": float, "end_sec": float, "confidence": float, "reason": str, "sponsor": str | null}]`
7. After all chunks, merge segments within `merge_gap_sec` of each other.
8. For each segment below `min_confidence`, log `WARNING` ("Low-confidence ad segment skipped: {start}–{end}ms confidence={confidence:.2f} reason={reason}").
9. Persist all detected segments. Cut only segments where `confidence >= min_confidence`.
10. Log `INFO` ("Ad detection complete: {total} detected, {cut} above threshold").

### Audio editing (`audio_editor.py`)

1. Log `INFO` ("Cutting {n} ad segments from {path.name}").
2. All `pydub` calls run inside `asyncio.to_thread(_cut_sync, ...)`.
3. Compute keep-segments as the inverse of ad segments and concatenate them.
4. Export at `audio.cbr_bitrate` as CBR to prevent timestamp drift.
5. Always write to a new file — never overwrite the original download.
6. Log `INFO` ("Export complete: {output_path.name} — removed {removed_sec:.1f}s ({pct:.0f}% of episode)").

---

## Database

All structured data lives in SQLite at `paths.database`. `output/` holds audio files only.

### Schema (`db/schema.sql`)

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS episodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guid         TEXT    NOT NULL UNIQUE,
    feed_name    TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    audio_url    TEXT    NOT NULL,
    published_at TEXT,
    duration_sec INTEGER,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transcripts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid   TEXT NOT NULL UNIQUE REFERENCES episodes(guid),
    language       TEXT NOT NULL DEFAULT 'en',
    full_text      TEXT NOT NULL,
    provider_model TEXT NOT NULL,
    transcribed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
    start_ms      INTEGER NOT NULL,
    end_ms        INTEGER NOT NULL,
    text          TEXT    NOT NULL,
    CHECK (end_ms > start_ms)
);
CREATE INDEX IF NOT EXISTS idx_segments_transcript ON transcript_segments(transcript_id);

CREATE TABLE IF NOT EXISTS topic_contexts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid TEXT NOT NULL UNIQUE REFERENCES episodes(guid),
    domain       TEXT NOT NULL,
    topic        TEXT NOT NULL,
    hosts        TEXT,                      -- JSON array
    notes        TEXT,
    extracted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ad_segments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_guid TEXT    NOT NULL REFERENCES episodes(guid),
    start_ms     INTEGER NOT NULL,
    end_ms       INTEGER NOT NULL,
    confidence   REAL    NOT NULL,
    reason       TEXT    NOT NULL,
    sponsor_name TEXT,
    was_cut      INTEGER NOT NULL DEFAULT 0,
    detected_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (end_ms > start_ms),
    CHECK (confidence BETWEEN 0.0 AND 1.0)
);
CREATE INDEX IF NOT EXISTS idx_ad_segments_episode ON ad_segments(episode_guid);
```

### Repository pattern

All SQL lives in `db/repositories/`. SQL anywhere else in the codebase is a bug.

- `EpisodeRepository`: `get_by_guid`, `upsert`, `list_by_feed`
- `TranscriptRepository`: `get_by_episode_guid`, `save`, `delete`
- `AdSegmentRepository`: `save_all`, `get_by_episode`, `mark_cut`

### `db/connection.py`

- Opens the database file, creating parent directories if needed.
- Sets `PRAGMA foreign_keys = ON` and `PRAGMA journal_mode = WAL` on every new connection — both are per-session and do not persist.
- Applies pending migrations from `db/migrations/` via a `_migrations` tracking table. New schema changes go in a new numbered `.sql` file. Never edit an applied migration.
- Exposes `async with get_db() as conn`.

---

## Data Models

All models are frozen Pydantic `BaseModel` subclasses — immutable, validated at construction, and JSON-serialisable. Never use `@dataclass` for models that cross module boundaries or touch the database.

```python
# models/episode.py
from datetime import datetime
from pydantic import BaseModel, HttpUrl

class Episode(BaseModel, frozen=True):
    guid: str
    feed_title: str
    title: str
    audio_url: HttpUrl
    published: datetime
    duration_seconds: int | None = None


# models/transcript.py
from pydantic import BaseModel, model_validator

class Segment(BaseModel, frozen=True):
    start_ms: int
    end_ms: int
    text: str

    @model_validator(mode="after")
    def end_after_start(self) -> Segment:
        if self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms {self.end_ms} must be > start_ms {self.start_ms}")
        return self

class Transcript(BaseModel, frozen=True):
    episode_guid: str
    segments: tuple[Segment, ...]
    full_text: str
    language: str
    provider_model: str


# models/ad_segment.py
from pydantic import BaseModel, Field

class TopicContext(BaseModel, frozen=True):
    domain: str
    topic: str
    hosts: tuple[str, ...]
    notes: str

class AdSegment(BaseModel, frozen=True):
    episode_guid: str
    start_ms: int
    end_ms: int
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    sponsor_name: str | None = None
    was_cut: bool = False
```

---

## LLM Prompts

### Topic extraction

```
System: Analyze the opening of this podcast transcript.
        Return only a JSON object — no markdown, no preamble.
        Schema: {"domain": str, "topic": str, "hosts": list[str], "notes": str}

User:   <transcript>{first_N_words}</transcript>
```

### Ad detection (per chunk)

```
System: Identify advertisements in this podcast transcript segment.
        An ad is any span where the host promotes a product, service, or sponsor.
        Exclude brand mentions that are naturally part of the episode content.
        Return only a JSON array — no markdown, no preamble.
        Schema: [{"start_sec": float, "end_sec": float, "confidence": float,
                  "reason": str, "sponsor": str | null}]
        Return [] if no ads are found.

User:   Episode context: {topic_context}

        Transcript (timestamps in seconds):
        <transcript>{chunk_text}</transcript>
```

---

## Python Style

**Target: Python 3.12. No compatibility shims. No `from __future__ import annotations`.**

### Types

```python
# Unions — never Optional[X] or Union[X, Y]
def get(guid: str) -> Transcript | None: ...

# Built-in generics — never typing.List, typing.Dict, typing.Tuple
segments: list[Segment]
counts: dict[str, int]

# 3.12 type alias statement
type Milliseconds = int
type SegmentPair = tuple[Milliseconds, Milliseconds]
```

Every function — public and private — has full parameter and return type annotations. `mypy --strict` must pass with zero errors. No bare `Any` without a `# type: ignore[<code>]` comment explaining why.

### Naming

| Kind | Convention |
|---|---|
| Functions, variables, modules | `snake_case` |
| Classes | `PascalCase` |
| Module-level constants | `SCREAMING_SNAKE_CASE` |
| Private module helpers | `_leading_underscore` |
| Single-letter names | Comprehensions and math expressions only |

### Enums

Use `enum.StrEnum` for any fixed-value set. Never compare against raw string literals.

```python
from enum import StrEnum

class AudioFormat(StrEnum):
    MP3 = "mp3"
    M4A = "m4a"
```

### Paths

All paths are `pathlib.Path`. `os.path` and string concatenation for paths are forbidden.

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

```python
import logging
from pathlib import Path

def setup_logging(level: str, log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(Path(log_file)))
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
```

### Level contract

| Level | Use for |
|---|---|
| `DEBUG` | Internal details useful when diagnosing a bug: LLM request/response token counts, SQL queries, per-chunk boundaries, retry attempt details |
| `INFO` | Every pipeline milestone a user would care about: episode found, download started/complete, transcription started/complete/cached, topic extracted, ad detection started/complete with counts, export complete with time saved |
| `WARNING` | Recoverable issues that affect output quality: LLM returns malformed JSON (chunk skipped), ad segment below confidence threshold (not cut), URL redirect followed, RSS namespace fallback used |
| `ERROR` | Always log immediately before raising a custom exception so the traceback has context |

### Use f-strings in log messages

Use f-strings in all log messages. They are more readable and Python's logging level check (`if logger.isEnabledFor(...)`) is only relevant for expensive-to-construct arguments; for typical string values the difference is negligible.

```python
logger.info(f"Detected {total} ad segments, cutting {above_threshold}")  # correct
logger.info("Detected %d ad segments, cutting %d", total, above_threshold)  # wrong
```

---

## Async

### Layer assignments

| Module | Mode |
|---|---|
| `rss.py` | async |
| `downloader.py` | async |
| `audio_preprocessor.py` | async (wraps sync via `asyncio.to_thread`) |
| `transcriber.py` | async |
| `topic_extractor.py` | async |
| `ad_detector.py` | async |
| `db/repositories/` | async |
| `audio_editor.py` | sync, called via `asyncio.to_thread` |
| `config/config_loader.py` | sync |

### Blocking code

All `pydub` and `ffmpeg` operations run via `asyncio.to_thread`. Calling blocking code directly in a coroutine is forbidden.

```python
async def cut_ads(audio_path: Path, ad_segments: list[AdSegment]) -> Path:
    return await asyncio.to_thread(_cut_ads_sync, audio_path, ad_segments)

def _cut_ads_sync(audio_path: Path, ad_segments: list[AdSegment]) -> Path:
    audio = AudioSegment.from_file(audio_path)
    ...
    return output_path
```

### Concurrent LLM calls

Use `asyncio.TaskGroup` for chunk processing. Every LLM call acquires `_LLM_SEMAPHORE` before proceeding — 3 is a safe default; tune per provider tier.

```python
_LLM_SEMAPHORE = asyncio.Semaphore(3)

async def detect_ads_in_chunks(
    chunks: list[TranscriptChunk],
    context: TopicContext,
    cfg: AppConfig,
) -> list[AdSegment]:
    results: list[list[AdSegment]] = [[] for _ in chunks]

    async with asyncio.TaskGroup() as tg:
        for i, chunk in enumerate(chunks):
            tg.create_task(_detect_chunk(chunk, context, cfg, results, i))

    return _merge_segments([s for batch in results for s in batch], cfg)

async def _detect_chunk(
    chunk: TranscriptChunk,
    context: TopicContext,
    cfg: AppConfig,
    results: list[list[AdSegment]],
    index: int,
) -> None:
    async with _LLM_SEMAPHORE:
        response = await llm_client.complete(messages, cfg.interpretation)
        results[index] = _parse_ad_segments(response, chunk.episode_guid)
```

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

- Always chain: `raise TranscriptionError("msg") from exc`
- Log at `ERROR` immediately before raising: `logger.error("Transcription failed for %s: %s", guid, exc)`
- Never use bare `except:` or `except Exception:` without re-raising or logging at `ERROR`
- Catch the narrowest exception type possible
- Retry logic lives in `llm_client.py` only — no retry loops anywhere else
- `TaskGroup` failures propagate as `ExceptionGroup` — handle with `except*`

---

## Testing

```bash
uv run pytest
uv run pytest tests/test_ad_detector.py -v
uv run pytest -k "test_merge"
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

- `asyncio_mode = "auto"` — write `async def test_*` directly, no `@pytest.mark.asyncio` decorator.
- Mock `pipeline.llm_client.complete` and `pipeline.llm_client.transcribe` with `AsyncMock`. Never mock litellm internals. Never make real network calls.
- Mock HTTP with `respx`.
- Pass `config_path=Path("tests/fixtures/test_config.yaml")` to `load_config()` in all tests.
- Use the in-memory SQLite fixture from `conftest.py`. Never open the real database.
- Audio fixtures must be ≤ 10 seconds.
- Test that `Segment` and `AdSegment` validators raise `ValidationError` for `end_ms <= start_ms` and `confidence` outside `[0.0, 1.0]`.
- Test repository round-trips: save a model, retrieve by primary key, assert all fields equal.

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
uv run python web.py                                  # Web UI on http://127.0.0.1:8000
uv run python web.py --host 0.0.0.0 --port 8080      # Custom host/port
uv run python web.py --reload                         # Dev mode with auto-reload
uv run python web.py --config ./alt.yaml              # Use a different config file
uv run python web.py -v                               # Set log level to DEBUG
```

Key files:

| File | Role |
|---|---|
| `web.py` | uvicorn launcher + argparse; imports `setup_logging` from `main.py` |
| `frontend/app.py` | FastAPI factory (`create_app()`); registers routers; Jinja2 with `slugify` filter |
| `frontend/state.py` | Module-level pipeline running flag; `FeedStatus` enum |
| `frontend/config_editor.py` | `set_config_path()`, `read_config()`, `write_config()` — all config mutations go here |
| `frontend/config_cache.py` | Cached `AppConfig` loader used by route handlers |
| `frontend/sse.py` | `QueueLogHandler` captures pipeline logs; async SSE generator streams them to browser |
| `frontend/routes/pages.py` | Main page render |
| `frontend/routes/feeds.py` | Feed CRUD endpoints (HTMX partials) |
| `frontend/routes/settings.py` | Settings form — reads/writes via `config_editor` |
| `frontend/routes/pipeline.py` | Start/Stop pipeline; `/log` SSE endpoint |

---

## Output

```
output/
└── deep-dive-into-rust/
    ├── original.mp3     # Downloaded, unmodified
    ├── clean.mp3        # Ads removed
    └── report.txt       # Segments removed, total time cut, % of episode
```

All other structured data (transcripts, topic context, ad segments) is in the database.

```bash
sqlite3 data/podcasts.db "SELECT full_text FROM transcripts WHERE episode_guid = '...'"
sqlite3 data/podcasts.db "SELECT start_ms, end_ms, confidence, sponsor_name FROM ad_segments WHERE episode_guid = '...'"
```

---

## Definition of Done

| Stage | Complete when |
|---|---|
| RSS | `Episode` returned with valid `audio_url`; row upserted in `episodes` |
| Download | File on disk, size > 0, SHA-256 verified |
| Transcription | Segments cover ≥ 95% of audio duration; rows in `transcripts` + `transcript_segments` |
| Topic Extraction | `TopicContext` with non-empty `domain` and `topic`; row in `topic_contexts` |
| Ad Detection | Rows in `ad_segments`; all have `start_ms < end_ms`; no overlapping segments after merge |
| Audio Edit | `clean.mp3` exists; duration = original − Σ(cut durations) ± 500ms; `was_cut = 1` on all cut rows |

---

## Gotchas

- **litellm model strings require the provider prefix.** `"claude-opus-4-5"` fails; `"anthropic/claude-opus-4-5"` is correct. Consult [litellm provider docs](https://docs.litellm.ai/docs/providers) for exact strings.
- **litellm caches provider lookups in memory.** Restart the process after changing `interpretation.model` in `config.yaml`.
- **`response_format={"type": "json_object"}` is not universal.** Groq and Ollama may reject it. Always instruct via system prompt and parse defensively.
- **`pydub` blocks the event loop.** Call it exclusively via `asyncio.to_thread`. Never call it directly in a coroutine.
- **`aiosqlite` cursors cannot be reused across `await` points.** Call `await cursor.fetchall()` before the next `await`.
- **SQLite pragmas are per-session.** `PRAGMA foreign_keys = ON` and `PRAGMA journal_mode = WAL` must be set in `connection.py` on every new connection.
- **Never query `transcript_segments` without `WHERE transcript_id = ?`.** A 2-hour episode can produce 5 000+ rows.
- **Whisper returns timestamps in seconds (float); pydub uses milliseconds (int).** All internal time values use `type Milliseconds = int`. Convert at the boundary, not inside processing logic.
- **Podcast audio is often VBR MP3.** Export as CBR to prevent timestamp drift on cut-and-join.
- **RSS feeds use both `<enclosure>` and `<media:content>`.** `rss.py` must handle both namespaces.
- **Podcast audio URLs often redirect.** Resolve to the final URL before opening the streaming downloader.
- **Episode files can exceed 200 MB.** Always stream downloads — never buffer the full file in memory.
