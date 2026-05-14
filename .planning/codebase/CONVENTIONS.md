# Coding Conventions

**Analysis Date:** 2026-05-14

## Naming Patterns

**Files:**
- Modules use `snake_case.py` throughout — e.g., `ad_detector.py`, `episode_store.py`, `config_loader.py`
- Test files mirror the module they test: `test_ad_detector.py`, `test_database_connection.py`
- No abbreviations in filenames; full descriptive names only

**Classes:**
- `PascalCase` for all classes: `AdDetector`, `FeedDownloader`, `EpisodeStore`, `Database`
- Private/internal sentinel classes use leading underscore: `_JsonValidateFailedError`, `_ContextWindowExceededError`, `_Stores`
- Pydantic models appended with `Schema` when used as LLM response contracts: `AdDetectionResponseSchema`, `AdSegmentDetectionSchema`
- Dataclasses used for plain data transfer objects: `AdSegment`, `Episode`, `AudioMetadata`, `CutRange`

**Functions and Methods:**
- `snake_case` for all functions and methods
- Private methods use leading underscore: `_fetch_one`, `_make_detector` (in tests)
- Async methods not prefixed — async-ness is inferred from `async def`

**Variables and Constants:**
- `snake_case` for local variables and parameters
- Module-level constants use `UPPER_SNAKE_CASE`: `_EPISODES_SCHEMA`, `_COMPLETION_RESERVE_TOKENS`, `PROVIDER_KEY_MAP`
- Private module-level constants use leading underscore + `UPPER_SNAKE_CASE`: `_SYSTEM_PROMPT_TEMPLATE`

## Python Version Target

- Python 3.12, configured in `pyproject.toml` (`target-version = "py312"`)
- `from __future__ import annotations` at the top of every file — enables PEP 563 postponed evaluation for cleaner forward references

## Import Organization

**Order enforced by ruff:**
1. `from __future__ import annotations` (always first)
2. Standard library (`import json`, `import logging`, `from pathlib import Path`)
3. Third-party packages (`import litellm`, `import pydantic`, `import aiohttp`)
4. Local project imports (`from components.ad_detector import AdDetector`, `from models.feed import Episode`)

**TYPE_CHECKING guard — mandatory pattern for runtime-unused type imports:**
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    import aiosqlite
```
Used in all database stores, most components, and `components/pipeline.py`. Types imported under `TYPE_CHECKING` must not be used at runtime — enforced by ruff rules `TC001`/`TC003`. Exceptions are explicitly annotated in `pyproject.toml` per-file ignores (e.g., `config/config_loader.py`, `utils/episode_log.py`).

**No barrel/re-export files.** `__init__.py` files are empty.

## Async Patterns

**Async throughout:** Every I/O operation is `async def`. No synchronous blocking calls in async paths.

**Context managers for every resource:**
```python
# HTTP sessions
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        ...

# Database
async with Database(db_path) as db:
    store = EpisodeStore(db.conn)
    ...

# aiosqlite cursors
async with self._conn.execute("SELECT ...") as cursor:
    rows = await cursor.fetchall()
