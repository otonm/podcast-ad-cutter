"""Tests for EpisodeStore episode persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
import pytest

from database.connection import Database
from database.episode_store import EpisodeStore
from models.feed import Episode

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def episodes() -> list[Episode]:
    return [
        Episode(
            guid="guid-1",
            url="https://example.com/ep1.mp3",
            title="Episode One",
            pub_date=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        Episode(
            guid="guid-2",
            url="https://example.com/ep2.mp3",
            title="Episode Two",
            pub_date=None,
        ),
    ]


async def test_save_episodes_inserts_rows(db_path: Path, episodes: list[Episode]) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT podcast, title, pubdate, guid FROM episodes ORDER BY id"
        )
        rows = await cursor.fetchall()

    assert len(rows) == 2
    assert rows[0] == ("My Podcast", "Episode One", "2024-01-01T00:00:00+00:00", "guid-1")
    assert rows[1] == ("My Podcast", "Episode Two", None, "guid-2")


async def test_duplicate_guid_is_ignored(db_path: Path, episodes: list[Episode]) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        await store.save_episodes("My Podcast", episodes)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM episodes")
        (count,) = await cursor.fetchone()  # type: ignore[misc]

    assert count == 2


async def test_empty_episodes_list_is_noop(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Empty Podcast", [])

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM episodes")
        (count,) = await cursor.fetchone()  # type: ignore[misc]

    assert count == 0
