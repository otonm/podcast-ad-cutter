"""AdStore — persists ad detection runs and ad segments."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from models.ad_detection import AdSegment

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class AdStore:
    """Handles ad detection persistence against an open aiosqlite connection.

    Expects the schema to already exist (created by Database).  Receives
    the connection rather than owning it — only Database manages the
    connection lifecycle.

    Args:
        conn: An open aiosqlite connection with ``ad_segments`` and
            ``ad_detection_runs`` tables present.

    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_detected_guids(self) -> set[str]:
        """Return all GUIDs that have already had ad detection run.

        Used by the pipeline to skip already-processed episodes.

        Returns:
            Set of GUIDs with existing ad detection run rows.

        """
        async with self._conn.execute("SELECT guid FROM ad_detection_runs") as cursor:
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def mark_detected(self, guid: str) -> None:
        """Record that ad detection has been run for this episode.

        Silently skips if the GUID is already present.

        Args:
            guid: Episode GUID to mark as detected.

        """
        await self._conn.execute(
            "INSERT OR IGNORE INTO ad_detection_runs (guid) VALUES (?)",
            (guid,),
        )
        await self._conn.commit()
        logger.debug(f"Marked ad detection complete for '{guid}'")

    async def save_segments(self, guid: str, segments: list[AdSegment]) -> None:
        """Persist ad segments for an episode, replacing any existing rows.

        Deletes all existing rows for the GUID before inserting, so calling
        twice produces exactly ``len(segments)`` rows.

        Args:
            guid: Episode GUID.
            segments: Ad segments to persist.  Empty list clears existing rows.

        """
        await self._conn.execute("DELETE FROM ad_segments WHERE guid = ?", (guid,))
        if segments:
            rows = [
                (s.guid, s.start_ms, s.end_ms, s.confidence, s.sponsor, s.ad_topic, json.dumps(s.indices))
                for s in segments
            ]
            await self._conn.executemany(
                "INSERT INTO ad_segments (guid, start_ms, end_ms, confidence, sponsor, ad_topic, indices) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        await self._conn.commit()
        logger.debug(f"Saved {len(segments)} ad segment(s) for '{guid}'")

    async def get_segments_for_guid(self, guid: str) -> list[AdSegment]:
        """Retrieve all ad segments for an episode, ordered by start time.

        Args:
            guid: Episode GUID.

        Returns:
            List of :class:`AdSegment` objects ordered by ``start_ms`` ascending.
            Empty list if no segments exist for this GUID.

        """
        async with self._conn.execute(
            "SELECT guid, start_ms, end_ms, confidence, sponsor, ad_topic, indices "
            "FROM ad_segments WHERE guid = ? ORDER BY start_ms ASC",
            (guid,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            AdSegment(
                guid=row[0],
                start_ms=row[1],
                end_ms=row[2],
                confidence=row[3],
                sponsor=row[4],
                ad_topic=row[5],
                indices=json.loads(row[6]),
            )
            for row in rows
        ]