```

**Subprocess/process:** Wrapped in custom `Ffmpeg` utility class (`utils/ffmpeg.py`) using `asyncio.create_subprocess_exec`.

**asyncio_mode = "auto"** configured in `pyproject.toml` — all `async def test_*` functions are recognized as async tests without `@pytest.mark.asyncio`.

## Class Design

**Single-responsibility classes:** Each component is a class with a clear public API. `Pipeline` (`components/pipeline.py`) is the sole orchestrator; no component imports from `config/`.

**Constructor injection:** Components receive only the plain data they need — no config objects passed to components below `Pipeline`.

**Store pattern for database access:** Each table domain has its own store class (e.g., `AdStore`, `EpisodeStore`, `TranscriptionStore`). Stores receive an `aiosqlite.Connection` — they do NOT own the connection lifecycle. Only `Database` (`database/connection.py`) manages connection open/close.

**`@dataclass` for plain data:** Transfer objects between layers use `@dataclass` (not Pydantic). Pydantic `BaseModel` is reserved for LLM response schema validation and config loading.

**`@dataclass(slots=True)` for internal grouping types:** E.g., `_Stores` in `components/pipeline.py`.

## Error Handling

**Custom exception hierarchy rooted at `PodcastAdCutterError`** (`utils/exceptions.py`):
```
PodcastAdCutterError
├── ConfigError
├── AudioProbeError
├── FfmpegError          # has .message and .stderr attributes
├── TranscriptionError   # has .message attribute
├── TopicExtractionError # has .message attribute
└── AdDetectionError     # has .message attribute
```

**Pattern:** Catch library/subprocess exceptions and re-raise as domain-specific errors with a `message` attribute. Never propagate raw library exceptions across component boundaries.

**`contextlib.suppress`** used for expected non-fatal errors (e.g., schema migration):
```python
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute("ALTER TABLE episodes ADD COLUMN source_url TEXT NOT NULL DEFAULT ''")
```

## Logging Style

**Logger instantiation — always module-level, never inside a class or function:**
```python
logger = logging.getLogger(__name__)
```
Present in every module that emits log messages (`components/`, `database/`, `utils/`, `main.py`).

**f-strings mandatory — never `%` operator:**
```python
logger.debug(f"Fetching feed '{title}' from {url}")
logger.warning(f"Feed '{title}' returned HTTP {response.status}, skipping")
logger.error(f"Failed to fetch feed '{title}': {exc}")
logger.debug(f"Saved {len(segments)} ad segment(s) for '{guid}'")
```

**Log levels used:**
- `DEBUG`: operational detail — data fetched, rows saved, decisions taken
- `INFO`: high-level pipeline milestones
- `WARNING`: recoverable non-fatal issues (non-200 HTTP, reasoning not supported by model)
- `ERROR`: failures that skip an episode or feed

## Code Style and Formatting

**Line length:** 120 characters (`pyproject.toml`)

**Ruff:** `select = ["ALL"]` with a targeted ignore list. Key ignored rules:
- `D107` — no docstrings required in `__init__`
- `D203`/`D213` — docstring style variants
- `TRY003` — long exception messages allowed
- `G004` — f-strings in logging allowed (project-mandated)
- `T201` — `print` allowed (used in `main.py`)
- `PLR0913` — many function parameters allowed

**Mypy:** `strict = True` with `pydantic.mypy` plugin. All production code is fully typed. Tests exempt from `disallow_untyped_defs`.

## Docstrings

**Google-style docstrings** on all public classes and methods:
```python
async def download_all(
    self,
    feeds: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Download XML for each feed in order.

    Args:
        feeds: ``(title, url)`` pairs to fetch. Order is preserved.

    Returns:
        List of ``(title, xml_text)`` for every successfully fetched feed.

    """
```

**Class docstrings** describe purpose, key design constraints, and constructor args. `__init__` does not have its own docstring (rule `D107` ignored).

**Module docstrings** are one-line dash-separated summaries:
```python
"""AdDetector — identifies advertisement segments in podcast transcripts via an LLM."""
```

## Comments

**Section headers in test files** use dashed comment separators to group related tests:
```python
# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------
```

**Inline comments** for non-obvious field semantics:
```python
source_url: str = ""  # immutable original feed enclosure URL; never updated after first insert
```

## Module Constants

Private module-level SQL schema strings use `UPPER_SNAKE_CASE` with underscore prefix:
```python
_EPISODES_SCHEMA = """CREATE TABLE IF NOT EXISTS episodes (...)"""
```

Shared test data constants also use `UPPER_SNAKE_CASE`:
```python
_SEGMENTS = [...]
_VALID_DETECTIONS = json.dumps(...)
```

---

*Convention analysis: 2026-05-14*
