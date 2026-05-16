"""Episode-specific database queries."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from models.feed import Episode

if TYPE_CHECKING:
    import aiosqlite

STAGE_CASCADE: dict[str, list[str]] = {
    "download": [
        "episode_audio_metadata",
        "transcriptions",
        "transcription_segments",
        "topic_extractions",
        "ad_segments",
        "ad_detection_runs",
    ],
    "transcribe": [
        "transcriptions",
        "transcription_segments",
        "topic_extractions",
        "ad_segments",
        "ad_detection_runs",
    ],
    "topic": [
        "topic_extractions",
        "ad_segments",
        "ad_detection_runs",
    ],
    "ad-detect": [
        "ad_segments",
        "ad_detection_runs",
    ],
    "edit": [],
}

# Exact shape of rows returned by the SELECT in get_episodes_for_feed.
# Column order: guid, url, title, pubdate, description, explicit, duration,
# image_url, episode_type, itunes_author, itunes_subtitle, itunes_summary,
# content_encoded, link, author, itunes_title, episode_number, season_number,
# itunes_block, length, source_url — 21 fields total.
type _EpisodeRow = tuple[
    str,        # 0  guid
    str,        # 1  url
    str,        # 2  title
    str | None, # 3  pubdate
    str | None, # 4  description
    int | None, # 5  explicit_int
    str | None, # 6  duration
    str | None, # 7  image_url
    str | None, # 8  episode_type
    str | None, # 9  itunes_author
    str | None, # 10 itunes_subtitle
    str | None, # 11 itunes_summary
    str | None, # 12 content_encoded
    str | None, # 13 link
    str | None, # 14 author
    str | None, # 15 itunes_title
    int | None, # 16 episode_number
    int | None, # 17 season_number
    int,        # 18 itunes_block
    int,        # 19 length
    str,        # 20 source_url
]

logger = logging.getLogger(__name__)


class EpisodeStore:
    """Handles episode persistence against an open aiosqlite connection.

    Expects the schema to already exist (created by Database).  Receives
    the connection rather than owning it — only Database manages the
    connection lifecycle.

    Args:
        conn: An open aiosqlite connection with the episodes table present.

    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_episodes(self, podcast: str, episodes: list[Episode]) -> None:
        """Insert episodes, silently skipping any duplicate GUIDs.

        Existing rows are left unchanged — this preserves any URL that was
        previously updated by :meth:`update_episode_url`.

        Args:
            podcast: The feed's config title, stored in the podcast column.
            episodes: Episodes to persist.

        """
        if not episodes:
            return

        rows = [
            (
                podcast,
                ep.title,
                ep.pub_date.isoformat() if ep.pub_date is not None else None,
                ep.guid,
                ep.url,
                ep.description,
                int(ep.explicit) if ep.explicit is not None else None,
                ep.duration,
                ep.image_url,
                # 11 new extended fields
                ep.episode_type,
                ep.itunes_author,
                ep.itunes_subtitle,
                ep.itunes_summary,
                ep.content_encoded,
                ep.link,
                ep.author,
                ep.itunes_title,
                ep.episode_number,          # int or None — stored directly
                ep.season_number,           # int or None — stored directly
                int(ep.itunes_block),       # bool → 0/1; column is NOT NULL DEFAULT 0
                ep.length,
                ep.url,   # source_url — always the original URL; INSERT OR IGNORE keeps it immutable
            )
            for ep in episodes
        ]
        await self._conn.executemany(
            "INSERT OR IGNORE INTO episodes "
            "(podcast, title, pubdate, guid, url, description, explicit, duration, image_url, "
            "episode_type, itunes_author, itunes_subtitle, itunes_summary, content_encoded, "
            "link, author, itunes_title, episode_number, season_number, itunes_block, length, "
            "source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._conn.commit()
        logger.info(
            f"Saved {len(episodes)} episode(s) for podcast '{podcast}' "
            f"(all fields included; duplicates ignored)"
        )

    async def get_episodes_for_feed(
        self, podcast: str, limit: int
    ) -> list[Episode]:
        """Return the most recent episodes for a podcast, ready for publication.

        Episodes are ordered newest-first (by pubdate descending) to match RSS
        convention. When ``limit`` exceeds the number of stored episodes, all
        available episodes are returned.

        Args:
            podcast: The feed's config title used when episodes were saved.
            limit: Maximum number of episodes to return.

        Returns:
            List of :class:`~models.feed.Episode` ordered newest-first.

        """
        async with self._conn.execute(
            "SELECT guid, url, title, pubdate, description, explicit, duration, image_url, "
            "episode_type, itunes_author, itunes_subtitle, itunes_summary, content_encoded, "
            "link, author, itunes_title, episode_number, season_number, itunes_block, length, "
            "source_url "
            "FROM episodes WHERE podcast = ? ORDER BY pubdate DESC LIMIT ?",
            (podcast, limit),
        ) as cursor:
            rows = await cursor.fetchall()

        episodes = [_row_to_episode(row) for row in rows]
        logger.debug(
            f"Retrieved {len(episodes)} episode(s) for podcast '{podcast}' (limit={limit})"
        )
        return episodes

    async def get_guids_for_feed(self, podcast: str) -> set[str]:
        """Return the set of GUIDs currently stored for a podcast.

        Used before saving new episodes to determine whether any episodes from
        a freshly-fetched RSS feed are new (not yet seen).

        Args:
            podcast: The feed's config title used when episodes were saved.

        Returns:
            Set of GUID strings.  Empty set if no episodes are stored yet.

        """
        async with self._conn.execute(
            "SELECT guid FROM episodes WHERE podcast = ?",
            (podcast,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def skip_episode(self, guid: str) -> bool:
        """Mark episode as permanently skipped. Returns False if GUID not found."""
        result = await self._conn.execute(
            "UPDATE episodes SET skipped = 1 WHERE guid = ?",
            (guid,),
        )
        await self._conn.commit()
        if result.rowcount > 0:
            logger.info(f"Episode {guid!r}: marked as permanently skipped")
            return True
        logger.warning(f"Episode {guid!r}: skip requested but not found in DB")
        return False

    async def reset_episode(self, guid: str, *, from_stage: str | None = None) -> bool:
        """Reset episode for reprocessing. Returns False if GUID not found."""
        tables = STAGE_CASCADE[from_stage] if from_stage else STAGE_CASCADE["download"]
        for table in tables:
            await self._conn.execute(
                f"DELETE FROM {table} WHERE guid = ?",  # noqa: S608
                (guid,),
            )
        if from_stage in (None, "download"):
            result = await self._conn.execute(
                "UPDATE episodes SET url = source_url WHERE guid = ?", (guid,)
            )
            await self._conn.commit()
            found = result.rowcount > 0
        else:
            async with self._conn.execute(
                "SELECT id FROM episodes WHERE guid = ?", (guid,)
            ) as cursor:
                row = await cursor.fetchone()
            await self._conn.commit()
            found = row is not None
        if found:
            logger.info(f"Episode {guid!r}: reset from stage {from_stage!r}")
        else:
            logger.warning(f"Episode {guid!r}: reset requested but not found in DB")
        return found

    async def update_episode_url(self, guid: str, new_url: str, length: int = 0) -> None:
        """Replace the enclosure URL and file size for a specific episode.

        Called by the pipeline after a processed audio file has been created,
        so the next feed publication uses the local file URL instead of the
        original remote URL.

        Args:
            guid: The episode's unique identifier.
            new_url: URL of the locally processed audio file.
            length: File size in bytes of the processed audio file.

        """
        await self._conn.execute(
            "UPDATE episodes SET url = ?, length = ? WHERE guid = ?",
            (new_url, length, guid),
        )
        await self._conn.commit()
        logger.info(f"Episode '{guid}': enclosure URL updated to {new_url!r}")


def _row_to_episode(row: _EpisodeRow) -> Episode:
    """Convert a database row to a :class:`~models.feed.Episode`.

    Column order must match the SELECT in :meth:`EpisodeStore.get_episodes_for_feed`:
        0  guid
        1  url
        2  title
        3  pubdate
        4  description
        5  explicit
        6  duration
        7  image_url
        8  episode_type
        9  itunes_author
        10 itunes_subtitle
        11 itunes_summary
        12 content_encoded
        13 link
        14 author
        15 itunes_title
        16 episode_number
        17 season_number
        18 itunes_block
        19 length
        20 source_url
    """
    (
        guid,
        url,
        title,
        pubdate,
        description,
        explicit_int,
        duration,
        image_url,
        episode_type,
        itunes_author,
        itunes_subtitle,
        itunes_summary,
        content_encoded,
        link,
        author,
        itunes_title,
        episode_number,
        season_number,
        itunes_block_int,
        length,
        source_url,
    ) = row

    # pubdate is stored as an ISO-8601 string; fall back to now() if missing.
    pub_date = datetime.fromisoformat(pubdate) if pubdate else datetime.now().astimezone()
    # explicit is stored as 0/1 integer; SQLite returns int | None directly.
    explicit: bool | None = None if explicit_int is None else bool(explicit_int)

    return Episode(
        guid=guid,
        url=url,
        title=title,
        pub_date=pub_date,
        description=description,
        explicit=explicit,
        duration=duration,
        image_url=image_url,
        # Extended episode metadata — SQLite returns str | None directly.
        episode_type=episode_type,
        itunes_author=itunes_author,
        itunes_subtitle=itunes_subtitle,
        itunes_summary=itunes_summary,
        content_encoded=content_encoded,
        link=link,
        author=author,
        itunes_title=itunes_title,
        # Numeric fields — SQLite INTEGER returns int | None directly.
        episode_number=episode_number,
        season_number=season_number,
        # Bool stored as 0/1 integer; always present (NOT NULL DEFAULT 0).
        itunes_block=bool(itunes_block_int),
        length=length,
        source_url=source_url,
    )
