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



async def test_foreign_keys_pragma_is_enforced(db_path: Path) -> None:
    """PRAGMA foreign_keys = ON must be active.

    Inserting into a child table without a matching parent row must raise
    IntegrityError.
    """
    async with Database(db_path) as db:
        # episodes table exists; episode_audio_metadata references it.
        # Inserting a metadata row with a non-existent guid must fail.
        with pytest.raises(aiosqlite.IntegrityError):
            await db.conn.execute(
                "INSERT INTO episode_audio_metadata (guid, duration, codec, channels, bitrate) "
                "VALUES ('ghost-guid', 60.0, 'aac', 2, 128000)"
            )


async def test_episode_audio_metadata_table_exists(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='episode_audio_metadata'"
        )
        row = await cursor.fetchone()
    assert row is not None


async def _audio_metadata_column_names(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(episode_audio_metadata)")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def test_episode_audio_metadata_has_expected_columns(db_path: Path) -> None:
    async with Database(db_path):
        pass
    cols = await _audio_metadata_column_names(db_path)
    assert {"id", "guid", "duration", "codec", "channels", "bitrate"} <= cols


_KNOWN_TABLES = frozenset({
    "episodes", "episode_audio_metadata", "transcriptions",
    "transcription_segments", "cost_tracking", "topic_extractions",
    "ad_segments", "ad_detection_runs",
})


async def _table_column_names(db_path: Path, table: str) -> set[str]:
    if table not in _KNOWN_TABLES:
        msg = f"unexpected table name: {table!r}"
        raise ValueError(msg)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def test_table_column_names_rejects_unknown_table(db_path: Path) -> None:
    async with Database(db_path):
        pass
    with pytest.raises(ValueError, match="unexpected table name"):
        await _table_column_names(db_path, "unknown_table")


async def test_transcriptions_table_exists(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions'"
        )
        row = await cursor.fetchone()
    assert row is not None


async def test_transcriptions_table_has_expected_columns(db_path: Path) -> None:
    async with Database(db_path):
        pass
    cols = await _table_column_names(db_path, "transcriptions")
    assert {"id", "guid", "transcription"} <= cols


async def test_transcription_segments_table_exists(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='transcription_segments'"
        )
        row = await cursor.fetchone()
    assert row is not None


async def test_transcription_segments_table_has_expected_columns(db_path: Path) -> None:
    async with Database(db_path):
        pass
    cols = await _table_column_names(db_path, "transcription_segments")
    assert {"id", "guid", "start_ms", "end_ms", "text"} <= cols


async def test_cost_tracking_table_exists(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cost_tracking'"
        )
        row = await cursor.fetchone()
    assert row is not None


async def test_cost_tracking_table_has_expected_columns(db_path: Path) -> None:
    async with Database(db_path):
        pass
    cols = await _table_column_names(db_path, "cost_tracking")
    assert {"id", "provider", "model", "cost"} <= cols


async def test_transcriptions_fk_enforced(db_path: Path) -> None:
    async with Database(db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.conn.execute(
                "INSERT INTO transcriptions (guid, transcription) VALUES ('ghost-guid', 'text')"
            )


async def test_transcription_segments_fk_enforced(db_path: Path) -> None:
    async with Database(db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.conn.execute(
                "INSERT INTO transcription_segments (guid, start_ms, end_ms, text) "
                "VALUES ('ghost-guid', 0, 1000, 'hello')"
            )


async def test_ad_segments_table_exists(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ad_segments'"
        )
        row = await cursor.fetchone()
    assert row is not None


async def test_ad_segments_table_has_expected_columns(db_path: Path) -> None:
    async with Database(db_path):
        pass
    cols = await _table_column_names(db_path, "ad_segments")
    assert {"id", "guid", "start_ms", "end_ms", "confidence", "sponsor", "ad_topic"} <= cols


async def test_ad_detection_runs_table_exists(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ad_detection_runs'"
        )
        row = await cursor.fetchone()
    assert row is not None


async def test_ad_detection_runs_table_has_expected_columns(db_path: Path) -> None:
    async with Database(db_path):
        pass
    cols = await _table_column_names(db_path, "ad_detection_runs")
    assert {"id", "guid"} <= cols


async def test_ad_segments_fk_enforced(db_path: Path) -> None:
    async with Database(db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.conn.execute(
                "INSERT INTO ad_segments (guid, start_ms, end_ms, confidence, sponsor, ad_topic) "
                "VALUES ('ghost-guid', 0, 1000, 0.9, 'Sponsor', 'ad')"
            )


async def test_ad_detection_runs_fk_enforced(db_path: Path) -> None:
    async with Database(db_path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.conn.execute(
                "INSERT INTO ad_detection_runs (guid) VALUES ('ghost-guid')"
            )


async def test_episodes_table_has_source_url_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "source_url" in await _column_names(db_path)


async def test_source_url_column_migration_on_existing_db(db_path: Path) -> None:
    """Database.__aenter__ must add source_url to a pre-existing episodes table that lacks it."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE episodes ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "podcast TEXT NOT NULL, title TEXT NOT NULL, pubdate TEXT, "
            "guid TEXT NOT NULL UNIQUE, url TEXT NOT NULL DEFAULT '', "
            "description TEXT, explicit INTEGER, duration TEXT, image_url TEXT, "
            "episode_type TEXT, itunes_author TEXT, itunes_subtitle TEXT, "
            "itunes_summary TEXT, content_encoded TEXT, link TEXT, author TEXT, "
            "itunes_title TEXT, episode_number INTEGER, season_number INTEGER, "
            "itunes_block INTEGER NOT NULL DEFAULT 0, "
            "length INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        await conn.commit()

    async with Database(db_path):
        pass

    assert "source_url" in await _column_names(db_path)


async def test_episodes_table_has_skipped_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "skipped" in await _column_names(db_path)


async def test_skipped_column_has_not_null_default_zero(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(episodes)")
        rows = await cursor.fetchall()
    skipped_col = next(r for r in rows if r[1] == "skipped")
    assert skipped_col[3] == 1  # notnull
    assert skipped_col[4] == "0"  # dflt_value


async def test_skipped_column_migration_is_idempotent(db_path: Path) -> None:
    """Opening Database twice on the same path must not raise."""
    async with Database(db_path):
        pass
    async with Database(db_path):
        pass
    assert "skipped" in await _column_names(db_path)


async def _cost_tracking_column_names(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(cost_tracking)")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def test_cost_tracking_has_guid_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "guid" in await _cost_tracking_column_names(db_path)


async def test_cost_tracking_guid_migration_is_idempotent(db_path: Path) -> None:
    """Opening Database twice on the same path must not raise (idempotency)."""
    async with Database(db_path):
        pass
    async with Database(db_path):
        pass
    assert "guid" in await _cost_tracking_column_names(db_path)
