"""Tests for Database connection and schema initialisation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from database.connection import Database

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


async def test_database_creates_file_on_enter(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert db_path.exists()  # noqa: ASYNC240


async def test_database_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "data.db"
    async with Database(nested):
        pass
    assert nested.exists()


async def test_episodes_table_exists_after_enter(db_path: Path) -> None:
    async with Database(db_path):
        async with aiosqlite.connect(db_path) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='episodes'"
            )
            row = await cursor.fetchone()
    assert row is not None


async def test_connection_is_exposed(db_path: Path) -> None:
    async with Database(db_path) as db:
        assert db.conn is not None


async def test_connection_is_closed_after_exit(db_path: Path) -> None:
    async with Database(db_path) as db:
        conn = db.conn
    # After __aexit__ the connection is closed — any further use must raise
    with pytest.raises(ValueError, match="no active connection"):
        await conn.execute("SELECT 1")


async def _column_names(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(episodes)")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def test_episodes_table_has_url_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "url" in await _column_names(db_path)


async def test_episodes_table_has_description_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "description" in await _column_names(db_path)


async def test_episodes_table_has_explicit_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "explicit" in await _column_names(db_path)


async def test_episodes_table_has_duration_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "duration" in await _column_names(db_path)


async def test_episodes_table_has_image_url_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "image_url" in await _column_names(db_path)


async def test_migration_adds_new_columns_to_existing_schema(tmp_path: Path) -> None:
    """Verify that opening an old-schema DB migrates it with all 11 new columns.

    Simulates an existing database created before the schema extension by
    building it with only the original 10 columns, then re-opening it through
    the real Database class so the migration logic runs.
    """
    db_file = tmp_path / "legacy.db"

    # Build an "old" database with only the original 10 columns.
    old_schema = """
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
    async with aiosqlite.connect(db_file) as conn:
        await conn.execute(old_schema)
        await conn.commit()

    # Re-open using the real Database class — migration must run.
    async with Database(db_file):
        pass

    new_columns = {
        "episode_type",
        "itunes_author",
        "itunes_subtitle",
        "itunes_summary",
        "content_encoded",
        "link",
        "author",
        "itunes_title",
        "episode_number",
        "season_number",
        "itunes_block",
    }
    found = await _column_names(db_file)
    for col in new_columns:
        assert col in found, f"Expected migrated column '{col}' not found in table"
