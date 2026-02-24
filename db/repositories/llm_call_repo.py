import logging

import aiosqlite

from models.llm_call import CallType, LLMCall

logger = logging.getLogger(__name__)


class LLMCallRepository:
    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, record: LLMCall) -> None:
        """Persist a single LLM call record."""
        await self._db.execute(
            "INSERT INTO llm_calls (episode_guid, call_type, model, cost_usd)"
            " VALUES (?, ?, ?, ?)",
            (record.episode_guid, str(record.call_type), record.model, record.cost_usd),
        )
        await self._db.commit()
        logger.debug(
            "Saved LLM call episode=%s type=%s model=%s cost_usd=%.6f",
            record.episode_guid,
            record.call_type,
            record.model,
            record.cost_usd,
        )

    async def get_by_episode(self, episode_guid: str) -> list[LLMCall]:
        """Return all LLM call records for an episode."""
        async with self._db.execute(
            "SELECT episode_guid, call_type, model, cost_usd"
            " FROM llm_calls WHERE episode_guid = ?",
            (episode_guid,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            LLMCall(
                episode_guid=row[0],
                call_type=CallType(row[1]),
                model=row[2],
                cost_usd=row[3],
            )
            for row in rows
        ]

    async def get_total_cost(self, episode_guid: str) -> float:
        """Return sum of all LLM costs for an episode."""
        async with self._db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls WHERE episode_guid = ?",
            (episode_guid,),
        ) as cursor:
            row = await cursor.fetchone()
        return float(row[0]) if row else 0.0
