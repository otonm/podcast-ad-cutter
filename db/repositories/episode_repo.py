import logging

import aiosqlite

from models.episode import Episode

logger = logging.getLogger(__name__)


class EpisodeRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert(self, episode: Episode) -> None:
        await self._conn.execute(
            "INSERT INTO episodes (guid, feed_name, title, audio_url, published_at, duration_sec)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(guid) DO UPDATE SET"
            "   title = excluded.title,"
            "   audio_url = excluded.audio_url,"
            "   published_at = excluded.published_at,"
            "   duration_sec = excluded.duration_sec",
            (
                episode.guid,
                episode.feed_title,
                episode.title,
                str(episode.audio_url),
                episode.published.isoformat(),
                episode.duration_seconds,
            ),
        )
        await self._conn.commit()
        logger.debug(f"Upserted episode guid={episode.guid}")

    async def get_by_guid(self, guid: str) -> Episode | None:
        cursor = await self._conn.execute(
            "SELECT guid, feed_name, title, audio_url, published_at, duration_sec"
            " FROM episodes WHERE guid = ?",
            (guid,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Episode(
            guid=row[0],
            feed_title=row[1],
            title=row[2],
            audio_url=row[3],
            published=row[4],
            duration_seconds=row[5],
        )

    async def list_by_feed(self, feed_name: str) -> list[Episode]:
        cursor = await self._conn.execute(
            "SELECT guid, feed_name, title, audio_url, published_at, duration_sec"
            " FROM episodes WHERE feed_name = ? ORDER BY published_at DESC",
            (feed_name,),
        )
        rows = await cursor.fetchall()
        return [
            Episode(
                guid=r[0],
                feed_title=r[1],
                title=r[2],
                audio_url=r[3],
                published=r[4],
                duration_seconds=r[5],
            )
            for r in rows
        ]
