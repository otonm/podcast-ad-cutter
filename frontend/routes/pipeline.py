"""Pipeline control routes: run, SSE events, and status."""

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette import EventSourceResponse

from config.config_loader import load_config
from frontend import sse, state
from frontend.app import templates
from frontend.config_editor import get_config_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline")

# Module-level reference to the active SSE queue so /pipeline/events can drain it.
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
    loop = asyncio.get_event_loop()
    _active_queue = sse.attach_handler(loop)

    state.set_running(True)
    asyncio.create_task(_run_pipeline_task(dry_run))

    return templates.TemplateResponse(
        request=request,
        name="partials/progress.html",
        context={},
    )


async def _run_pipeline_task(dry_run: bool) -> None:
    """Background task: run the pipeline then enqueue None sentinel."""
    from pipeline.runner import run_pipeline as _run_pipeline

    try:
        cfg = load_config(get_config_path())
        await _run_pipeline(cfg, dry_run=dry_run)
    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")
    finally:
        state.set_running(False)
        # Signal SSE generator to close the stream.
        queue = sse._active_queue  # noqa: SLF001
        if queue is not None:
            queue.put_nowait(None)
        sse.detach_handler()


@router.get("/events")
async def pipeline_events(request: Request) -> EventSourceResponse:
    """SSE endpoint: stream log lines from the active pipeline run."""
    global _active_queue

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
async def pipeline_status() -> JSONResponse:
    """Return JSON with the current pipeline running state."""
    return JSONResponse({"running": state.is_running()})
