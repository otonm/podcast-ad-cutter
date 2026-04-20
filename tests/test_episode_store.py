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
        rows = list(await cursor.fetchall())

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


# ---------------------------------------------------------------------------
# New-field round-trip tests
# ---------------------------------------------------------------------------


async def test_new_fields_round_trip_all_populated(db_path: Path) -> None:
    """All 11 new fields survive a save/retrieve cycle with non-None values."""
    ep = Episode(
        guid="guid-ext-1",
        url="https://example.com/ep-ext.mp3",
        title="Extended Episode",
        pub_date=datetime(2025, 6, 15, tzinfo=UTC),
        episode_type="trailer",
        itunes_author="Jane Doe",
        itunes_subtitle="A brief subtitle",
        itunes_summary="A longer summary paragraph.",
        content_encoded="<p>Rich HTML content</p>",
        link="https://example.com/episodes/1",
        author="jane@example.com (Jane Doe)",
        itunes_title="iTunes-Specific Title",
        episode_number=7,
        season_number=3,
        itunes_block=True,
    )
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Ext Podcast", [ep])
        result = await store.get_episodes_for_feed("Ext Podcast", limit=10)

    assert len(result) == 1
    got = result[0]
    assert got.episode_type == "trailer"
    assert got.itunes_author == "Jane Doe"
    assert got.itunes_subtitle == "A brief subtitle"
    assert got.itunes_summary == "A longer summary paragraph."
    assert got.content_encoded == "<p>Rich HTML content</p>"
    assert got.link == "https://example.com/episodes/1"
    assert got.author == "jane@example.com (Jane Doe)"
    assert got.itunes_title == "iTunes-Specific Title"
    assert got.episode_number == 7
    assert got.season_number == 3
    assert got.itunes_block is True


async def test_itunes_block_bool_round_trip(db_path: Path) -> None:
    """itunes_block is stored as an integer and comes back as a proper bool."""
    ep_true = Episode(
        guid="block-true",
        url="https://example.com/block-true.mp3",
        itunes_block=True,
    )
    ep_false = Episode(
        guid="block-false",
        url="https://example.com/block-false.mp3",
        itunes_block=False,
    )
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Block Podcast", [ep_true, ep_false])
        result = await store.get_episodes_for_feed("Block Podcast", limit=10)

    result_by_guid = {e.guid: e for e in result}
    # Verify the Python type, not just truthiness
    assert result_by_guid["block-true"].itunes_block is True
    assert result_by_guid["block-false"].itunes_block is False


async def test_episode_and_season_number_int_round_trip(db_path: Path) -> None:
    """episode_number and season_number survive as integers end-to-end."""
    ep = Episode(
        guid="num-ep",
        url="https://example.com/num.mp3",
        episode_number=5,
        season_number=2,
    )
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Num Podcast", [ep])
        result = await store.get_episodes_for_feed("Num Podcast", limit=10)

    assert len(result) == 1
    got = result[0]
    assert got.episode_number == 5
    assert isinstance(got.episode_number, int)
    assert got.season_number == 2
    assert isinstance(got.season_number, int)


async def test_get_guids_for_feed_returns_empty_for_unknown_podcast(db_path: Path) -> None:
    """get_guids_for_feed returns an empty set when no episodes exist for that podcast."""
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        result = await store.get_guids_for_feed("Unknown Podcast")

    assert result == set()


async def test_get_guids_for_feed_returns_guid_set(db_path: Path, episodes: list[Episode]) -> None:
    """get_guids_for_feed returns the exact set of GUIDs stored for that podcast."""
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        result = await store.get_guids_for_feed("My Podcast")

    assert result == {"guid-1", "guid-2"}


async def test_get_guids_for_feed_excludes_other_podcasts(
    db_path: Path, episodes: list[Episode]
) -> None:
    """get_guids_for_feed does not return GUIDs belonging to a different podcast."""
    other_ep = Episode(guid="guid-other", url="https://example.com/other.mp3")
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        await store.save_episodes("Other Podcast", [other_ep])
        result = await store.get_guids_for_feed("My Podcast")

    assert result == {"guid-1", "guid-2"}
    assert "guid-other" not in result


