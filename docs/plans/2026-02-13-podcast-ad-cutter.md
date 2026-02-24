# Podcast Ad Cutter — Full Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a CLI tool that downloads podcast episodes, transcribes them, detects ads via LLM, and exports clean audio with ads removed.

**Architecture:** Pipeline architecture: RSS → Download → Transcribe → Topic Extract → Ad Detect → Audio Edit. All LLM calls go through a single `llm_client.py` module using litellm. Data persists in SQLite via async repositories. Audio processing runs in threads to avoid blocking the event loop.

**Tech Stack:** Python 3.12, uv, litellm, httpx, pydub, ffmpeg-python, aiosqlite, Pydantic v2, feedparser, tenacity, rich, pytest + pytest-asyncio + respx

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `config.example.yaml`

**Step 1: Create `.python-version`**

```
3.12
```

**Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.env
output/
data/
*.egg-info/
dist/
.mypy_cache/
.pytest_cache/
.ruff_cache/
```

**Step 3: Create `pyproject.toml`**

```toml
[project]
name = "podcast-ad-cutter"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "feedparser>=6.0",
    "httpx>=0.27",
    "litellm>=1.40",
    "openai>=1.30",
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

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["ANN"]

[tool.mypy]
python_version = "3.12"
strict = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 4: Create `.env.example`**

```bash
# litellm reads these from the environment automatically.
# Fill in only the keys for providers you're using.
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-east-1
```

**Step 5: Create `config.example.yaml`**

```yaml
feeds:
  - name: "My Favorite Podcast"
    url: "https://feeds.example.com/podcast.rss"
    enabled: true

paths:
  output_dir: "./output"
  database: "./data/podcasts.db"

transcription:
  model: "whisper-1"
  language: "en"

llm:
  model: "anthropic/claude-opus-4-5"
  api_base: null
  temperature: 0
  max_tokens: 2048
  topic_excerpt_words: 2000

ad_detection:
  chunk_duration_sec: 300
  chunk_overlap_sec: 30
  min_confidence: 0.75
  merge_gap_sec: 5
  max_tokens_per_chunk: 6000

audio:
  output_format: "mp3"
  cbr_bitrate: "192k"

logging:
  level: "INFO"
  log_file: null

retry:
  max_attempts: 3
  backoff_factor: 2
```

**Step 6: Create directory stubs**

```bash
mkdir -p pipeline models db/repositories db/migrations tests/fixtures output
touch pipeline/__init__.py models/__init__.py db/__init__.py db/repositories/__init__.py
```

**Step 7: Install dependencies**

Run: `uv sync`
Expected: Lockfile created, all deps installed, `.venv` created.

**Step 8: Initialize git and commit**

```bash
git init
git add .
git commit -m "chore: project scaffolding with pyproject.toml and config templates"
```

---

## Task 2: Custom Exceptions

**Files:**
- Create: `pipeline/exceptions.py`

**Step 1: Write the exceptions module**

```python
class PodcastAdCutterError(Exception):
    """Base exception for all podcast-ad-cutter errors."""


class ConfigError(PodcastAdCutterError):
    """Invalid or missing configuration."""


class DatabaseError(PodcastAdCutterError):
    """Database operation failed."""


class FeedFetchError(PodcastAdCutterError):
    """RSS feed fetch or parse failed."""


class DownloadError(PodcastAdCutterError):
    """Audio file download failed."""


class LLMError(PodcastAdCutterError):
    """LLM completion call failed."""


class TranscriptionError(PodcastAdCutterError):
    """Audio transcription failed."""


class AdDetectionError(PodcastAdCutterError):
    """Ad detection processing failed."""


class AudioEditError(PodcastAdCutterError):
    """Audio cutting or export failed."""
```

**Step 2: Commit**

```bash
git add pipeline/exceptions.py
git commit -m "feat: add custom exception hierarchy"
```

---

## Task 3: Data Models

**Files:**
- Create: `models/episode.py`
- Create: `models/transcript.py`
- Create: `models/ad_segment.py`
- Create: `models/__init__.py` (re-exports)
- Create: `tests/test_models.py`

**Step 1: Write the failing tests**

```python
# tests/test_models.py
import pytest
from pydantic import ValidationError


def test_episode_creation():
    from models.episode import Episode

    ep = Episode(
        guid="abc-123",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    assert ep.guid == "abc-123"
    assert ep.duration_seconds is None


def test_episode_is_frozen():
    from models.episode import Episode

    ep = Episode(
        guid="abc-123",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    with pytest.raises(ValidationError):
        ep.guid = "new"  # type: ignore[misc]


def test_segment_valid():
    from models.transcript import Segment

    seg = Segment(start_ms=0, end_ms=1000, text="hello")
    assert seg.start_ms == 0
    assert seg.end_ms == 1000


def test_segment_end_before_start_raises():
    from models.transcript import Segment

    with pytest.raises(ValidationError):
        Segment(start_ms=1000, end_ms=500, text="bad")


def test_segment_equal_start_end_raises():
    from models.transcript import Segment

    with pytest.raises(ValidationError):
        Segment(start_ms=1000, end_ms=1000, text="bad")


def test_transcript_creation():
    from models.transcript import Segment, Transcript

    t = Transcript(
        episode_guid="abc-123",
        segments=(Segment(start_ms=0, end_ms=1000, text="hello"),),
        full_text="hello",
        language="en",
        provider_model="whisper-1",
    )
    assert len(t.segments) == 1


def test_ad_segment_valid():
    from models.ad_segment import AdSegment

    ad = AdSegment(
        episode_guid="abc-123",
        start_ms=60000,
        end_ms=120000,
        confidence=0.9,
        reason="Promo code mentioned",
        sponsor_name="Acme",
    )
    assert ad.was_cut is False


def test_ad_segment_confidence_out_of_range():
    from models.ad_segment import AdSegment

    with pytest.raises(ValidationError):
        AdSegment(
            episode_guid="abc-123",
            start_ms=0,
            end_ms=1000,
            confidence=1.5,
            reason="bad",
        )


def test_ad_segment_confidence_negative():
    from models.ad_segment import AdSegment

    with pytest.raises(ValidationError):
        AdSegment(
            episode_guid="abc-123",
            start_ms=0,
            end_ms=1000,
            confidence=-0.1,
            reason="bad",
        )


def test_topic_context_creation():
    from models.ad_segment import TopicContext

    tc = TopicContext(
        domain="technology",
        topic="Rust programming",
        hosts=("Alice", "Bob"),
        notes="Weekly deep dive",
    )
    assert tc.hosts == ("Alice", "Bob")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — modules don't exist yet.

**Step 3: Write `models/episode.py`**

```python
from datetime import datetime

from pydantic import BaseModel, HttpUrl


class Episode(BaseModel, frozen=True):
    guid: str
    feed_title: str
    title: str
    audio_url: HttpUrl
    published: datetime
    duration_seconds: int | None = None
```

**Step 4: Write `models/transcript.py`**

```python
from pydantic import BaseModel, model_validator


class Segment(BaseModel, frozen=True):
    start_ms: int
    end_ms: int
    text: str

    @model_validator(mode="after")
    def end_after_start(self) -> "Segment":
        if self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms {self.end_ms} must be > start_ms {self.start_ms}")
        return self


class Transcript(BaseModel, frozen=True):
    episode_guid: str
    segments: tuple[Segment, ...]
    full_text: str
    language: str
    provider_model: str
```

**Step 5: Write `models/ad_segment.py`**

```python
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

**Step 6: Update `models/__init__.py`**

```python
from models.ad_segment import AdSegment, TopicContext
from models.episode import Episode
from models.transcript import Segment, Transcript

__all__ = ["AdSegment", "Episode", "Segment", "TopicContext", "Transcript"]
```

**Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: All PASS.

**Step 8: Commit**

```bash
git add models/ tests/test_models.py
git commit -m "feat: add Pydantic data models with validation"
```

---

## Task 4: Config Loader

**Files:**
- Create: `config_loader.py`
- Create: `tests/fixtures/test_config.yaml`
- Create: `tests/test_config_loader.py`

**Step 1: Create `tests/fixtures/test_config.yaml`**

```yaml
feeds:
  - name: "Test Podcast"
    url: "https://feeds.example.com/test.rss"
    enabled: true

paths:
  output_dir: "./test_output"
  database: ":memory:"

transcription:
  model: "whisper-1"
  language: "en"

llm:
  model: "anthropic/claude-opus-4-5"
  api_base: null
  temperature: 0
  max_tokens: 2048
  topic_excerpt_words: 2000

ad_detection:
  chunk_duration_sec: 300
  chunk_overlap_sec: 30
  min_confidence: 0.75
  merge_gap_sec: 5
  max_tokens_per_chunk: 6000

audio:
  output_format: "mp3"
  cbr_bitrate: "192k"

logging:
  level: "INFO"
  log_file: null

retry:
  max_attempts: 3
  backoff_factor: 2
```

**Step 2: Write the failing tests**

