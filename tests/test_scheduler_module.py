# tests/test_scheduler_module.py
import asyncio
import time
from collections.abc import AsyncGenerator

import pytest

import frontend.scheduler as sched


@pytest.fixture(autouse=True)
async def clean_scheduler() -> AsyncGenerator[None, None]:
    sched.stop()
    sched._interval_minutes = 30
    sched._next_run_at = None
    yield
    sched.stop()
    await asyncio.sleep(0)


async def test_reset_updates_next_run_at() -> None:
    sched._interval_minutes = 10
    sched._next_run_at = 0.0

    before = time.time()
    sched.reset()
    after = time.time()

    assert sched._next_run_at is not None
    assert before + 600 <= sched._next_run_at <= after + 600


async def test_reset_preserves_interval() -> None:
    sched._interval_minutes = 15
    sched.reset()

    assert sched._interval_minutes == 15


async def test_reset_replaces_scheduler_task() -> None:
    sched.start(5)
    original_task = sched._scheduler_task
    await asyncio.sleep(0)

    sched.reset()

    assert sched._scheduler_task is not original_task
