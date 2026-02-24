import logging

import aiosqlite

from models.transcript import Segment, Transcript

logger = logging.getLogger(__name__)


class TranscriptRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save(self, transcript: Transcript) -> None:
        cursor = await self._conn.execute(
            "INSERT INTO transcripts (episode_guid, language, full_text, provider_model)"
            " VALUES (?, ?, ?, ?)",
            (
                transcript.episode_guid,
                transcript.language,
                transcript.full_text,
                transcript.provider_model,
            ),
        )
        transcript_id = cursor.lastrowid
        for seg in transcript.segments:
            await self._conn.execute(
                "INSERT INTO transcript_segments (transcript_id, start_ms, end_ms, text)"
                " VALUES (?, ?, ?, ?)",
                (transcript_id, seg.start_ms, seg.end_ms, seg.text),
            )
        await self._conn.commit()
        logger.debug(f"Saved transcript for episode_guid={transcript.episode_guid}")

    async def get_by_episode_guid(self, episode_guid: str) -> Transcript | None:
        cursor = await self._conn.execute(
            "SELECT id, language, full_text, provider_model"
            " FROM transcripts WHERE episode_guid = ?",
            (episode_guid,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        transcript_id, language, full_text, provider_model = row
        seg_cursor = await self._conn.execute(
            "SELECT start_ms, end_ms, text"
            " FROM transcript_segments WHERE transcript_id = ? ORDER BY start_ms",
            (transcript_id,),
        )
        seg_rows = await seg_cursor.fetchall()
        segments = tuple(Segment(start_ms=r[0], end_ms=r[1], text=r[2]) for r in seg_rows)
        return Transcript(
            episode_guid=episode_guid,
            segments=segments,
            full_text=full_text,
            language=language,
            provider_model=provider_model,
        )

    async def delete(self, episode_guid: str) -> None:
        await self._conn.execute("DELETE FROM transcripts WHERE episode_guid = ?", (episode_guid,))
        await self._conn.commit()
        logger.debug(f"Deleted transcript for episode_guid={episode_guid}")
