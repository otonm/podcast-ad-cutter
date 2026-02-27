"""In-memory pipeline running state.

Resets on server restart. One pipeline at a time enforced by is_running() check.
"""

import asyncio
from enum import StrEnum

# ---------------------------------------------------------------------------
# Pipeline running flag
# ---------------------------------------------------------------------------

_pipeline_running: bool = False


def is_running() -> bool:
    return _pipeline_running


def set_running(value: bool) -> None:
    global _pipeline_running
    _pipeline_running = value


# ---------------------------------------------------------------------------
# Active pipeline task (for cancellation)
# ---------------------------------------------------------------------------

_pipeline_task: asyncio.Task[None] | None = None


def set_task(task: asyncio.Task[None] | None) -> None:
    global _pipeline_task
    _pipeline_task = task


def get_task() -> asyncio.Task[None] | None:
    return _pipeline_task


# ---------------------------------------------------------------------------
# Feed status enum
# ---------------------------------------------------------------------------


class FeedStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    RUNNING = "running"
    ERROR = "error"
    DONE = "done"
