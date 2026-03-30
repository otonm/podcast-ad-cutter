"""Tests for TranscriptionStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from database.connection import Database
from database.episode_store import EpisodeStore
from database.transcription_store import TranscriptionStore
from models.feed import Episode
from models.transcription import Transcription, TranscriptionSegment

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _ep(guid: str) -> Episode:
    return Episode(guid=guid, url=f"https://example.com/{guid}.mp3", title=guid)


def _transcription(guid: str) -> Transcription:
    return Transcription(guid=guid, text="Hello world")


def _segments(guid: str) -> list[TranscriptionSegment]:
    return [
        TranscriptionSegment(guid=guid, start_ms=0, end_ms=1500, text="Hello"),
        TranscriptionSegment(guid=guid, start_ms=1500, end_ms=3000, text="world"),
    ]


async def test_get_transcribed_guids_empty(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TranscriptionStore(db.conn)
        result = await store.get_transcribed_guids()
    assert result == set()


async def test_get_transcribed_guids_after_save(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1"), _ep("ep-2")])
        store = TranscriptionStore(db.conn)
        await store.save_transcription(_transcription("ep-1"))
        result = await store.get_transcribed_guids()
    assert result == {"ep-1"}


async def test_save_transcription_insert_or_ignore(db_path: Path) -> None:
    """Saving the same guid twice must not raise and must store exactly one row."""
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TranscriptionStore(db.conn)
        await store.save_transcription(_transcription("ep-1"))
        await store.save_transcription(_transcription("ep-1"))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM transcriptions")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 1


async def test_save_transcription_stores_correct_values(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TranscriptionStore(db.conn)
        await store.save_transcription(Transcription(guid="ep-1", text="Full transcription text"))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT guid, transcription FROM transcriptions")
        row = await cursor.fetchone()
    assert row == ("ep-1", "Full transcription text")


async def test_save_transcription_raises_on_fk_violation(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TranscriptionStore(db.conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.save_transcription(_transcription("ghost-guid"))


async def test_save_segments_empty_is_noop(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TranscriptionStore(db.conn)
        await store.save_segments([])  # must not raise

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM transcription_segments")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 0


async def test_save_segments_stores_all_rows(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TranscriptionStore(db.conn)
        await store.save_segments(_segments("ep-1"))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM transcription_segments")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 2


async def test_save_segments_stores_correct_values(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TranscriptionStore(db.conn)
        await store.save_segments([
            TranscriptionSegment(guid="ep-1", start_ms=250, end_ms=1750, text="Check")
        ])

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT guid, start_ms, end_ms, text FROM transcription_segments"
        )
        row = await cursor.fetchone()
    assert row == ("ep-1", 250, 1750, "Check")


async def test_save_segments_raises_on_fk_violation(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TranscriptionStore(db.conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.save_segments([
                TranscriptionSegment(guid="ghost-guid", start_ms=0, end_ms=1000, text="hi")
            ])


# ---------------------------------------------------------------------------
# get_transcription_text
# ---------------------------------------------------------------------------

async def test_get_transcription_text_returns_text(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TranscriptionStore(db.conn)
        await store.save_transcription(Transcription(guid="ep-1", text="Hello world"))
        result = await store.get_transcription_text("ep-1")
    assert result == "Hello world"


async def test_get_transcription_text_unknown_guid_returns_none(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TranscriptionStore(db.conn)
        result = await store.get_transcription_text("no-such-guid")
    assert result is None


# ---------------------------------------------------------------------------
# get_segments_for_guid
# ---------------------------------------------------------------------------

async def test_get_segments_for_guid_returns_ordered(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TranscriptionStore(db.conn)
        await store.save_segments([
            TranscriptionSegment(guid="ep-1", start_ms=1500, end_ms=3000, text="world"),
            TranscriptionSegment(guid="ep-1", start_ms=0, end_ms=1500, text="Hello"),
        ])
        result = await store.get_segments_for_guid("ep-1")

    assert len(result) == 2
    assert result[0].start_ms == 0
    assert result[1].start_ms == 1500


async def test_get_segments_for_guid_correct_fields(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = TranscriptionStore(db.conn)
        await store.save_segments([
            TranscriptionSegment(guid="ep-1", start_ms=250, end_ms=1750, text="Check"),
        ])
        result = await store.get_segments_for_guid("ep-1")

    seg = result[0]
    assert seg.guid == "ep-1"
    assert seg.start_ms == 250
    assert seg.end_ms == 1750
    assert seg.text == "Check"


async def test_get_segments_for_guid_empty(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = TranscriptionStore(db.conn)
        result = await store.get_segments_for_guid("no-such-guid")
    assert result == []
