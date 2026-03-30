"""Tests for TopicStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from database.connection import Database
from database.episode_store import EpisodeStore
from database.topic_store import TopicStore
from models.feed import Episode
from models.topic import TopicExtraction

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _ep(guid: str) -> Episode:
    return Episode(guid=guid, url=f"https://example.com/{guid}.mp3", title=guid)


def _topic(guid: str) -> TopicExtraction:
    return TopicExtraction(
        guid=guid,
        podcast="my-podcast",
        title="Episode Title",
        topic="This episode covers topic A. It also discusses B. And touches on C.",
        hosts="Alice, Bob",
        show="My Show",
    )


async def test_get_extracted_guids_empty(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TopicStore(db.conn)
        result = await store.get_extracted_guids()
    assert result == set()


async def test_get_extracted_guids_after_save(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1"), _ep("ep-2")])
        store = TopicStore(db.conn)
        await store.save_topic(_topic("ep-1"))
        result = await store.get_extracted_guids()
    assert result == {"ep-1"}


async def test_save_topic_insert_or_ignore(db_path: Path) -> None:
    """Saving the same guid twice must not raise and must store exactly one row."""
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TopicStore(db.conn)
        await store.save_topic(_topic("ep-1"))
        await store.save_topic(_topic("ep-1"))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM topic_extractions")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 1


async def test_save_topic_stores_correct_values(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TopicStore(db.conn)
        await store.save_topic(TopicExtraction(
            guid="ep-1",
            podcast="my-podcast",
            title="Ep 1 Title",
            topic="The main topic.",
            hosts="Alice",
            show="My Show",
        ))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT guid, podcast, title, topic, hosts, show FROM topic_extractions"
        )
        row = await cursor.fetchone()
    assert row == ("ep-1", "my-podcast", "Ep 1 Title", "The main topic.", "Alice", "My Show")


async def test_save_topic_raises_on_fk_violation(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TopicStore(db.conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.save_topic(_topic("ghost-guid"))


async def test_get_topic_for_guid_returns_topic(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TopicStore(db.conn)
        await store.save_topic(_topic("ep-1"))
        result = await store.get_topic_for_guid("ep-1")
    assert result is not None
    assert result.guid == "ep-1"
    assert result.podcast == "my-podcast"
    assert result.hosts == "Alice, Bob"


async def test_get_topic_for_guid_unknown_guid_returns_none(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TopicStore(db.conn)
        result = await store.get_topic_for_guid("no-such-guid")
    assert result is None
