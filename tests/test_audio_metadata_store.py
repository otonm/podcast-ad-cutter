"""Tests for AudioMetadataStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from database.audio_metadata_store import AudioMetadataStore
from database.connection import Database
from database.episode_store import EpisodeStore
from models.feed import AudioMetadata, Episode

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _ep(guid: str) -> Episode:
    return Episode(guid=guid, url=f"https://example.com/{guid}.mp3", title=guid)


def _meta(guid: str) -> AudioMetadata:
    return AudioMetadata(guid=guid, duration=120.5, codec="aac", channels=2, bitrate=128000)


async def test_get_probed_guids_empty(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AudioMetadataStore(db.conn)
        result = await store.get_probed_guids()
    assert result == set()


async def test_get_probed_guids_after_save(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1"), _ep("ep-2")])
        meta_store = AudioMetadataStore(db.conn)
        await meta_store.save_all([_meta("ep-1")])
        result = await meta_store.get_probed_guids()
    assert result == {"ep-1"}


async def test_save_all_empty_is_noop(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AudioMetadataStore(db.conn)
        await store.save_all([])  # must not raise
        result = await store.get_probed_guids()
    assert result == set()


async def test_save_all_insert_or_ignore(db_path: Path) -> None:
    """Saving the same record twice must not raise and must store exactly one row."""
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        meta_store = AudioMetadataStore(db.conn)
        await meta_store.save_all([_meta("ep-1")])
        await meta_store.save_all([_meta("ep-1")])  # second save — must be silently ignored

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM episode_audio_metadata")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 1


async def test_save_all_stores_correct_values(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        meta_store = AudioMetadataStore(db.conn)
        await meta_store.save_all([
            AudioMetadata(guid="ep-1", duration=3661.5, codec="mp3", channels=1, bitrate=64000)
        ])

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT guid, duration, codec, channels, bitrate FROM episode_audio_metadata"
        )
        row = await cursor.fetchone()

    assert row == ("ep-1", 3661.5, "mp3", 1, 64000)


async def test_save_all_raises_on_fk_violation(db_path: Path) -> None:
    """Inserting metadata for a GUID not in episodes must raise IntegrityError."""
    async with Database(db_path) as db:
        store = AudioMetadataStore(db.conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.save_all([_meta("ghost-guid")])