```python
# tests/test_config_loader.py
from pathlib import Path

import pytest


def test_load_valid_config():
    from config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    assert cfg.llm.model == "anthropic/claude-opus-4-5"
    assert cfg.llm.temperature == 0
    assert cfg.feeds[0].name == "Test Podcast"
    assert cfg.ad_detection.min_confidence == 0.75
    assert cfg.audio.output_format == "mp3"


def test_config_paths_are_path_objects():
    from config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    assert isinstance(cfg.paths.output_dir, Path)
    assert isinstance(cfg.paths.database, Path)


def test_missing_config_raises():
    from config_loader import load_config
    from pipeline.exceptions import ConfigError

    with pytest.raises(ConfigError):
        load_config(Path("nonexistent.yaml"))


def test_invalid_config_raises(tmp_path):
    from config_loader import load_config
    from pipeline.exceptions import ConfigError

    bad = tmp_path / "bad.yaml"
    bad.write_text("feeds: not_a_list\n")
    with pytest.raises((ConfigError, Exception)):
        load_config(bad)
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_loader.py -v`
Expected: FAIL — `config_loader` doesn't exist.

**Step 4: Write `config_loader.py`**

```python
import logging
from enum import StrEnum
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

from pipeline.exceptions import ConfigError

logger = logging.getLogger(__name__)


class AudioFormat(StrEnum):
    MP3 = "mp3"
    M4A = "m4a"


class FeedConfig(BaseModel, frozen=True):
    name: str
    url: str
    enabled: bool = True


class PathsConfig(BaseModel, frozen=True):
    output_dir: Path
    database: Path


class TranscriptionConfig(BaseModel, frozen=True):
    model: str
    language: str | None = "en"


class LLMConfig(BaseModel, frozen=True):
    model: str
    api_base: str | None = None
    temperature: float = 0
    max_tokens: int = 2048
    topic_excerpt_words: int = 2000


class AdDetectionConfig(BaseModel, frozen=True):
    chunk_duration_sec: int = 300
    chunk_overlap_sec: int = 30
    min_confidence: float = 0.75
    merge_gap_sec: int = 5
    max_tokens_per_chunk: int = 6000


class AudioConfig(BaseModel, frozen=True):
    output_format: AudioFormat = AudioFormat.MP3
    cbr_bitrate: str = "192k"


class LoggingConfig(BaseModel, frozen=True):
    level: str = "INFO"
    log_file: str | None = None

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
        return v.upper()


class RetryConfig(BaseModel, frozen=True):
    max_attempts: int = 3
    backoff_factor: int = 2


class AppConfig(BaseModel, frozen=True):
    feeds: list[FeedConfig]
    paths: PathsConfig
    transcription: TranscriptionConfig
    llm: LLMConfig
    ad_detection: AdDetectionConfig
    audio: AudioConfig
    logging: LoggingConfig
    retry: RetryConfig


def load_config(config_path: Path) -> AppConfig:
    """Load and validate config from YAML. Loads .env for API keys."""
    load_dotenv()

    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config must be a YAML mapping, got {type(raw).__name__}")

    # Fail fast if API keys leak into config.yaml
    _check_no_secrets(raw)

    try:
        cfg = AppConfig(**raw)
    except Exception as exc:
        raise ConfigError(f"Config validation failed: {exc}") from exc

    logger.info("Config loaded from %s", config_path)
    return cfg


def _check_no_secrets(raw: dict[str, object]) -> None:
    """Raise if any value looks like an API key."""
    secret_prefixes = ("sk-ant-", "sk-", "gsk_", "sk-or-")
    for section in raw.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, str) and any(
                value.startswith(prefix) for prefix in secret_prefixes
            ):
                raise ConfigError(
                    f"API key detected in config.yaml field '{key}'. "
                    "Move secrets to .env, not config.yaml."
                )
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config_loader.py -v`
Expected: All PASS.

**Step 6: Commit**

```bash
git add config_loader.py tests/fixtures/test_config.yaml tests/test_config_loader.py
git commit -m "feat: add config loader with Pydantic validation"
```

---

## Task 5: Database Layer

**Files:**
- Create: `db/schema.sql`
- Create: `db/migrations/001_initial.sql`
- Create: `db/connection.py`
- Create: `tests/conftest.py`
- Create: `tests/test_connection.py`

**Step 1: Create `db/schema.sql`**

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
    hosts        TEXT,
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

**Step 2: Create `db/migrations/001_initial.sql`**

Same content as `db/schema.sql` above — this is the initial migration.

**Step 3: Write the failing tests**

```python
# tests/conftest.py
from pathlib import Path

import pytest

from config_loader import AppConfig, load_config


@pytest.fixture
def app_config() -> AppConfig:
    return load_config(Path("tests/fixtures/test_config.yaml"))


@pytest.fixture
async def db_conn():
    """In-memory SQLite connection with schema applied."""
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.execute("PRAGMA foreign_keys = ON")
    schema = Path("db/schema.sql").read_text()
    await conn.executescript(schema)
    yield conn
    await conn.close()
```

```python
# tests/test_connection.py
async def test_db_connection_applies_pragmas(db_conn):
    cursor = await db_conn.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_db_tables_exist(db_conn):
    cursor = await db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    assert "episodes" in tables
    assert "transcripts" in tables
    assert "transcript_segments" in tables
    assert "topic_contexts" in tables
    assert "ad_segments" in tables
```

**Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_connection.py -v`
Expected: FAIL — schema file doesn't exist.

**Step 5: Write `db/connection.py`**

```python
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_db(db_path: Path) -> AsyncGenerator[aiosqlite.Connection, None]:
    """Open a connection, set pragmas, apply pending migrations, and yield."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await _apply_migrations(conn)
        yield conn
    finally:
        await conn.close()


async def _apply_migrations(conn: aiosqlite.Connection) -> None:
    """Apply any unapplied SQL migration files in order."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  filename TEXT NOT NULL UNIQUE,"
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    cursor = await conn.execute("SELECT filename FROM _migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    migrations_dir = Path("db/migrations")
    if not migrations_dir.exists():
        return

    for sql_file in sorted(migrations_dir.glob("*.sql")):
        if sql_file.name not in applied:
            logger.info("Applying migration: %s", sql_file.name)
            sql = sql_file.read_text()
            await conn.executescript(sql)
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES (?)", (sql_file.name,)
            )
            await conn.commit()
```

**Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_connection.py -v`
Expected: All PASS.

**Step 7: Commit**

```bash
git add db/ tests/conftest.py tests/test_connection.py
git commit -m "feat: add database schema, migrations, and connection manager"
```

---

## Task 6: Repositories

**Files:**
- Create: `db/repositories/episode_repo.py`
- Create: `db/repositories/transcript_repo.py`
- Create: `db/repositories/ad_segment_repo.py`
- Create: `db/repositories/__init__.py`
- Create: `tests/test_episode_repo.py`
- Create: `tests/test_transcript_repo.py`
- Create: `tests/test_ad_segment_repo.py`

**Step 1: Write failing tests for EpisodeRepository**

```python
# tests/test_episode_repo.py
from models.episode import Episode


async def test_upsert_and_get_by_guid(db_conn):
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    ep = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
        duration_seconds=3600,
    )
    await repo.upsert(ep)
    result = await repo.get_by_guid("ep-001")
    assert result is not None
    assert result.guid == "ep-001"
    assert result.title == "Episode 1"


async def test_get_by_guid_returns_none(db_conn):
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    result = await repo.get_by_guid("nonexistent")
    assert result is None


async def test_upsert_updates_existing(db_conn):
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    ep1 = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    await repo.upsert(ep1)
    ep2 = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1 Updated",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    await repo.upsert(ep2)
    result = await repo.get_by_guid("ep-001")
    assert result is not None
    assert result.title == "Episode 1 Updated"


async def test_list_by_feed(db_conn):
    from db.repositories.episode_repo import EpisodeRepository

    repo = EpisodeRepository(db_conn)
    for i in range(3):
        ep = Episode(
            guid=f"ep-{i}",
            feed_title="Test Pod",
            title=f"Episode {i}",
            audio_url=f"https://example.com/ep{i}.mp3",
            published="2025-01-01T00:00:00Z",
        )
        await repo.upsert(ep)
    results = await repo.list_by_feed("Test Pod")
    assert len(results) == 3
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_episode_repo.py -v`
Expected: FAIL.

**Step 3: Write `db/repositories/episode_repo.py`**

```python
import logging

import aiosqlite

from models.episode import Episode

logger = logging.getLogger(__name__)


class EpisodeRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert(self, episode: Episode) -> None:
        await self._conn.execute(
            "INSERT INTO episodes (guid, feed_name, title, audio_url, published_at, duration_sec)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(guid) DO UPDATE SET"
            "   title = excluded.title,"
            "   audio_url = excluded.audio_url,"
            "   published_at = excluded.published_at,"
            "   duration_sec = excluded.duration_sec",
            (
                episode.guid,
                episode.feed_title,
                episode.title,
                str(episode.audio_url),
                episode.published.isoformat(),
                episode.duration_seconds,
            ),
        )
        await self._conn.commit()
        logger.debug("Upserted episode guid=%s", episode.guid)

    async def get_by_guid(self, guid: str) -> Episode | None:
        cursor = await self._conn.execute(
            "SELECT guid, feed_name, title, audio_url, published_at, duration_sec"
            " FROM episodes WHERE guid = ?",
            (guid,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Episode(
            guid=row[0],
            feed_title=row[1],
            title=row[2],
            audio_url=row[3],
            published=row[4],
            duration_seconds=row[5],
        )

    async def list_by_feed(self, feed_name: str) -> list[Episode]:
        cursor = await self._conn.execute(
            "SELECT guid, feed_name, title, audio_url, published_at, duration_sec"
            " FROM episodes WHERE feed_name = ? ORDER BY published_at DESC",
            (feed_name,),
        )
        rows = await cursor.fetchall()
        return [
            Episode(
                guid=r[0],
                feed_title=r[1],
                title=r[2],
                audio_url=r[3],
                published=r[4],
                duration_seconds=r[5],
            )
            for r in rows
        ]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_episode_repo.py -v`
