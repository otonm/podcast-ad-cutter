"""Episode-specific database queries."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from models.feed import Episode

if TYPE_CHECKING:
    import aiosqlite

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
            )
            for ep in episodes
        ]
        await self._conn.executemany(
            "INSERT OR IGNORE INTO episodes "
            "(podcast, title, pubdate, guid, url, description, explicit, duration, image_url, "
            "episode_type, itunes_author, itunes_subtitle, itunes_summary, content_encoded, "
            "link, author, itunes_title, episode_number, season_number, itunes_block) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await self._conn.commit()
        logger.info(
            f"Saved {len(episodes)} episode(s) for podcast '{podcast}' "
            f"(url/description/explicit/duration/image_url included; duplicates ignored)"
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
            "link, author, itunes_title, episode_number, season_number, itunes_block "
            "FROM episodes WHERE podcast = ? ORDER BY pubdate DESC LIMIT ?",
            (podcast, limit),
        ) as cursor:
            rows = await cursor.fetchall()

        episodes = [_row_to_episode(row) for row in rows]
        logger.debug(
            f"Retrieved {len(episodes)} episode(s) for podcast '{podcast}' (limit={limit})"
        )
        return episodes

    async def update_episode_url(self, guid: str, new_url: str) -> None:
        """Replace the enclosure URL for a specific episode.

        Called by the pipeline after a processed audio file has been created,
        so the next feed publication uses the local file URL instead of the
        original remote URL.

        Args:
            guid: The episode's unique identifier.
            new_url: URL of the locally processed audio file.

        """
        await self._conn.execute(
            "UPDATE episodes SET url = ? WHERE guid = ?",
            (new_url, guid),
        )
        await self._conn.commit()
        logger.info(f"Episode '{guid}': enclosure URL updated to {new_url!r}")


def _row_to_episode(row: tuple[object, ...]) -> Episode:
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
        episode_number_raw,
        season_number_raw,
        itunes_block_int,
    ) = row

    pub_date = datetime.fromisoformat(str(pubdate)) if pubdate else datetime.now().astimezone()
    explicit: bool | None = None if explicit_int is None else bool(explicit_int)

    return Episode(
        guid=str(guid),
        url=str(url),
        title=str(title),
        pub_date=pub_date,
        description=str(description) if description is not None else None,
        explicit=explicit,
        duration=str(duration) if duration is not None else None,
        image_url=str(image_url) if image_url is not None else None,
        # Extended episode metadata
        episode_type=str(episode_type) if episode_type is not None else None,
        itunes_author=str(itunes_author) if itunes_author is not None else None,
        itunes_subtitle=str(itunes_subtitle) if itunes_subtitle is not None else None,
        itunes_summary=str(itunes_summary) if itunes_summary is not None else None,
        content_encoded=str(content_encoded) if content_encoded is not None else None,
        link=str(link) if link is not None else None,
        author=str(author) if author is not None else None,
        itunes_title=str(itunes_title) if itunes_title is not None else None,
        # Numeric fields: preserve None when absent; cast to int when present
        episode_number=int(episode_number_raw) if episode_number_raw is not None else None,
        season_number=int(season_number_raw) if season_number_raw is not None else None,
        # Bool stored as integer; always present (NOT NULL DEFAULT 0)
        itunes_block=bool(itunes_block_int),
    )
