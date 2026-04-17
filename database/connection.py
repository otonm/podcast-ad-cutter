"""Database connection and schema management."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Self

import aiosqlite

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

logger = logging.getLogger(__name__)

_EPISODES_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast         TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    pubdate         TEXT,
    guid            TEXT    NOT NULL UNIQUE,
    url             TEXT    NOT NULL DEFAULT '',
    description     TEXT,
    explicit        INTEGER,
    duration        TEXT,
    image_url       TEXT,
    episode_type    TEXT,
    itunes_author   TEXT,
    itunes_subtitle TEXT,
    itunes_summary  TEXT,
    content_encoded TEXT,
    link            TEXT,
    author          TEXT,
    itunes_title    TEXT,
    episode_number  INTEGER,
    season_number   INTEGER,
    itunes_block    INTEGER NOT NULL DEFAULT 0,
    length          INTEGER NOT NULL DEFAULT 0
)
"""

_AUDIO_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS episode_audio_metadata (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guid     TEXT    NOT NULL UNIQUE REFERENCES episodes(guid),
    duration REAL    NOT NULL,
    codec    TEXT    NOT NULL,
    channels INTEGER NOT NULL,
    bitrate  INTEGER NOT NULL
)
"""

_TRANSCRIPTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcriptions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guid          TEXT    NOT NULL UNIQUE REFERENCES episodes(guid),
    transcription TEXT    NOT NULL
)
"""

_TRANSCRIPTION_SEGMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcription_segments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guid     TEXT    NOT NULL REFERENCES episodes(guid),
    start_ms INTEGER NOT NULL,
    end_ms   INTEGER NOT NULL,
    text     TEXT    NOT NULL
)
"""

_COST_TRACKING_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_tracking (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT    NOT NULL,
    model    TEXT    NOT NULL,
    cost     REAL    NOT NULL
)
"""

_TOPIC_EXTRACTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS topic_extractions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    guid    TEXT    NOT NULL UNIQUE REFERENCES episodes(guid),
    podcast TEXT    NOT NULL,
    title   TEXT    NOT NULL,
    topic   TEXT    NOT NULL,
    hosts   TEXT    NOT NULL,
    show    TEXT    NOT NULL
)
"""

_AD_SEGMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_segments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guid       TEXT    NOT NULL REFERENCES episodes(guid),
    start_ms   INTEGER NOT NULL,
    end_ms     INTEGER NOT NULL,
    confidence REAL    NOT NULL,
    sponsor    TEXT    NOT NULL,
    ad_topic   TEXT    NOT NULL,
    indices    TEXT    NOT NULL DEFAULT '[]'
)
"""

_AD_DETECTION_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_detection_runs (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT    NOT NULL UNIQUE REFERENCES episodes(guid)
)
"""

_TRANSCRIPTION_SEGMENTS_GUID_INDEX = """
CREATE INDEX IF NOT EXISTS idx_transcription_segments_guid ON transcription_segments(guid)
"""

_AD_SEGMENTS_GUID_INDEX = """
CREATE INDEX IF NOT EXISTS idx_ad_segments_guid ON ad_segments(guid)
"""


class Database:
    """Async context manager that owns the SQLite connection and schema.

    Opens the connection on entry, applies the schema (idempotent), and
    closes the connection on exit.  Only the Pipeline should instantiate
    this class.

    Args:
        db_path: Path to the SQLite file. Parent directories are created
            automatically on entry.

    Attributes:
        conn: The live aiosqlite connection, available between __aenter__
            and __aexit__.

    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self.conn: aiosqlite.Connection

    async def __aenter__(self) -> Self:
        """Open the database connection and apply the schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self._db_path)
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute(_EPISODES_SCHEMA)
        await self.conn.execute(_AUDIO_METADATA_SCHEMA)
        await self.conn.execute(_TRANSCRIPTIONS_SCHEMA)
        await self.conn.execute(_TRANSCRIPTION_SEGMENTS_SCHEMA)
        await self.conn.execute(_COST_TRACKING_SCHEMA)
        await self.conn.execute(_TOPIC_EXTRACTIONS_SCHEMA)
        await self.conn.execute(_AD_SEGMENTS_SCHEMA)
        await self.conn.execute(_AD_DETECTION_RUNS_SCHEMA)
        await self.conn.execute(_TRANSCRIPTION_SEGMENTS_GUID_INDEX)
        await self.conn.execute(_AD_SEGMENTS_GUID_INDEX)
        with contextlib.suppress(aiosqlite.OperationalError):
            await self.conn.execute(
                "ALTER TABLE episodes ADD COLUMN length INTEGER NOT NULL DEFAULT 0"
            )
        await self.conn.commit()
        logger.debug(f"Database opened: {self._db_path}")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the database connection."""
        await self.conn.close()
        logger.debug(f"Database closed: {self._db_path}")
