import logging

import aiosqlite

from models.ad_segment import AdSegment

logger = logging.getLogger(__name__)


class AdSegmentRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_all(self, segments: list[AdSegment]) -> None:
        for seg in segments:
            await self._conn.execute(
                "INSERT INTO ad_segments"
                " (episode_guid, start_ms, end_ms, confidence, reason, sponsor_name, was_cut)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    seg.episode_guid,
                    seg.start_ms,
                    seg.end_ms,
                    seg.confidence,
                    seg.reason,
                    seg.sponsor_name,
                    int(seg.was_cut),
                ),
            )
        await self._conn.commit()
        guid = segments[0].episode_guid if segments else "?"
        logger.debug(f"Saved {len(segments)} ad segments for episode_guid={guid}")

    async def get_by_episode(self, episode_guid: str) -> list[AdSegment]:
        cursor = await self._conn.execute(
            "SELECT episode_guid, start_ms, end_ms, confidence, reason, sponsor_name, was_cut"
            " FROM ad_segments WHERE episode_guid = ? ORDER BY start_ms",
            (episode_guid,),
        )
        rows = await cursor.fetchall()
        return [
            AdSegment(
                episode_guid=r[0],
                start_ms=r[1],
                end_ms=r[2],
                confidence=r[3],
                reason=r[4],
                sponsor_name=r[5],
                was_cut=bool(r[6]),
            )
            for r in rows
        ]

    async def mark_cut(self, episode_guid: str) -> None:
        await self._conn.execute(
            "UPDATE ad_segments SET was_cut = 1 WHERE episode_guid = ?",
            (episode_guid,),
        )
        await self._conn.commit()
        logger.debug(f"Marked ad segments as cut for episode_guid={episode_guid}")
