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
            )
            for ep in episodes
        ]
        await self._conn.executemany(
            "INSERT OR IGNORE INTO episodes "
            "(podcast, title, pubdate, guid, url, description, explicit, duration, image_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "SELECT guid, url, title, pubdate, description, explicit, duration, image_url "
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
    """Convert a database row to a :class:`~models.feed.Episode`."""
    guid, url, title, pubdate, description, explicit_int, duration, image_url = row
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
    )
