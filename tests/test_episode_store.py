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
            description="First episode description",
            explicit=False,
            duration="01:00:00",
            image_url="https://example.com/ep1-cover.jpg",
        ),
        Episode(
            guid="guid-2",
            url="https://example.com/ep2.mp3",
            title="Episode Two",
            pub_date=datetime(2024, 1, 2, tzinfo=UTC),
            description="Second episode description",
            explicit=True,
            duration="00:30:00",
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
    assert rows[1] == ("My Podcast", "Episode Two", "2024-01-02T00:00:00+00:00", "guid-2")


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


async def test_save_episodes_stores_url(db_path: Path, episodes: list[Episode]) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT guid, url FROM episodes ORDER BY id")
        rows = list(await cursor.fetchall())

    assert rows[0] == ("guid-1", "https://example.com/ep1.mp3")
    assert rows[1] == ("guid-2", "https://example.com/ep2.mp3")


async def test_save_episodes_stores_description_explicit_duration(
    db_path: Path, episodes: list[Episode]
) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT description, explicit, duration FROM episodes WHERE guid = 'guid-1'"
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "First episode description"
    assert row[1] == 0  # False stored as 0
    assert row[2] == "01:00:00"


async def test_get_episodes_for_feed_returns_published_episodes(
    db_path: Path, episodes: list[Episode]
) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        result = await store.get_episodes_for_feed("My Podcast", limit=10)

    assert len(result) == 2
    # Newest first (ordered by pubdate DESC)
    assert result[0].guid == "guid-2"
    assert result[0].url == "https://example.com/ep2.mp3"
    assert result[0].title == "Episode Two"
    assert result[0].description == "Second episode description"
    assert result[0].explicit is True
    assert result[0].duration == "00:30:00"
    assert result[1].guid == "guid-1"


async def test_get_episodes_for_feed_respects_limit(
    db_path: Path, episodes: list[Episode]
) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        result = await store.get_episodes_for_feed("My Podcast", limit=1)

    assert len(result) == 1
    assert result[0].guid == "guid-2"  # newest


async def test_get_episodes_for_feed_returns_empty_for_unknown_podcast(
    db_path: Path,
) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        result = await store.get_episodes_for_feed("Unknown Podcast", limit=10)

    assert result == []


async def test_update_episode_url_changes_stored_url(
    db_path: Path, episodes: list[Episode]
) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        await store.update_episode_url("guid-1", "https://local/processed.mp3")
        result = await store.get_episodes_for_feed("My Podcast", limit=10)

    ep1 = next(e for e in result if e.guid == "guid-1")
    assert ep1.url == "https://local/processed.mp3"


async def test_save_episodes_does_not_overwrite_updated_url(
    db_path: Path, episodes: list[Episode]
) -> None:
    """Re-saving an episode that was already updated must not revert its URL."""
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        await store.update_episode_url("guid-1", "https://local/processed.mp3")
        # Second save of the same episodes must be a no-op due to INSERT OR IGNORE
        await store.save_episodes("My Podcast", episodes)
        result = await store.get_episodes_for_feed("My Podcast", limit=10)

    ep1 = next(e for e in result if e.guid == "guid-1")
    assert ep1.url == "https://local/processed.mp3"


async def test_save_episodes_stores_image_url(
    db_path: Path, episodes: list[Episode]
) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT guid, image_url FROM episodes ORDER BY id"
        )
        rows = list(await cursor.fetchall())

    assert rows[0] == ("guid-1", "https://example.com/ep1-cover.jpg")
    assert rows[1] == ("guid-2", None)


async def test_get_episodes_for_feed_returns_image_url(
    db_path: Path, episodes: list[Episode]
) -> None:
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        result = await store.get_episodes_for_feed("My Podcast", limit=10)

    ep1 = next(e for e in result if e.guid == "guid-1")
    ep2 = next(e for e in result if e.guid == "guid-2")
    assert ep1.image_url == "https://example.com/ep1-cover.jpg"
    assert ep2.image_url is None
