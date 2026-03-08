"""Shared pipeline execution: used by both the HTTP route and the scheduler."""

import asyncio
import logging

from frontend import config_cache, sse, state
from pipeline.runner import run_pipeline as _run_pipeline

logger = logging.getLogger(__name__)


async def start_pipeline(dry_run: bool = False) -> None:
    """Attach SSE handler, set running state, and launch the pipeline task.

    Caller is responsible for checking state.is_running() first.
    """
    loop = asyncio.get_running_loop()
    sse.attach_handler(loop)
    state.set_running(True)
    task = asyncio.create_task(_run_pipeline_task(dry_run))
    state.set_task(task)
    sse.notify_started()


async def _run_pipeline_task(dry_run: bool) -> None:
    """Background task: run pipeline then signal completion."""
    try:
        cfg = config_cache.get_config()
        await _run_pipeline(cfg, dry_run=dry_run)
    except asyncio.CancelledError:
        logger.info("Pipeline run cancelled")
        raise
    except Exception:
        logger.exception("Pipeline run failed")
    finally:
        state.set_running(False)
        state.set_task(None)
        queue = sse.get_active_queue()
        if queue is not None:
            queue.put_nowait(None)
        sse.detach_handler()
        sse.notify_done()
