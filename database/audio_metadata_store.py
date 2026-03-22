"""AudioMetadata persistence against an open aiosqlite connection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from models.feed import AudioMetadata

logger = logging.getLogger(__name__)


class AudioMetadataStore:
    """Handles audio metadata persistence against an open aiosqlite connection.

    Expects the schema to already exist (created by Database).  Receives
    the connection rather than owning it — only Database manages the
    connection lifecycle.

    Args:
        conn: An open aiosqlite connection with episode_audio_metadata present.

    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_probed_guids(self) -> set[str]:
        """Return all GUIDs that already have a row in episode_audio_metadata.

        Used by the pipeline to filter out already-probed episodes before
        calling AudioProber.probe_all.

        Returns:
            Set of GUIDs with existing metadata rows.

        """
        async with self._conn.execute(
            "SELECT guid FROM episode_audio_metadata"
        ) as cursor:
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def save_all(self, records: list[AudioMetadata]) -> None:
        """Persist probe results, silently skipping any duplicate GUIDs.

        Args:
            records: Metadata records to persist.  Empty list is a no-op.

        """
        if not records:
            return
        rows = [(r.guid, r.duration, r.codec, r.channels, r.bitrate) for r in records]
        await self._conn.executemany(
            "INSERT OR IGNORE INTO episode_audio_metadata "
            "(guid, duration, codec, channels, bitrate) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await self._conn.commit()
        logger.info(f"Saved {len(records)} audio metadata record(s)")