Expected: All PASS.

**Step 5: Write failing tests for TranscriptRepository**

```python
# tests/test_transcript_repo.py
from models.episode import Episode
from models.transcript import Segment, Transcript


async def _insert_episode(db_conn) -> None:
    """Helper to satisfy foreign key."""
    await db_conn.execute(
        "INSERT INTO episodes (guid, feed_name, title, audio_url, published_at)"
        " VALUES ('ep-001', 'Test', 'Ep 1', 'https://example.com/ep1.mp3', '2025-01-01')"
    )
    await db_conn.commit()


async def test_save_and_get_transcript(db_conn):
    from db.repositories.transcript_repo import TranscriptRepository

    await _insert_episode(db_conn)
    repo = TranscriptRepository(db_conn)
    transcript = Transcript(
        episode_guid="ep-001",
        segments=(
            Segment(start_ms=0, end_ms=5000, text="Hello world"),
            Segment(start_ms=5000, end_ms=10000, text="Welcome back"),
        ),
        full_text="Hello world Welcome back",
        language="en",
        provider_model="whisper-1",
    )
    await repo.save(transcript)
    result = await repo.get_by_episode_guid("ep-001")
    assert result is not None
    assert result.episode_guid == "ep-001"
    assert len(result.segments) == 2
    assert result.segments[0].text == "Hello world"
    assert result.full_text == "Hello world Welcome back"


async def test_get_returns_none_for_missing(db_conn):
    from db.repositories.transcript_repo import TranscriptRepository

    repo = TranscriptRepository(db_conn)
    result = await repo.get_by_episode_guid("nonexistent")
    assert result is None


async def test_delete_transcript(db_conn):
    from db.repositories.transcript_repo import TranscriptRepository

    await _insert_episode(db_conn)
    repo = TranscriptRepository(db_conn)
    transcript = Transcript(
        episode_guid="ep-001",
        segments=(Segment(start_ms=0, end_ms=5000, text="Hello"),),
        full_text="Hello",
        language="en",
        provider_model="whisper-1",
    )
    await repo.save(transcript)
    await repo.delete("ep-001")
    result = await repo.get_by_episode_guid("ep-001")
    assert result is None
```

**Step 6: Write `db/repositories/transcript_repo.py`**

```python
import logging

import aiosqlite

from models.transcript import Segment, Transcript

logger = logging.getLogger(__name__)


class TranscriptRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save(self, transcript: Transcript) -> None:
        cursor = await self._conn.execute(
            "INSERT INTO transcripts (episode_guid, language, full_text, provider_model)"
            " VALUES (?, ?, ?, ?)",
            (
                transcript.episode_guid,
                transcript.language,
                transcript.full_text,
                transcript.provider_model,
            ),
        )
        transcript_id = cursor.lastrowid
        for seg in transcript.segments:
            await self._conn.execute(
                "INSERT INTO transcript_segments (transcript_id, start_ms, end_ms, text)"
                " VALUES (?, ?, ?, ?)",
                (transcript_id, seg.start_ms, seg.end_ms, seg.text),
            )
        await self._conn.commit()
        logger.debug("Saved transcript for episode_guid=%s", transcript.episode_guid)

    async def get_by_episode_guid(self, episode_guid: str) -> Transcript | None:
        cursor = await self._conn.execute(
            "SELECT id, language, full_text, provider_model"
            " FROM transcripts WHERE episode_guid = ?",
            (episode_guid,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        transcript_id, language, full_text, provider_model = row
        seg_cursor = await self._conn.execute(
            "SELECT start_ms, end_ms, text"
            " FROM transcript_segments WHERE transcript_id = ? ORDER BY start_ms",
            (transcript_id,),
        )
        seg_rows = await seg_cursor.fetchall()
        segments = tuple(
            Segment(start_ms=r[0], end_ms=r[1], text=r[2]) for r in seg_rows
        )
        return Transcript(
            episode_guid=episode_guid,
            segments=segments,
            full_text=full_text,
            language=language,
            provider_model=provider_model,
        )

    async def delete(self, episode_guid: str) -> None:
        await self._conn.execute(
            "DELETE FROM transcripts WHERE episode_guid = ?", (episode_guid,)
        )
        await self._conn.commit()
        logger.debug("Deleted transcript for episode_guid=%s", episode_guid)
```

**Step 7: Run transcript repo tests**

Run: `uv run pytest tests/test_transcript_repo.py -v`
Expected: All PASS.

**Step 8: Write failing tests for AdSegmentRepository**

```python
# tests/test_ad_segment_repo.py
from models.ad_segment import AdSegment


async def _insert_episode(db_conn) -> None:
    await db_conn.execute(
        "INSERT INTO episodes (guid, feed_name, title, audio_url, published_at)"
        " VALUES ('ep-001', 'Test', 'Ep 1', 'https://example.com/ep1.mp3', '2025-01-01')"
    )
    await db_conn.commit()


async def test_save_all_and_get_by_episode(db_conn):
    from db.repositories.ad_segment_repo import AdSegmentRepository

    await _insert_episode(db_conn)
    repo = AdSegmentRepository(db_conn)
    segments = [
        AdSegment(
            episode_guid="ep-001",
            start_ms=60000,
            end_ms=120000,
            confidence=0.9,
            reason="Promo code",
            sponsor_name="Acme",
        ),
        AdSegment(
            episode_guid="ep-001",
            start_ms=300000,
            end_ms=360000,
            confidence=0.8,
            reason="Sponsor mention",
        ),
    ]
    await repo.save_all(segments)
    results = await repo.get_by_episode("ep-001")
    assert len(results) == 2
    assert results[0].start_ms == 60000
    assert results[1].sponsor_name is None


async def test_mark_cut(db_conn):
    from db.repositories.ad_segment_repo import AdSegmentRepository

    await _insert_episode(db_conn)
    repo = AdSegmentRepository(db_conn)
    segments = [
        AdSegment(
            episode_guid="ep-001",
            start_ms=60000,
            end_ms=120000,
            confidence=0.9,
            reason="Promo code",
        ),
    ]
    await repo.save_all(segments)
    results = await repo.get_by_episode("ep-001")
    assert results[0].was_cut is False
    await repo.mark_cut("ep-001")
    results = await repo.get_by_episode("ep-001")
    assert results[0].was_cut is True
```

**Step 9: Write `db/repositories/ad_segment_repo.py`**

```python
import logging

import aiosqlite

from models.ad_segment import AdSegment

logger = logging.getLogger(__name__)


class AdSegmentRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_all(self, segments: list[AdSegment]) -> None:
        for seg in segments:
            await self._conn.execute(
                "INSERT INTO ad_segments"
                " (episode_guid, start_ms, end_ms, confidence, reason, sponsor_name, was_cut)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    seg.episode_guid,
                    seg.start_ms,
                    seg.end_ms,
                    seg.confidence,
                    seg.reason,
                    seg.sponsor_name,
                    int(seg.was_cut),
                ),
            )
        await self._conn.commit()
        logger.debug("Saved %d ad segments for episode_guid=%s", len(segments), segments[0].episode_guid if segments else "?")

    async def get_by_episode(self, episode_guid: str) -> list[AdSegment]:
        cursor = await self._conn.execute(
            "SELECT episode_guid, start_ms, end_ms, confidence, reason, sponsor_name, was_cut"
            " FROM ad_segments WHERE episode_guid = ? ORDER BY start_ms",
            (episode_guid,),
        )
        rows = await cursor.fetchall()
        return [
            AdSegment(
                episode_guid=r[0],
                start_ms=r[1],
                end_ms=r[2],
                confidence=r[3],
                reason=r[4],
                sponsor_name=r[5],
                was_cut=bool(r[6]),
            )
            for r in rows
        ]

    async def mark_cut(self, episode_guid: str) -> None:
        await self._conn.execute(
            "UPDATE ad_segments SET was_cut = 1 WHERE episode_guid = ?",
            (episode_guid,),
        )
        await self._conn.commit()
        logger.debug("Marked ad segments as cut for episode_guid=%s", episode_guid)
```

**Step 10: Update `db/repositories/__init__.py`**

```python
from db.repositories.ad_segment_repo import AdSegmentRepository
from db.repositories.episode_repo import EpisodeRepository
from db.repositories.transcript_repo import TranscriptRepository

__all__ = ["AdSegmentRepository", "EpisodeRepository", "TranscriptRepository"]
```

**Step 11: Run all repo tests**

Run: `uv run pytest tests/test_episode_repo.py tests/test_transcript_repo.py tests/test_ad_segment_repo.py -v`
Expected: All PASS.

**Step 12: Commit**

```bash
git add db/repositories/ tests/test_episode_repo.py tests/test_transcript_repo.py tests/test_ad_segment_repo.py
git commit -m "feat: add repository layer for episodes, transcripts, and ad segments"
```

