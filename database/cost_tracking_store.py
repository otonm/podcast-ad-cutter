"""CostTrackingStore — persists API cost records."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from models.cost import CostRecord

logger = logging.getLogger(__name__)


class CostTrackingStore:
    """Handles cost record persistence against an open aiosqlite connection.

    Expects the schema to already exist (created by Database).  Receives
    the connection rather than owning it — only Database manages the
    connection lifecycle.

    Each :meth:`save_cost` call appends a new row — there is no deduplication
    because every API call is a distinct billable event.

    Args:
        conn: An open aiosqlite connection with ``cost_tracking`` table present.

    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save_cost(self, cost: CostRecord) -> None:
        """Append a cost record to ``cost_tracking``.

        Args:
            cost: Cost record to persist.

        """
        await self._conn.execute(
            "INSERT INTO cost_tracking (provider, model, cost) VALUES (?, ?, ?)",
            (cost.provider, cost.model, cost.cost),
        )
        await self._conn.commit()
        logger.debug(f"Saved cost ${cost.cost:.6f} for {cost.provider}/{cost.model}")