async def test_new_fields_default_null_round_trip(db_path: Path) -> None:
    """An episode with all new fields at their defaults comes back with None/False."""
    ep = Episode(
        guid="default-ep",
        url="https://example.com/default.mp3",
        # All 11 new fields intentionally left at their defaults
    )
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Default Podcast", [ep])
        result = await store.get_episodes_for_feed("Default Podcast", limit=10)

    assert len(result) == 1
    got = result[0]
    assert got.episode_type is None
    assert got.itunes_author is None
    assert got.itunes_subtitle is None
    assert got.itunes_summary is None
    assert got.content_encoded is None
    assert got.link is None
    assert got.author is None
    assert got.itunes_title is None
    assert got.episode_number is None
    assert got.season_number is None
    assert got.itunes_block is False


# ---------------------------------------------------------------------------
# Tests: enclosure length field (Fix 3)
# ---------------------------------------------------------------------------


async def test_episode_length_defaults_to_zero(db_path: Path) -> None:
    """Episode.length must default to 0 and round-trip through save/retrieve."""
    ep = Episode(guid="len-default", url="https://example.com/ep.mp3")
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Length Podcast", [ep])
        result = await store.get_episodes_for_feed("Length Podcast", limit=10)

    assert result[0].length == 0


async def test_update_episode_url_updates_length_in_db(
    db_path: Path, episodes: list[Episode]
) -> None:
    """update_episode_url must persist the length value so get_episodes_for_feed returns it."""
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        await store.update_episode_url("guid-1", "https://local/processed.mp3", length=5432100)
        result = await store.get_episodes_for_feed("My Podcast", limit=10)

    ep1 = next(e for e in result if e.guid == "guid-1")
    assert ep1.length == 5432100


async def test_update_episode_url_without_length_keeps_zero(
    db_path: Path, episodes: list[Episode]
) -> None:
    """Calling update_episode_url without length leaves length=0 (default)."""
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        await store.update_episode_url("guid-1", "https://local/processed.mp3")
        result = await store.get_episodes_for_feed("My Podcast", limit=10)

    ep1 = next(e for e in result if e.guid == "guid-1")
    assert ep1.length == 0


async def test_episodes_length_column_exists(db_path: Path) -> None:
    """The episodes table must have a length column after Database.__aenter__."""
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(episodes)")
        rows = await cursor.fetchall()
    column_names = {row[1] for row in rows}
    assert "length" in column_names


async def test_length_column_migration_on_existing_db(db_path: Path) -> None:
    """Database.__aenter__ must add the length column to a pre-existing episodes table."""
    # Create db without length column (simulate old schema)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE episodes ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "podcast TEXT NOT NULL, "
            "title TEXT NOT NULL, "
            "pubdate TEXT, "
            "guid TEXT NOT NULL UNIQUE, "
            "url TEXT NOT NULL DEFAULT '', "
            "description TEXT, explicit INTEGER, duration TEXT, image_url TEXT, "
            "episode_type TEXT, itunes_author TEXT, itunes_subtitle TEXT, "
            "itunes_summary TEXT, content_encoded TEXT, link TEXT, author TEXT, "
            "itunes_title TEXT, episode_number INTEGER, season_number INTEGER, "
            "itunes_block INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        await conn.commit()

    async with Database(db_path):
        pass

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(episodes)")
        rows = await cursor.fetchall()
    column_names = {row[1] for row in rows}
    assert "length" in column_names


async def test_source_url_is_stored_and_retrieved(db_path: Path) -> None:
    """source_url round-trips through save/get unchanged."""
    ep = Episode(
        guid="src-url-ep",
        url="https://cdn.example.com/ep.mp3",
    )
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Pod", [ep])
        result = await store.get_episodes_for_feed("Pod", limit=10)

    assert result[0].source_url == "https://cdn.example.com/ep.mp3"
