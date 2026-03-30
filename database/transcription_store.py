"""TranscriptionStore — persists episode transcriptions and segments."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.transcription import Transcription, TranscriptionSegment

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class TranscriptionStore:
    """Handles transcription persistence against an open aiosqlite connection.

    Expects the schema to already exist (created by Database).  Receives
    the connection rather than owning it — only Database manages the
    connection lifecycle.

    Args:
        conn: An open aiosqlite connection with ``transcriptions`` and
            ``transcription_segments`` tables present.

    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_transcribed_guids(self) -> set[str]:
        """Return all GUIDs that already have a row in ``transcriptions``.

        Used by the pipeline to skip already-transcribed episodes.

        Returns:
            Set of GUIDs with existing transcription rows.

        """
        async with self._conn.execute("SELECT guid FROM transcriptions") as cursor:
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def save_transcription(self, record: Transcription) -> None:
        """Persist a full transcription, silently skipping duplicate GUIDs.

        Args:
            record: Transcription to persist.

        """
        await self._conn.execute(
            "INSERT OR IGNORE INTO transcriptions (guid, transcription) VALUES (?, ?)",
            (record.guid, record.text),
        )
        await self._conn.commit()
        logger.debug(f"Saved transcription for '{record.guid}'")

    async def save_segments(self, segments: list[TranscriptionSegment]) -> None:
        """Persist timestamped segments for an episode.

        Args:
            segments: Segments to persist.  Empty list is a no-op.

        """
        if not segments:
            return
        rows = [(s.guid, s.start_ms, s.end_ms, s.text) for s in segments]
        await self._conn.executemany(
            "INSERT INTO transcription_segments (guid, start_ms, end_ms, text) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        await self._conn.commit()
        logger.debug(f"Saved {len(segments)} segment(s) for '{segments[0].guid}'")

    async def get_transcription_text(self, guid: str) -> str | None:
        """Return the full transcription text for an episode, or None if absent.

        Args:
            guid: Episode GUID.

        Returns:
            Transcription text, or ``None`` if no row exists for this GUID.

        """
        async with self._conn.execute(
            "SELECT transcription FROM transcriptions WHERE guid = ?", (guid,)
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def get_segments_for_guid(self, guid: str) -> list[TranscriptionSegment]:
        """Return all transcription segments for an episode, ordered by start time.

        Args:
            guid: Episode GUID.

        Returns:
            List of :class:`TranscriptionSegment` ordered by ``start_ms`` ascending.
            Empty list if no segments exist for this GUID.

        """
        async with self._conn.execute(
            "SELECT guid, start_ms, end_ms, text FROM transcription_segments "
            "WHERE guid = ? ORDER BY start_ms ASC",
            (guid,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [TranscriptionSegment(guid=r[0], start_ms=r[1], end_ms=r[2], text=r[3]) for r in rows]
