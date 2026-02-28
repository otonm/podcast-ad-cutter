import json
import logging

import aiosqlite

from models.ad_segment import TopicContext

logger = logging.getLogger(__name__)


class TopicContextRepository:
    """Persist and retrieve topic context records from the database."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def get_by_episode_guid(self, episode_guid: str) -> TopicContext | None:
        """Return the cached TopicContext for this episode, or None if not found."""
        async with self._db.execute(
            "SELECT domain, topic, hosts, notes FROM topic_contexts WHERE episode_guid = ?",
            (episode_guid,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return TopicContext(
            domain=row[0],
            topic=row[1],
            hosts=tuple(json.loads(row[2]) if row[2] else []),
            notes=row[3] or "",
        )

    async def save(self, topic: TopicContext, *, episode_guid: str) -> None:
        """Persist a topic context, replacing any existing row for this episode."""
        await self._db.execute(
            "INSERT OR REPLACE INTO topic_contexts (episode_guid, domain, topic, hosts, notes)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                episode_guid,
                topic.domain,
                topic.topic,
                json.dumps(list(topic.hosts)),
                topic.notes,
            ),
        )
        await self._db.commit()
        logger.debug(f"Saved topic context episode={episode_guid} domain={topic.domain}")
