"""Episode-specific database queries."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from models.feed import Episode

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
            )
            for ep in episodes
        ]
        await self._conn.executemany(
            "INSERT OR IGNORE INTO episodes (podcast, title, pubdate, guid) VALUES (?, ?, ?, ?)",
            rows,
        )
        await self._conn.commit()
        logger.info(
            f"Saved {len(episodes)} episode(s) for podcast '{podcast}' (duplicates ignored)"
        )