---

## Task 7: LLM Client

**Files:**
- Create: `pipeline/llm_client.py`
- Create: `tests/test_llm_client.py`

**Step 1: Write failing tests**

```python
# tests/test_llm_client.py
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config_loader import LLMConfig, TranscriptionConfig


@pytest.fixture
def llm_config():
    return LLMConfig(model="anthropic/claude-opus-4-5", temperature=0, max_tokens=2048)


@pytest.fixture
def transcription_config():
    return TranscriptionConfig(model="whisper-1", language="en")


async def test_complete_returns_text(llm_config):
    from pipeline.llm_client import complete

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello world"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        mock_litellm.APIError = Exception
        result = await complete(
            [{"role": "user", "content": "Hi"}],
            llm_config,
        )
    assert result == "Hello world"


async def test_complete_raises_llm_error_on_api_error(llm_config):
    from pipeline.exceptions import LLMError
    from pipeline.llm_client import complete

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.APIError = type("APIError", (Exception,), {})
        mock_litellm.acompletion = AsyncMock(side_effect=mock_litellm.APIError("fail"))
        with pytest.raises(LLMError):
            await complete(
                [{"role": "user", "content": "Hi"}],
                llm_config,
            )


async def test_transcribe_returns_dict(transcription_config, tmp_path):
    from pipeline.llm_client import transcribe

    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"fake audio data")

    mock_result = {"words": [{"word": "hello", "start": 0.0, "end": 0.5}]}

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.atranscription = AsyncMock(return_value=mock_result)
        mock_litellm.APIError = Exception
        result = await transcribe(audio_file, transcription_config)
    assert "words" in result
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_client.py -v`
Expected: FAIL.

**Step 3: Write `pipeline/llm_client.py`**

```python
import logging
from pathlib import Path
from typing import Any

import litellm
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from config_loader import LLMConfig, TranscriptionConfig
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
    cfg: LLMConfig,
    *,
    response_format: dict[str, str] | None = None,
) -> str:
    """Return the text of the first completion choice."""
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
            result = await litellm.atranscription(
                model=cfg.model,
                file=f,
                language=cfg.language,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        except litellm.APIError as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

    logger.info("Transcription complete segments=%d", len(result.get("words", [])))
    return result  # type: ignore[no-any-return]
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_client.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add pipeline/llm_client.py tests/test_llm_client.py
git commit -m "feat: add LLM client with retry logic and transcription support"
```

---

## Task 8: RSS Feed Parser

**Files:**
- Create: `pipeline/rss.py`
- Create: `tests/fixtures/sample_feed.xml`
- Create: `tests/test_rss.py`

**Step 1: Create `tests/fixtures/sample_feed.xml`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel>
  <title>Test Podcast</title>
  <item>
    <title>Episode 42: The Answer</title>
    <guid>ep-042</guid>
    <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
    <enclosure url="https://example.com/ep42.mp3" length="50000000" type="audio/mpeg"/>
  </item>
  <item>
    <title>Episode 41: Media Content</title>
    <guid>ep-041</guid>
    <pubDate>Tue, 31 Dec 2024 00:00:00 GMT</pubDate>
    <media:content url="https://example.com/ep41.mp3" type="audio/mpeg"/>
  </item>
  <item>
    <title>Episode 40: No Audio</title>
    <guid>ep-040</guid>
    <pubDate>Mon, 30 Dec 2024 00:00:00 GMT</pubDate>
  </item>
</channel>
</rss>
```

**Step 2: Write failing tests**

```python
# tests/test_rss.py
from pathlib import Path

import pytest


def test_parse_feed_with_enclosure():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    enclosure_ep = [e for e in episodes if e.guid == "ep-042"]
    assert len(enclosure_ep) == 1
    assert str(enclosure_ep[0].audio_url) == "https://example.com/ep42.mp3"


def test_parse_feed_with_media_content():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    media_ep = [e for e in episodes if e.guid == "ep-041"]
    assert len(media_ep) == 1
    assert str(media_ep[0].audio_url) == "https://example.com/ep41.mp3"


def test_parse_feed_skips_items_without_audio():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    guids = [e.guid for e in episodes]
    assert "ep-040" not in guids


def test_parse_feed_returns_sorted_by_date():
    from pipeline.rss import parse_feed

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    episodes = parse_feed(xml, feed_name="Test Podcast")
    assert episodes[0].guid == "ep-042"  # newest first


async def test_fetch_latest_episode():
    import httpx
    import respx

    from config_loader import FeedConfig
    from pipeline.rss import fetch_latest_episode

    xml = Path("tests/fixtures/sample_feed.xml").read_text()
    feed_cfg = FeedConfig(name="Test Podcast", url="https://feeds.example.com/test.rss")

    with respx.mock:
        respx.get("https://feeds.example.com/test.rss").respond(200, text=xml)
        async with httpx.AsyncClient() as client:
            ep = await fetch_latest_episode(feed_cfg, client=client)
    assert ep is not None
    assert ep.guid == "ep-042"
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_rss.py -v`
Expected: FAIL.

**Step 4: Write `pipeline/rss.py`**

```python
import logging
from datetime import datetime, timezone

import feedparser
import httpx

from config_loader import FeedConfig
from models.episode import Episode
from pipeline.exceptions import FeedFetchError

logger = logging.getLogger(__name__)


def parse_feed(xml: str, *, feed_name: str) -> list[Episode]:
    """Parse RSS XML and return episodes with audio URLs, newest first."""
    feed = feedparser.parse(xml)
    episodes: list[Episode] = []

    for entry in feed.entries:
        audio_url = _extract_audio_url(entry)
        if audio_url is None:
            logger.debug("Skipping entry %s — no audio URL", entry.get("id", "unknown"))
            continue

        guid = entry.get("id") or entry.get("guid", "")
        title = entry.get("title", "Untitled")
        published = _parse_date(entry)

        episodes.append(
            Episode(
                guid=guid,
                feed_title=feed_name,
                title=title,
                audio_url=audio_url,
                published=published,
            )
        )

    episodes.sort(key=lambda e: e.published, reverse=True)
    logger.info("Parsed %d episodes from feed '%s'", len(episodes), feed_name)
    return episodes


def _extract_audio_url(entry: feedparser.FeedParserDict) -> str | None:
    """Extract audio URL from <enclosure> or <media:content>."""
    for link in entry.get("links", []):
        if link.get("rel") == "enclosure" and "audio" in link.get("type", ""):
            return link["href"]  # type: ignore[no-any-return]

    for enclosure in entry.get("enclosures", []):
        if "audio" in enclosure.get("type", ""):
            return enclosure["href"]  # type: ignore[no-any-return]

    media = entry.get("media_content", [])
    for m in media:
        if "audio" in m.get("type", ""):
            return m["url"]  # type: ignore[no-any-return]

    return None


