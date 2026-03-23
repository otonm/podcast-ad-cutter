"""Tests for CostTrackingStore."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite

from database.connection import Database
from database.cost_tracking_store import CostTrackingStore
from models.transcription import TranscriptionCost

if TYPE_CHECKING:
    from pathlib import Path


def _cost(provider: str = "groq", model: str = "whisper-large-v3-turbo", cost: float = 0.001) -> TranscriptionCost:
    return TranscriptionCost(provider=provider, model=model, cost=cost)


async def test_save_cost_stores_correct_values(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    async with Database(db_path) as db:
        store = CostTrackingStore(db.conn)
        await store.save_cost(_cost(provider="groq", model="whisper-large-v3-turbo", cost=0.0042))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT provider, model, cost FROM cost_tracking")
        row = await cursor.fetchone()
    assert row == ("groq", "whisper-large-v3-turbo", 0.0042)


async def test_save_cost_allows_multiple_rows(tmp_path: Path) -> None:
    """cost_tracking has no UNIQUE constraint — each call adds a new row."""
    db_path = tmp_path / "test.db"
    async with Database(db_path) as db:
        store = CostTrackingStore(db.conn)
        await store.save_cost(_cost(cost=0.001))
        await store.save_cost(_cost(cost=0.002))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM cost_tracking")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 2
