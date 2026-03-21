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