def _parse_date(entry: feedparser.FeedParserDict) -> datetime:
    """Parse the published date from a feed entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        from time import mktime

        return datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
    return datetime.now(tz=timezone.utc)


async def fetch_latest_episode(
    feed_cfg: FeedConfig,
    *,
    client: httpx.AsyncClient | None = None,
) -> Episode | None:
    """Fetch the RSS feed and return the most recent episode."""
    logger.info("Fetching feed: %s", feed_cfg.name)
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)

    try:
        response = await client.get(feed_cfg.url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise FeedFetchError(f"Failed to fetch feed '{feed_cfg.name}': {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    episodes = parse_feed(response.text, feed_name=feed_cfg.name)
    if not episodes:
        logger.warning("No episodes found in feed '%s'", feed_cfg.name)
        return None

    logger.info("Latest episode: %s", episodes[0].title)
    return episodes[0]
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_rss.py -v`
Expected: All PASS.

**Step 6: Commit**

```bash
git add pipeline/rss.py tests/fixtures/sample_feed.xml tests/test_rss.py
git commit -m "feat: add RSS feed parser with enclosure and media:content support"
```

---

## Task 9: Audio Downloader

**Files:**
- Create: `pipeline/downloader.py`
- Create: `tests/test_downloader.py`

**Step 1: Write failing tests**

```python
# tests/test_downloader.py
import httpx
import respx

from models.episode import Episode


@respx.mock
async def test_download_episode(tmp_path):
    from pipeline.downloader import download_episode

    ep = Episode(
        guid="ep-001",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    audio_bytes = b"\xff\xfb\x90\x00" * 1000  # fake MP3 bytes
    respx.get("https://example.com/ep1.mp3").respond(200, content=audio_bytes)

    async with httpx.AsyncClient() as client:
        path = await download_episode(ep, output_dir=tmp_path, client=client)

    assert path.exists()
    assert path.stat().st_size == len(audio_bytes)
    assert path.suffix == ".mp3"


@respx.mock
async def test_download_follows_redirect(tmp_path):
    from pipeline.downloader import download_episode

    ep = Episode(
        guid="ep-002",
        feed_title="Test Pod",
        title="Episode 2",
        audio_url="https://example.com/ep2.mp3",
        published="2025-01-01T00:00:00Z",
    )
    audio_bytes = b"\xff\xfb\x90\x00" * 500
    respx.get("https://example.com/ep2.mp3").respond(
        302, headers={"Location": "https://cdn.example.com/ep2.mp3"}
    )
    respx.get("https://cdn.example.com/ep2.mp3").respond(200, content=audio_bytes)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        path = await download_episode(ep, output_dir=tmp_path, client=client)

    assert path.exists()
    assert path.stat().st_size == len(audio_bytes)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_downloader.py -v`
Expected: FAIL.

**Step 3: Write `pipeline/downloader.py`**

```python
import hashlib
import logging
import re
from pathlib import Path

import httpx

from models.episode import Episode
from pipeline.exceptions import DownloadError

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:80]


async def download_episode(
    episode: Episode,
    *,
    output_dir: Path,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Stream-download the episode audio and return the local file path."""
    slug = _slugify(episode.title)
    suffix = Path(str(episode.audio_url)).suffix or ".mp3"
    episode_dir = output_dir / slug
    episode_dir.mkdir(parents=True, exist_ok=True)
    dest = episode_dir / f"original{suffix}"

    if dest.exists() and dest.stat().st_size > 0:
        logger.info("Already downloaded: %s", dest)
        return dest

    logger.info("Downloading %s → %s", episode.audio_url, dest)
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)

    hasher = hashlib.sha256()
    try:
        async with client.stream("GET", str(episode.audio_url)) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    hasher.update(chunk)
    except httpx.HTTPError as exc:
        logger.error("Download failed for %s: %s", episode.guid, exc)
        raise DownloadError(f"Download failed: {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    file_size = dest.stat().st_size
    sha256 = hasher.hexdigest()
    logger.info(
        "Download complete: %s (%d bytes, SHA-256=%s)", dest.name, file_size, sha256[:16]
    )

    if file_size == 0:
        dest.unlink()
        raise DownloadError(f"Downloaded file is empty: {dest}")

    return dest
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_downloader.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add pipeline/downloader.py tests/test_downloader.py
git commit -m "feat: add streaming audio downloader with SHA-256 verification"
```

---

## Task 10: Transcriber

**Files:**
- Create: `pipeline/transcriber.py`
- Create: `tests/test_transcriber.py`

**Step 1: Write failing tests**

```python
# tests/test_transcriber.py
from pathlib import Path
from unittest.mock import AsyncMock, patch

from config_loader import TranscriptionConfig
from models.episode import Episode


@patch("pipeline.transcriber.llm_client")
async def test_transcribe_episode(mock_llm_client):
    from pipeline.transcriber import transcribe_episode

    mock_llm_client.transcribe = AsyncMock(
        return_value={
            "text": "Hello world. Welcome back to the show.",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 0.3},
                {"word": "world.", "start": 0.3, "end": 0.6},
                {"word": "Welcome", "start": 0.7, "end": 1.0},
                {"word": "back", "start": 1.0, "end": 1.2},
                {"word": "to", "start": 1.2, "end": 1.3},
                {"word": "the", "start": 1.3, "end": 1.4},
                {"word": "show.", "start": 1.4, "end": 1.7},
            ],
        }
    )

    cfg = TranscriptionConfig(model="whisper-1", language="en")
    ep = Episode(
        guid="ep-001",
        feed_title="Test",
        title="Ep 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )

    transcript = await transcribe_episode(
        episode=ep, audio_path=Path("/fake/audio.mp3"), cfg=cfg
    )
    assert transcript.episode_guid == "ep-001"
    assert len(transcript.segments) > 0
    assert transcript.language == "en"
    assert "Hello" in transcript.full_text


@patch("pipeline.transcriber.llm_client")
async def test_transcribe_groups_words_into_segments(mock_llm_client):
    from pipeline.transcriber import transcribe_episode

    # Create words spanning multiple seconds to test segment grouping
    words = []
    for i in range(20):
        words.append({"word": f"word{i}", "start": i * 0.5, "end": (i + 1) * 0.5})

    mock_llm_client.transcribe = AsyncMock(
        return_value={
            "text": " ".join(f"word{i}" for i in range(20)),
            "words": words,
        }
    )

    cfg = TranscriptionConfig(model="whisper-1", language="en")
    ep = Episode(
        guid="ep-002",
        feed_title="Test",
        title="Ep 2",
        audio_url="https://example.com/ep2.mp3",
        published="2025-01-01T00:00:00Z",
    )

    transcript = await transcribe_episode(
        episode=ep, audio_path=Path("/fake/audio.mp3"), cfg=cfg
    )
    assert len(transcript.segments) >= 1
    # Segments should be sorted and non-overlapping
    for i in range(len(transcript.segments) - 1):
        assert transcript.segments[i].end_ms <= transcript.segments[i + 1].start_ms
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: FAIL.

**Step 3: Write `pipeline/transcriber.py`**

```python
import logging
from pathlib import Path

from config_loader import TranscriptionConfig
from models.episode import Episode
from models.transcript import Segment, Transcript
from pipeline import llm_client

logger = logging.getLogger(__name__)

_SEGMENT_DURATION_MS = 30_000  # Group words into ~30s segments


async def transcribe_episode(
    *,
    episode: Episode,
    audio_path: Path,
    cfg: TranscriptionConfig,
) -> Transcript:
    """Transcribe an audio file and return a Transcript with word-grouped segments."""
    logger.info("Starting transcription for %s", episode.guid)
    result = await llm_client.transcribe(audio_path, cfg)

    full_text: str = result.get("text", "")
    words: list[dict[str, object]] = result.get("words", [])

    segments = _group_words_into_segments(words)
    logger.info("Transcription saved: %d segments", len(segments))

    return Transcript(
        episode_guid=episode.guid,
        segments=tuple(segments),
        full_text=full_text,
        language=cfg.language or "en",
        provider_model=cfg.model,
    )


def _group_words_into_segments(words: list[dict[str, object]]) -> list[Segment]:
    """Group consecutive words into segments of ~SEGMENT_DURATION_MS."""
    if not words:
        return []

    segments: list[Segment] = []
    current_words: list[str] = []
    segment_start_ms: int | None = None

    for word_data in words:
        word = str(word_data.get("word", ""))
        start_sec = float(word_data.get("start", 0))
        end_sec = float(word_data.get("end", 0))
        start_ms = int(start_sec * 1000)
        end_ms = int(end_sec * 1000)

        if segment_start_ms is None:
            segment_start_ms = start_ms

        current_words.append(word)

        if end_ms - segment_start_ms >= _SEGMENT_DURATION_MS:
            if end_ms > segment_start_ms:
                segments.append(
                    Segment(
                        start_ms=segment_start_ms,
                        end_ms=end_ms,
                        text=" ".join(current_words),
                    )
                )
            current_words = []
            segment_start_ms = None

    # Flush remaining words
    if current_words and segment_start_ms is not None:
        last_end_sec = float(words[-1].get("end", 0))
        last_end_ms = int(last_end_sec * 1000)
        if last_end_ms > segment_start_ms:
            segments.append(
                Segment(
                    start_ms=segment_start_ms,
                    end_ms=last_end_ms,
                    text=" ".join(current_words),
                )
            )

    return segments
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_transcriber.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add pipeline/transcriber.py tests/test_transcriber.py
git commit -m "feat: add transcriber with word-level timestamp grouping"
```

---

## Task 11: Topic Extractor

**Files:**
- Create: `pipeline/topic_extractor.py`
- Create: `tests/test_topic_extractor.py`

**Step 1: Write failing tests**

```python
# tests/test_topic_extractor.py
import json
from unittest.mock import AsyncMock, patch

from config_loader import LLMConfig
from models.transcript import Segment, Transcript


@patch("pipeline.topic_extractor.llm_client")
async def test_extract_topic(mock_llm_client):
    from pipeline.topic_extractor import extract_topic

    mock_llm_client.complete = AsyncMock(
        return_value=json.dumps(
            {
                "domain": "technology",
                "topic": "Rust programming language",
                "hosts": ["Alice", "Bob"],
                "notes": "Weekly deep dive into systems programming",
            }
        )
    )

    cfg = LLMConfig(model="anthropic/claude-opus-4-5", topic_excerpt_words=2000)
    transcript = Transcript(
        episode_guid="ep-001",
        segments=(Segment(start_ms=0, end_ms=5000, text="Today we talk about Rust"),),
        full_text="Today we talk about Rust " * 100,
        language="en",
        provider_model="whisper-1",
    )

    topic = await extract_topic(transcript=transcript, cfg=cfg)
    assert topic.domain == "technology"
    assert topic.topic == "Rust programming language"
    assert topic.hosts == ("Alice", "Bob")


@patch("pipeline.topic_extractor.llm_client")
async def test_extract_topic_truncates_long_transcript(mock_llm_client):
    from pipeline.topic_extractor import extract_topic

    mock_llm_client.complete = AsyncMock(
        return_value=json.dumps(
            {
                "domain": "science",
                "topic": "Quantum computing",
                "hosts": ["Carol"],
                "notes": "",
            }
        )
    )

    cfg = LLMConfig(model="anthropic/claude-opus-4-5", topic_excerpt_words=10)
    long_text = "word " * 1000
    transcript = Transcript(
        episode_guid="ep-002",
        segments=(Segment(start_ms=0, end_ms=5000, text="test"),),
        full_text=long_text,
        language="en",
        provider_model="whisper-1",
    )

    topic = await extract_topic(transcript=transcript, cfg=cfg)
    assert topic.domain == "science"
    # Verify the LLM was called with truncated text
    call_args = mock_llm_client.complete.call_args
    messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
    user_msg = [m for m in messages if m["role"] == "user"][0]["content"]
    word_count = len(user_msg.split())
    assert word_count < 100  # much less than 1000
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_topic_extractor.py -v`
Expected: FAIL.

**Step 3: Write `pipeline/topic_extractor.py`**

```python
import json
import logging

from config_loader import LLMConfig
from models.ad_segment import TopicContext
from models.transcript import Transcript
from pipeline import llm_client

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Analyze the opening of this podcast transcript.\n"
    "Return only a JSON object — no markdown, no preamble.\n"
    'Schema: {"domain": str, "topic": str, "hosts": list[str], "notes": str}'
)


async def extract_topic(
    *,
    transcript: Transcript,
    cfg: LLMConfig,
) -> TopicContext:
    """Extract topic context from the beginning of a transcript."""
    excerpt = _truncate_words(transcript.full_text, cfg.topic_excerpt_words)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"<transcript>{excerpt}</transcript>"},
    ]

    response = await llm_client.complete(messages, cfg)

    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("Topic extraction returned invalid JSON, using defaults")
        data = {"domain": "unknown", "topic": "unknown", "hosts": [], "notes": ""}

    topic = TopicContext(
        domain=data.get("domain", "unknown"),
        topic=data.get("topic", "unknown"),
        hosts=tuple(data.get("hosts", [])),
        notes=data.get("notes", ""),
    )

    logger.info("Topic extracted: domain=%s topic=%s", topic.domain, topic.topic)
    return topic


def _truncate_words(text: str, max_words: int) -> str:
    """Return the first max_words words of text."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_topic_extractor.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add pipeline/topic_extractor.py tests/test_topic_extractor.py
git commit -m "feat: add topic extractor with LLM-based analysis"
```

---

## Task 12: Ad Detector

**Files:**
- Create: `pipeline/ad_detector.py`
- Create: `tests/test_ad_detector.py`

**Step 1: Write failing tests**

```python
# tests/test_ad_detector.py
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from config_loader import AdDetectionConfig, LLMConfig, load_config
from models.ad_segment import AdSegment, TopicContext
from models.transcript import Segment, Transcript


def _make_transcript(duration_sec: int = 600) -> Transcript:
    """Create a transcript with segments spanning duration_sec."""
    segments = []
    for i in range(0, duration_sec, 5):
        segments.append(
            Segment(start_ms=i * 1000, end_ms=(i + 5) * 1000, text=f"Word at {i}s. ")
        )
    return Transcript(
        episode_guid="ep-001",
        segments=tuple(segments),
        full_text=" ".join(f"Word at {i}s." for i in range(0, duration_sec, 5)),
        language="en",
        provider_model="whisper-1",
    )


def _make_topic() -> TopicContext:
    return TopicContext(
        domain="technology",
        topic="Rust programming",
        hosts=("Alice", "Bob"),
        notes="Weekly deep dive",
    )


@patch("pipeline.ad_detector.llm_client")
async def test_detect_ads_returns_segments(mock_llm_client):
    from pipeline.ad_detector import detect_ads

    mock_llm_client.complete = AsyncMock(
        return_value=json.dumps(
            [
                {
                    "start_sec": 60.0,
                    "end_sec": 120.0,
                    "confidence": 0.95,
                    "reason": "Promo code mentioned",
                    "sponsor": "Acme Corp",
                }
            ]
        )
    )

    transcript = _make_transcript(600)
    topic = _make_topic()
    llm_cfg = LLMConfig(model="anthropic/claude-opus-4-5")
    ad_cfg = AdDetectionConfig()

    results = await detect_ads(
        transcript=transcript, topic=topic, llm_cfg=llm_cfg, ad_cfg=ad_cfg
    )
    assert len(results) >= 1
    assert all(isinstance(s, AdSegment) for s in results)


@patch("pipeline.ad_detector.llm_client")
async def test_detect_ads_handles_empty_response(mock_llm_client):
    from pipeline.ad_detector import detect_ads

    mock_llm_client.complete = AsyncMock(return_value="[]")

    transcript = _make_transcript(300)
    topic = _make_topic()
    llm_cfg = LLMConfig(model="anthropic/claude-opus-4-5")
    ad_cfg = AdDetectionConfig()

    results = await detect_ads(
        transcript=transcript, topic=topic, llm_cfg=llm_cfg, ad_cfg=ad_cfg
    )
    assert results == []


@patch("pipeline.ad_detector.llm_client")
async def test_detect_ads_skips_malformed_json(mock_llm_client):
    from pipeline.ad_detector import detect_ads

    mock_llm_client.complete = AsyncMock(return_value="not valid json")

    transcript = _make_transcript(300)
    topic = _make_topic()
    llm_cfg = LLMConfig(model="anthropic/claude-opus-4-5")
    ad_cfg = AdDetectionConfig()

    results = await detect_ads(
        transcript=transcript, topic=topic, llm_cfg=llm_cfg, ad_cfg=ad_cfg
    )
    assert results == []


def test_merge_adjacent_segments():
    from pipeline.ad_detector import _merge_segments

    segments = [
        AdSegment(episode_guid="ep-001", start_ms=60000, end_ms=120000, confidence=0.9, reason="Ad 1"),
        AdSegment(episode_guid="ep-001", start_ms=123000, end_ms=180000, confidence=0.85, reason="Ad 2"),
    ]
    merged = _merge_segments(segments, merge_gap_ms=5000)
    assert len(merged) == 1
    assert merged[0].start_ms == 60000
    assert merged[0].end_ms == 180000


def test_merge_does_not_merge_distant_segments():
    from pipeline.ad_detector import _merge_segments

    segments = [
        AdSegment(episode_guid="ep-001", start_ms=60000, end_ms=120000, confidence=0.9, reason="Ad 1"),
        AdSegment(episode_guid="ep-001", start_ms=300000, end_ms=360000, confidence=0.85, reason="Ad 2"),
    ]
    merged = _merge_segments(segments, merge_gap_ms=5000)
    assert len(merged) == 2
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ad_detector.py -v`
Expected: FAIL.

**Step 3: Write `pipeline/ad_detector.py`**

```python
import asyncio
import json
import logging

from config_loader import AdDetectionConfig, LLMConfig
from models.ad_segment import AdSegment, TopicContext
from models.transcript import Segment, Transcript
from pipeline import llm_client

logger = logging.getLogger(__name__)

_LLM_SEMAPHORE = asyncio.Semaphore(3)

_SYSTEM_PROMPT = (
    "Identify advertisements in this podcast transcript segment.\n"
    "An ad is any span where the host promotes a product, service, or sponsor.\n"
    "Exclude brand mentions that are naturally part of the episode content.\n"
    "Return only a JSON array — no markdown, no preamble.\n"
    'Schema: [{"start_sec": float, "end_sec": float, "confidence": float, '
    '"reason": str, "sponsor": str | null}]\n'
    "Return [] if no ads are found."
)


type TranscriptChunk = list[Segment]


async def detect_ads(
    *,
    transcript: Transcript,
    topic: TopicContext,
    llm_cfg: LLMConfig,
    ad_cfg: AdDetectionConfig,
) -> list[AdSegment]:
    """Detect ad segments in a transcript using chunked LLM analysis."""
    chunks = _split_into_chunks(
        list(transcript.segments),
        chunk_duration_ms=ad_cfg.chunk_duration_sec * 1000,
        overlap_ms=ad_cfg.chunk_overlap_sec * 1000,
    )
    logger.info("Detecting ads in %d chunks", len(chunks))

    results: list[list[AdSegment]] = [[] for _ in chunks]

    async with asyncio.TaskGroup() as tg:
        for i, chunk in enumerate(chunks):
            tg.create_task(
                _detect_chunk(
                    chunk=chunk,
                    context=topic,
                    llm_cfg=llm_cfg,
                    episode_guid=transcript.episode_guid,
                    results=results,
                    index=i,
                    total=len(chunks),
                )
            )

    all_segments = [s for batch in results for s in batch]
    merged = _merge_segments(all_segments, merge_gap_ms=ad_cfg.merge_gap_sec * 1000)

    above_threshold = [s for s in merged if s.confidence >= ad_cfg.min_confidence]
    below_threshold = [s for s in merged if s.confidence < ad_cfg.min_confidence]

    for seg in below_threshold:
        logger.warning(
            "Low-confidence ad segment skipped: %d–%dms confidence=%.2f reason=%s",
            seg.start_ms,
            seg.end_ms,
            seg.confidence,
            seg.reason,
        )

    logger.info(
        "Ad detection complete: %d detected, %d above threshold",
        len(merged),
        len(above_threshold),
    )
    return above_threshold


async def _detect_chunk(
    *,
    chunk: TranscriptChunk,
    context: TopicContext,
    llm_cfg: LLMConfig,
    episode_guid: str,
    results: list[list[AdSegment]],
    index: int,
    total: int,
) -> None:
    """Detect ads in a single chunk, writing results to results[index]."""
    chunk_start = chunk[0].start_ms / 1000 if chunk else 0
    chunk_end = chunk[-1].end_ms / 1000 if chunk else 0
    logger.debug("Chunk %d/%d: %.1f–%.1fs", index + 1, total, chunk_start, chunk_end)

    chunk_text = "\n".join(
        f"[{s.start_ms / 1000:.1f}s] {s.text}" for s in chunk
    )

    context_str = f"Domain: {context.domain}, Topic: {context.topic}, Hosts: {', '.join(context.hosts)}"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Episode context: {context_str}\n\n"
                f"Transcript (timestamps in seconds):\n"
                f"<transcript>{chunk_text}</transcript>"
            ),
        },
    ]

    async with _LLM_SEMAPHORE:
        response = await llm_client.complete(messages, llm_cfg)

    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("Skipping chunk %d: invalid JSON response", index + 1)
        return

    if not isinstance(data, list):
        logger.warning("Skipping chunk %d: response is not a JSON array", index + 1)
        return

    segments: list[AdSegment] = []
    for item in data:
        try:
            segments.append(
                AdSegment(
                    episode_guid=episode_guid,
                    start_ms=int(float(item["start_sec"]) * 1000),
                    end_ms=int(float(item["end_sec"]) * 1000),
                    confidence=float(item["confidence"]),
                    reason=str(item.get("reason", "")),
                    sponsor_name=item.get("sponsor"),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Skipping malformed ad segment in chunk %d: %s", index + 1, exc)

    results[index] = segments


def _split_into_chunks(
    segments: list[Segment],
    *,
    chunk_duration_ms: int,
    overlap_ms: int,
) -> list[TranscriptChunk]:
    """Split segments into overlapping time-window chunks."""
    if not segments:
        return []

    total_start = segments[0].start_ms
    total_end = segments[-1].end_ms
    chunks: list[TranscriptChunk] = []

    window_start = total_start
    while window_start < total_end:
        window_end = window_start + chunk_duration_ms
        chunk = [s for s in segments if s.end_ms > window_start and s.start_ms < window_end]
        if chunk:
            chunks.append(chunk)
        window_start = window_end - overlap_ms

    return chunks


def _merge_segments(
    segments: list[AdSegment],
    *,
    merge_gap_ms: int,
) -> list[AdSegment]:
    """Merge adjacent ad segments within merge_gap_ms of each other."""
    if not segments:
        return []

    sorted_segs = sorted(segments, key=lambda s: s.start_ms)
    merged: list[AdSegment] = [sorted_segs[0]]

    for seg in sorted_segs[1:]:
        prev = merged[-1]
        if seg.start_ms - prev.end_ms <= merge_gap_ms:
            # Merge: extend previous segment
            merged[-1] = AdSegment(
                episode_guid=prev.episode_guid,
                start_ms=prev.start_ms,
                end_ms=max(prev.end_ms, seg.end_ms),
                confidence=max(prev.confidence, seg.confidence),
                reason=f"{prev.reason}; {seg.reason}",
                sponsor_name=prev.sponsor_name or seg.sponsor_name,
            )
        else:
            merged.append(seg)

    return merged
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ad_detector.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add pipeline/ad_detector.py tests/test_ad_detector.py
git commit -m "feat: add ad detector with chunked LLM analysis and segment merging"
```

---

## Task 13: Audio Editor

**Files:**
- Create: `pipeline/audio_editor.py`
- Create: `tests/fixtures/silence.mp3` (generated)
- Create: `tests/test_audio_editor.py`

**Step 1: Generate a test audio fixture**

Run: `ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t 10 -q:a 9 -acodec libmp3lame tests/fixtures/silence.mp3`
This creates a 10-second silent MP3 file.

**Step 2: Write failing tests**

```python
# tests/test_audio_editor.py
from pathlib import Path

from models.ad_segment import AdSegment

FIXTURE_AUDIO = Path("tests/fixtures/silence.mp3")


async def test_cut_ads_removes_segments(tmp_path):
    from pipeline.audio_editor import cut_ads

    segments = [
        AdSegment(
            episode_guid="ep-001",
            start_ms=2000,
            end_ms=5000,
            confidence=0.9,
            reason="Ad",
        ),
    ]
    output = await cut_ads(
        audio_path=FIXTURE_AUDIO,
        ad_segments=segments,
        output_dir=tmp_path,
        output_format="mp3",
        cbr_bitrate="192k",
    )
    assert output.exists()
    assert output.stat().st_size > 0

    from pydub import AudioSegment

    original = AudioSegment.from_file(FIXTURE_AUDIO)
    clean = AudioSegment.from_file(output)
    # Removed 3s of ads, so clean should be ~7s (±500ms for encoding)
    assert abs(len(clean) - 7000) < 500


async def test_cut_ads_no_segments_returns_copy(tmp_path):
    from pipeline.audio_editor import cut_ads

    output = await cut_ads(
        audio_path=FIXTURE_AUDIO,
        ad_segments=[],
        output_dir=tmp_path,
        output_format="mp3",
        cbr_bitrate="192k",
    )
    assert output.exists()
    from pydub import AudioSegment

    original = AudioSegment.from_file(FIXTURE_AUDIO)
    clean = AudioSegment.from_file(output)
    assert abs(len(clean) - len(original)) < 500
```

**Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_audio_editor.py -v`
Expected: FAIL.

**Step 4: Write `pipeline/audio_editor.py`**

```python
import asyncio
import logging
from pathlib import Path

from pydub import AudioSegment

from models.ad_segment import AdSegment
from pipeline.exceptions import AudioEditError

logger = logging.getLogger(__name__)

type Milliseconds = int
type SegmentPair = tuple[Milliseconds, Milliseconds]


async def cut_ads(
    *,
    audio_path: Path,
    ad_segments: list[AdSegment],
    output_dir: Path,
    output_format: str = "mp3",
    cbr_bitrate: str = "192k",
) -> Path:
    """Cut ad segments from audio and export a clean file."""
    logger.info("Cutting %d ad segments from %s", len(ad_segments), audio_path.name)
    output_path = output_dir / f"clean.{output_format}"

    return await asyncio.to_thread(
        _cut_ads_sync,
        audio_path=audio_path,
        ad_segments=ad_segments,
        output_path=output_path,
        output_format=output_format,
        cbr_bitrate=cbr_bitrate,
    )


def _cut_ads_sync(
    *,
    audio_path: Path,
    ad_segments: list[AdSegment],
    output_path: Path,
    output_format: str,
    cbr_bitrate: str,
) -> Path:
    """Synchronous implementation — runs in a thread."""
    try:
        audio = AudioSegment.from_file(audio_path)
    except Exception as exc:
        raise AudioEditError(f"Failed to load audio: {exc}") from exc

    original_duration = len(audio)

    if not ad_segments:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        audio.export(str(output_path), format=output_format, bitrate=cbr_bitrate)
        logger.info("No ads to cut, exported copy: %s", output_path.name)
        return output_path

    # Sort ad segments by start time
    sorted_ads = sorted(ad_segments, key=lambda s: s.start_ms)

    # Compute keep segments (inverse of ad segments)
    keep_segments: list[SegmentPair] = []
    cursor: Milliseconds = 0

    for ad in sorted_ads:
        if ad.start_ms > cursor:
            keep_segments.append((cursor, ad.start_ms))
        cursor = max(cursor, ad.end_ms)

    if cursor < original_duration:
        keep_segments.append((cursor, original_duration))

    # Concatenate keep segments
    clean = AudioSegment.empty()
    for start, end in keep_segments:
        clean += audio[start:end]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        clean.export(str(output_path), format=output_format, bitrate=cbr_bitrate)
    except Exception as exc:
        raise AudioEditError(f"Failed to export audio: {exc}") from exc

    removed_ms = original_duration - len(clean)
    pct = (removed_ms / original_duration * 100) if original_duration > 0 else 0
    logger.info(
        "Export complete: %s — removed %.1fs (%.0f%% of episode)",
        output_path.name,
        removed_ms / 1000,
        pct,
    )
    return output_path
```

**Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_audio_editor.py -v`
Expected: All PASS.

**Step 6: Commit**

```bash
git add pipeline/audio_editor.py tests/test_audio_editor.py tests/fixtures/silence.mp3
git commit -m "feat: add audio editor with ad segment cutting"
```

---

## Task 14: Pipeline Runner

**Files:**
- Create: `pipeline/runner.py`
- Create: `tests/test_runner.py`

**Step 1: Write failing test**

```python
# tests/test_runner.py
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config_loader import load_config


async def test_run_pipeline_single_feed(tmp_path):
    from pipeline.runner import run_pipeline

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))

    mock_episode = MagicMock()
    mock_episode.guid = "ep-001"
    mock_episode.title = "Test Episode"
    mock_episode.audio_url = "https://example.com/ep.mp3"
    mock_episode.feed_title = "Test Pod"

    mock_transcript = MagicMock()
    mock_transcript.episode_guid = "ep-001"
    mock_transcript.full_text = "Hello world"
    mock_transcript.segments = ()

    mock_topic = MagicMock()
    mock_topic.domain = "tech"
    mock_topic.topic = "testing"

    with (
        patch("pipeline.runner.fetch_latest_episode", new_callable=AsyncMock, return_value=mock_episode),
        patch("pipeline.runner.download_episode", new_callable=AsyncMock, return_value=tmp_path / "audio.mp3"),
        patch("pipeline.runner.transcribe_episode", new_callable=AsyncMock, return_value=mock_transcript),
        patch("pipeline.runner.extract_topic", new_callable=AsyncMock, return_value=mock_topic),
        patch("pipeline.runner.detect_ads", new_callable=AsyncMock, return_value=[]),
        patch("pipeline.runner.cut_ads", new_callable=AsyncMock, return_value=tmp_path / "clean.mp3"),
        patch("pipeline.runner.get_db") as mock_get_db,
    ):
        mock_conn = AsyncMock()
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=False)

        # Should not raise
        await run_pipeline(cfg=cfg, feed_name="Test Podcast", dry_run=True)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL.

**Step 3: Write `pipeline/runner.py`**

```python
import logging
from pathlib import Path

from config_loader import AppConfig
from db.connection import get_db
from db.repositories.ad_segment_repo import AdSegmentRepository
from db.repositories.episode_repo import EpisodeRepository
from db.repositories.transcript_repo import TranscriptRepository
from pipeline.ad_detector import detect_ads
from pipeline.audio_editor import cut_ads
from pipeline.downloader import download_episode
from pipeline.rss import fetch_latest_episode
from pipeline.topic_extractor import extract_topic
from pipeline.transcriber import transcribe_episode

logger = logging.getLogger(__name__)


async def run_pipeline(
    *,
    cfg: AppConfig,
    feed_name: str | None = None,
    output_dir: Path | None = None,
    min_confidence: float | None = None,
    use_cache: bool = False,
    dry_run: bool = False,
) -> None:
    """Run the full podcast ad-cutting pipeline."""
    feeds = cfg.feeds
    if feed_name:
        feeds = [f for f in feeds if f.name == feed_name]
        if not feeds:
            logger.error("Feed '%s' not found in config", feed_name)
            return

    out_dir = output_dir or cfg.paths.output_dir
    confidence = min_confidence or cfg.ad_detection.min_confidence

    for feed_cfg in feeds:
        if not feed_cfg.enabled:
            logger.info("Skipping disabled feed: %s", feed_cfg.name)
            continue

        logger.info("Processing feed: %s", feed_cfg.name)

        try:
            await _process_feed(
                feed_cfg=feed_cfg,
                cfg=cfg,
                output_dir=out_dir,
                min_confidence=confidence,
                use_cache=use_cache,
                dry_run=dry_run,
            )
        except Exception:
            logger.exception("Failed to process feed '%s'", feed_cfg.name)


async def _process_feed(
    *,
    feed_cfg: "from config_loader import FeedConfig",  # type: ignore[name-defined]
    cfg: AppConfig,
    output_dir: Path,
    min_confidence: float,
    use_cache: bool,
    dry_run: bool,
) -> None:
    from config_loader import FeedConfig

    assert isinstance(feed_cfg, FeedConfig)

    # 1. Fetch latest episode
    episode = await fetch_latest_episode(feed_cfg)
    if episode is None:
        return

    async with get_db(cfg.paths.database) as conn:
        episode_repo = EpisodeRepository(conn)
        transcript_repo = TranscriptRepository(conn)
        ad_repo = AdSegmentRepository(conn)

        # 2. Persist episode
        await episode_repo.upsert(episode)

        # 3. Download
        audio_path = await download_episode(episode, output_dir=output_dir)

        # 4. Transcribe (with cache check)
        transcript = None
        if use_cache:
            transcript = await transcript_repo.get_by_episode_guid(episode.guid)
            if transcript:
                logger.info("Transcript cache hit for %s", episode.guid)

        if transcript is None:
            transcript = await transcribe_episode(
                episode=episode, audio_path=audio_path, cfg=cfg.transcription
            )
            await transcript_repo.save(transcript)

        # 5. Extract topic
        topic = await extract_topic(transcript=transcript, cfg=cfg.llm)

        # 6. Detect ads
        ad_segments = await detect_ads(
            transcript=transcript,
            topic=topic,
            llm_cfg=cfg.llm,
            ad_cfg=cfg.ad_detection,
        )
        await ad_repo.save_all(ad_segments)

        # 7. Cut audio (unless dry run)
        if dry_run:
            logger.info("Dry run — skipping audio cutting for %s", episode.guid)
            return

        above_threshold = [s for s in ad_segments if s.confidence >= min_confidence]
        if above_threshold:
            await cut_ads(
                audio_path=audio_path,
                ad_segments=above_threshold,
                output_dir=audio_path.parent,
                output_format=cfg.audio.output_format,
                cbr_bitrate=cfg.audio.cbr_bitrate,
            )
            await ad_repo.mark_cut(episode.guid)
        else:
            logger.info("No ads above threshold for %s", episode.guid)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runner.py -v`
Expected: All PASS.

**Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner.py
git commit -m "feat: add pipeline runner orchestrating all stages"
```

---

## Task 15: CLI Entry Point

**Files:**
- Create: `main.py`

**Step 1: Write `main.py`**

```python
import argparse
import asyncio
import logging
from pathlib import Path

from config_loader import load_config
from pipeline.runner import run_pipeline


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Podcast Ad Cutter")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Config file path")
    parser.add_argument("--feed", type=str, default=None, help="Process a single feed by name")
    parser.add_argument("--output", type=Path, default=None, help="Override output directory")
    parser.add_argument("--min-confidence", type=float, default=None, help="Override confidence threshold")
    parser.add_argument("--use-cache", action="store_true", help="Skip transcription if cached")
    parser.add_argument("--dry-run", action="store_true", help="Detect ads but skip cutting")
    parser.add_argument("-v", "--verbose", action="store_true", help="Set log level to DEBUG")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)

    level = "DEBUG" if args.verbose else cfg.logging.level
    setup_logging(level, cfg.logging.log_file)

    asyncio.run(
        run_pipeline(
            cfg=cfg,
            feed_name=args.feed,
            output_dir=args.output,
            min_confidence=args.min_confidence,
            use_cache=args.use_cache,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
```

**Step 2: Verify it parses args**

Run: `uv run python main.py --help`
Expected: Help text printed with all options.

**Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add CLI entry point with argument parsing"
```

---

## Task 16: Report Generator

**Files:**
- Modify: `pipeline/audio_editor.py` (add report writing)
- Modify: `pipeline/runner.py` (call report writer)

**Step 1: Add `write_report` to `pipeline/audio_editor.py`**

Add after the `cut_ads` function:

```python
def write_report(
    *,
    output_dir: Path,
    ad_segments: list[AdSegment],
    original_duration_ms: int,
) -> Path:
    """Write a human-readable report of removed segments."""
    report_path = output_dir / "report.txt"
    total_removed = sum(s.end_ms - s.start_ms for s in ad_segments)
    pct = (total_removed / original_duration_ms * 100) if original_duration_ms > 0 else 0

    lines = [
        "Podcast Ad Cutter — Report",
        "=" * 40,
        f"Original duration: {original_duration_ms / 1000:.1f}s",
        f"Total removed:     {total_removed / 1000:.1f}s ({pct:.1f}%)",
        f"Segments removed:  {len(ad_segments)}",
        "",
    ]

    for i, seg in enumerate(ad_segments, 1):
        dur = (seg.end_ms - seg.start_ms) / 1000
        lines.append(f"  {i}. [{seg.start_ms / 1000:.1f}s – {seg.end_ms / 1000:.1f}s] ({dur:.1f}s)")
        lines.append(f"     Confidence: {seg.confidence:.2f}")
        lines.append(f"     Reason: {seg.reason}")
        if seg.sponsor_name:
            lines.append(f"     Sponsor: {seg.sponsor_name}")
        lines.append("")

    report_path.write_text("\n".join(lines))
    logger.info("Report written to %s", report_path)
    return report_path
```

**Step 2: Commit**

```bash
git add pipeline/audio_editor.py
git commit -m "feat: add report generator for ad removal summary"
```

---

## Task 17: Final Integration and Lint

**Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS.

**Step 2: Run ruff lint and format check**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: No errors. Fix any issues found.

**Step 3: Run mypy**

Run: `uv run mypy .`
Expected: No errors. Fix type issues as needed (may require adjusting some annotations, adding `# type: ignore` comments with codes for litellm dynamic APIs).

**Step 4: Final commit**

```bash
git add -A
git commit -m "chore: fix lint and type errors for clean CI"
```

---

## Summary

| Task | Component | Key Deliverables |
|------|-----------|-----------------|
| 1 | Scaffolding | pyproject.toml, .gitignore, config templates |
| 2 | Exceptions | Exception hierarchy in pipeline/exceptions.py |
| 3 | Models | Episode, Transcript, Segment, AdSegment, TopicContext |
| 4 | Config | config_loader.py with Pydantic validation |
| 5 | Database | Schema, migrations, connection manager |
| 6 | Repositories | Episode, Transcript, AdSegment repos with tests |
| 7 | LLM Client | litellm wrapper with retry logic |
| 8 | RSS | Feed parser with enclosure + media:content |
| 9 | Downloader | Streaming download with SHA-256 verification |
| 10 | Transcriber | Audio → Transcript with word grouping |
| 11 | Topic Extractor | LLM-based topic analysis |
| 12 | Ad Detector | Chunked detection with merging |
| 13 | Audio Editor | pydub-based ad cutting |
| 14 | Pipeline Runner | Full orchestration |
| 15 | CLI | argparse entry point |
| 16 | Report | Human-readable removal report |
| 17 | Final QA | Lint, format, type check |
