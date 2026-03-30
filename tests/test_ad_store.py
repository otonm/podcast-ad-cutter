"""Tests for AdStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from database.ad_store import AdStore
from database.connection import Database
from database.episode_store import EpisodeStore
from models.ad_detection import AdSegment
from models.feed import Episode

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _ep(guid: str) -> Episode:
    return Episode(guid=guid, url=f"https://example.com/{guid}.mp3", title=guid)


def _seg(guid: str, start_ms: int = 60000, end_ms: int = 90000) -> AdSegment:
    return AdSegment(
        guid=guid,
        start_ms=start_ms,
        end_ms=end_ms,
        confidence=0.95,
        sponsor="Acme",
        ad_topic="widget app",
    )


# ---------------------------------------------------------------------------
# get_detected_guids
# ---------------------------------------------------------------------------

async def test_get_detected_guids_empty(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AdStore(db.conn)
        result = await store.get_detected_guids()
    assert result == set()


async def test_get_detected_guids_after_mark(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1"), _ep("ep-2")])
        store = AdStore(db.conn)
        await store.mark_detected("ep-1")
        result = await store.get_detected_guids()
    assert result == {"ep-1"}


async def test_get_detected_guids_multiple(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1"), _ep("ep-2"), _ep("ep-3")])
        store = AdStore(db.conn)
        await store.mark_detected("ep-1")
        await store.mark_detected("ep-2")
        result = await store.get_detected_guids()
    assert result == {"ep-1", "ep-2"}


# ---------------------------------------------------------------------------
# mark_detected
# ---------------------------------------------------------------------------

async def test_mark_detected_is_idempotent(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = AdStore(db.conn)
        await store.mark_detected("ep-1")
        await store.mark_detected("ep-1")  # no error

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM ad_detection_runs")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 1


async def test_mark_detected_raises_on_fk_violation(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AdStore(db.conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.mark_detected("ghost-guid")


# ---------------------------------------------------------------------------
# save_segments
# ---------------------------------------------------------------------------

async def test_save_segments_stores_correct_values(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = AdStore(db.conn)
        await store.save_segments("ep-1", [
            AdSegment(guid="ep-1", start_ms=60000, end_ms=90000,
                      confidence=0.95, sponsor="Acme", ad_topic="widget app"),
        ])

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT guid, start_ms, end_ms, confidence, sponsor, ad_topic FROM ad_segments"
        )
        row = await cursor.fetchone()
    assert row == ("ep-1", 60000, 90000, 0.95, "Acme", "widget app")


async def test_save_segments_empty_list_is_noop(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = AdStore(db.conn)
        await store.save_segments("ep-1", [])

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM ad_segments")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 0


async def test_save_segments_is_idempotent(db_path: Path) -> None:
    """Calling save_segments twice for the same guid replaces, not duplicates."""
    segs = [_seg("ep-1", 60000, 90000), _seg("ep-1", 300000, 330000)]
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = AdStore(db.conn)
        await store.save_segments("ep-1", segs)
        await store.save_segments("ep-1", segs)

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM ad_segments WHERE guid='ep-1'")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 2


async def test_save_segments_raises_on_fk_violation(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AdStore(db.conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.save_segments("ghost-guid", [_seg("ghost-guid")])


# ---------------------------------------------------------------------------
# get_segments_for_guid
# ---------------------------------------------------------------------------

async def test_get_segments_for_guid_returns_ordered(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = AdStore(db.conn)
        await store.save_segments("ep-1", [
            _seg("ep-1", 300000, 330000),
            _seg("ep-1", 60000, 90000),
        ])
        result = await store.get_segments_for_guid("ep-1")

    assert len(result) == 2
    assert result[0].start_ms == 60000
    assert result[1].start_ms == 300000


async def test_get_segments_for_guid_correct_fields(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        store = AdStore(db.conn)
        await store.save_segments("ep-1", [
            AdSegment(guid="ep-1", start_ms=60000, end_ms=90000,
                      confidence=0.95, sponsor="Acme", ad_topic="widget app"),
        ])
        result = await store.get_segments_for_guid("ep-1")

    seg = result[0]
    assert seg.guid == "ep-1"
    assert seg.start_ms == 60000
    assert seg.end_ms == 90000
    assert seg.confidence == pytest.approx(0.95)
    assert seg.sponsor == "Acme"
    assert seg.ad_topic == "widget app"


async def test_get_segments_for_guid_unknown_guid(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AdStore(db.conn)
        result = await store.get_segments_for_guid("no-such-guid")
    assert result == []
