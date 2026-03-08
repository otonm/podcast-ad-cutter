"""Pipeline control routes: run, stop, SSE events, and status."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator

import psutil
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sse_starlette import EventSourceResponse

from frontend import scheduler, sse, state
from frontend.app import templates
from frontend.pipeline_executor import start_pipeline

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline")


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

    sched_running = scheduler.is_running()
    if sched_running:
        scheduler.reset()

    await start_pipeline(dry_run=dry_run)

    return templates.TemplateResponse(
        request=request,
        name="partials/progress.html",
        context={
            "scheduler_running": sched_running,
            "pipeline_running": True,
            "next_run_at": scheduler.get_next_run_at(),
            "interval_minutes": scheduler.get_interval_minutes(),
            "oob_swap": sched_running,
        },
    )


@router.post("/stop", response_class=HTMLResponse)
async def stop_pipeline() -> HTMLResponse:
    """Cancel the running pipeline task and kill any spawned child processes."""
    try:
        for child in psutil.Process().children(recursive=True):
            with contextlib.suppress(psutil.NoSuchProcess):
                child.terminate()
    except Exception:
        logger.debug("Could not enumerate child processes")
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
        queue = sse.get_active_queue()
        if queue is None:
            yield {"event": "done", "data": ""}
            return
        async for event in sse.event_generator(queue):
            if await request.is_disconnected():
                break
            yield event

    return EventSourceResponse(_generator(), send_timeout=60)


@router.get("/progress", response_class=HTMLResponse)
async def pipeline_progress(request: Request) -> HTMLResponse:
    """Return the progress panel partial for browser injection on scheduler-triggered runs."""
    return templates.TemplateResponse(
        request=request,
        name="partials/progress.html",
        context={"oob_swap": False},
    )


@router.get("/status-events")
async def pipeline_status_events(request: Request) -> EventSourceResponse:
    """Stream pipeline state-change events (started/done) to the browser."""

    async def _generator() -> AsyncGenerator[dict[str, str], None]:
        q = sse.subscribe_status()
        try:
            if state.is_running():
                yield {"event": "started", "data": ""}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    if event is not None:
                        yield {"event": event, "data": ""}
                except TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            sse.unsubscribe_status(q)

    return EventSourceResponse(_generator(), send_timeout=60)


@router.get("/status")
async def pipeline_status() -> dict[str, bool]:
    """Return JSON with the current pipeline running state."""
    return {"running": state.is_running()}
