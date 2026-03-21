"""Database connection and schema management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

import aiosqlite

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast     TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    pubdate     TEXT,
    guid        TEXT    NOT NULL UNIQUE,
    url         TEXT    NOT NULL DEFAULT '',
    description TEXT,
    explicit    INTEGER,
    duration    TEXT,
    image_url   TEXT
)
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
        await self.conn.execute(_SCHEMA)
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
