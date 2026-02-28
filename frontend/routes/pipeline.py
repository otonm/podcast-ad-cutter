"""Pipeline control routes: run, stop, SSE events, and status."""

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sse_starlette import EventSourceResponse

from frontend import config_cache, sse, state
from frontend.app import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline")

_active_queue: asyncio.Queue[str | None] | None = None


@router.post("/run", response_class=HTMLResponse)
async def run_pipeline(
    request: Request,
    dry_run: bool = False,
) -> HTMLResponse:
    """Start the pipeline in a background task and return the progress partial."""
    if state.is_running():
        return HTMLResponse(
            '<p class="text-yellow-700 text-sm">⚠ Pipeline is already running.</p>'
        )

    global _active_queue
    loop = asyncio.get_running_loop()
    _active_queue = sse.attach_handler(loop)

    state.set_running(True)
    task = asyncio.create_task(_run_pipeline_task(dry_run))
    state.set_task(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/progress.html",
        context={},
    )


async def _run_pipeline_task(dry_run: bool) -> None:
    """Background task: run the pipeline then enqueue None sentinel."""
    from pipeline.runner import run_pipeline as _run_pipeline

    try:
        cfg = config_cache.get_config()
        await _run_pipeline(cfg, dry_run=dry_run)
    except asyncio.CancelledError:
        logger.info("Pipeline cancelled by user")
        raise
    except Exception:
        logger.exception("Pipeline failed")
    finally:
        state.set_running(False)
        state.set_task(None)
        queue = sse._active_queue  # noqa: SLF001
        if queue is not None:
            queue.put_nowait(None)
        sse.detach_handler()


@router.post("/stop", response_class=HTMLResponse)
async def stop_pipeline() -> HTMLResponse:
    """Cancel the running pipeline task. The SSE 'done' event restores the UI."""
    task = state.get_task()
    if task is not None and not task.done():
        task.cancel()
    return HTMLResponse("")


@router.get("/actions", response_class=HTMLResponse)
async def pipeline_actions(request: Request) -> HTMLResponse:
    """Return the Run / Dry Run button pair partial (used to restore UI after stop)."""
    return templates.TemplateResponse(
        request=request,
        name="partials/pipeline_actions.html",
        context={},
    )


@router.get("/events")
async def pipeline_events(request: Request) -> EventSourceResponse:
    """SSE endpoint: stream log lines from the active pipeline run."""

    async def _generator() -> AsyncGenerator[dict[str, str], None]:
        queue = _active_queue
        if queue is None:
            yield {"event": "done", "data": ""}
            return
        async for event in sse.event_generator(queue):
            if await request.is_disconnected():
                break
            yield event

    return EventSourceResponse(_generator(), send_timeout=60)


@router.get("/status")
async def pipeline_status() -> dict[str, bool]:
    """Return JSON with the current pipeline running state."""
    return {"running": state.is_running()}
