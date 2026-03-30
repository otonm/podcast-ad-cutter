"""TopicStore — persists episode topic extractions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.topic import TopicExtraction

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class TopicStore:
    """Handles topic extraction persistence against an open aiosqlite connection.

    Expects the schema to already exist (created by Database).  Receives
    the connection rather than owning it — only Database manages the
    connection lifecycle.

    Args:
        conn: An open aiosqlite connection with ``topic_extractions`` table present.

    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_extracted_guids(self) -> set[str]:
        """Return all GUIDs that already have a row in ``topic_extractions``.

        Used by the pipeline to skip already-processed episodes.

        Returns:
            Set of GUIDs with existing topic extraction rows.

        """
        async with self._conn.execute("SELECT guid FROM topic_extractions") as cursor:
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def save_topic(self, record: TopicExtraction) -> None:
        """Persist a topic extraction, silently skipping duplicate GUIDs.

        Args:
            record: TopicExtraction to persist.

        """
        await self._conn.execute(
            "INSERT OR IGNORE INTO topic_extractions "
            "(guid, podcast, title, topic, hosts, show) VALUES (?, ?, ?, ?, ?, ?)",
            (record.guid, record.podcast, record.title, record.topic, record.hosts, record.show),
        )
        await self._conn.commit()
        logger.debug(f"Saved topic extraction for '{record.guid}'")

    async def get_topic_for_guid(self, guid: str) -> TopicExtraction | None:
        """Return the topic extraction for an episode, or None if absent.

        Args:
            guid: Episode GUID.

        Returns:
            :class:`TopicExtraction`, or ``None`` if no row exists for this GUID.

        """
        async with self._conn.execute(
            "SELECT guid, podcast, title, topic, hosts, show "
            "FROM topic_extractions WHERE guid = ?",
            (guid,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return TopicExtraction(
            guid=row[0], podcast=row[1], title=row[2], topic=row[3], hosts=row[4], show=row[5]
        )
