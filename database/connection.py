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
    itunes_block    INTEGER NOT NULL DEFAULT 0
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

# Columns added after the initial schema release.  Each entry is
# (column_name, column_definition).  The migration in __aenter__ attempts
# to ADD each column; SQLite raises OperationalError when it already exists,
# which we silently ignore so the routine is safe to run against both fresh
# and legacy databases.
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("episode_type", "TEXT"),
    ("itunes_author", "TEXT"),
    ("itunes_subtitle", "TEXT"),
    ("itunes_summary", "TEXT"),
    ("content_encoded", "TEXT"),
    ("link", "TEXT"),
    ("author", "TEXT"),
    ("itunes_title", "TEXT"),
    ("episode_number", "INTEGER"),
    ("season_number", "INTEGER"),
    ("itunes_block", "INTEGER NOT NULL DEFAULT 0"),
]


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
        """Open the database connection, apply the schema, and run migrations."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self._db_path)
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute(_EPISODES_SCHEMA)
        await self.conn.execute(_AUDIO_METADATA_SCHEMA)
        await self.conn.commit()

        # Migration: add any columns that did not exist in the original schema.
        # SQLite raises OperationalError with "duplicate column name" when a
        # column is already present; contextlib.suppress silently ignores it so
        # this routine is safe to run against both fresh and legacy databases.
        for col_name, col_type in _NEW_COLUMNS:
            with contextlib.suppress(aiosqlite.OperationalError):
                await self.conn.execute(
                    f"ALTER TABLE episodes ADD COLUMN {col_name} {col_type}"
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
