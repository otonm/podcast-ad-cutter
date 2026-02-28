"""In-process asyncio scheduler for the WebUI.

Periodically fires the pipeline for all configured feeds.
"""

import asyncio
import logging
import time

from frontend import config_cache, sse, state
from pipeline.runner import run_pipeline as _run_pipeline

logger = logging.getLogger(__name__)

_scheduler_task: asyncio.Task[None] | None = None
_next_run_at: float | None = None
_interval_minutes: int = 30


def start(interval_minutes: int) -> None:
    """Start the scheduler with the given interval, cancelling any existing run."""
    global _scheduler_task, _next_run_at, _interval_minutes
    _interval_minutes = interval_minutes
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
    _next_run_at = time.time() + interval_minutes * 60
    _scheduler_task = asyncio.create_task(_scheduler_loop(interval_minutes))
    logger.info(f"Scheduler started — interval {interval_minutes} min")


def stop() -> None:
    """Stop the scheduler."""
    global _scheduler_task, _next_run_at
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
    _next_run_at = None
    logger.info("Scheduler stopped")


def is_running() -> bool:
    """Return True if the scheduler task is active."""
    return _scheduler_task is not None and not _scheduler_task.done()


def get_next_run_at() -> float | None:
    """Return the Unix timestamp of the next scheduled run, or None if stopped."""
    return _next_run_at


def get_interval_minutes() -> int:
    """Return the current interval in minutes."""
    return _interval_minutes


def reset() -> None:
    """Restart the countdown to now + interval without changing the interval."""
    start(_interval_minutes)
    logger.debug(f"Scheduler reset — next run in {_interval_minutes} min")


async def _scheduler_loop(interval_minutes: int) -> None:
    """Repeatedly sleep until _next_run_at, then fire the pipeline."""
    global _next_run_at
    try:
        while True:
            now = time.time()
            if _next_run_at is not None:
                delay = max(0.0, _next_run_at - now)
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(interval_minutes * 60)

            if state.is_running():
                logger.info("Scheduler: pipeline already running, skipping this firing")
            else:
                logger.info("Scheduler: firing pipeline")
                await _fire_pipeline()

            _next_run_at = time.time() + interval_minutes * 60
    except asyncio.CancelledError:
        logger.debug("Scheduler loop cancelled")
        raise


async def _fire_pipeline() -> None:
    """Run the pipeline for all feeds (mirrors pipeline.py _run_pipeline_task)."""
    loop = asyncio.get_running_loop()
    sse.attach_handler(loop)

    state.set_running(True)
    task = asyncio.create_task(_run_pipeline_task())
    state.set_task(task)


async def _run_pipeline_task() -> None:
    """Background task: run pipeline then signal SSE done."""
    try:
        cfg = config_cache.get_config()
        await _run_pipeline(cfg, dry_run=False)
    except asyncio.CancelledError:
        logger.info("Scheduler-fired pipeline cancelled")
        raise
    except Exception:
        logger.exception("Scheduler-fired pipeline failed")
    finally:
        state.set_running(False)
        state.set_task(None)
        queue = sse._active_queue  # noqa: SLF001
        if queue is not None:
            queue.put_nowait(None)
        sse.detach_handler()
