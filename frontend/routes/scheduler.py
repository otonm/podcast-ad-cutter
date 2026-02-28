"""Scheduler control routes: start and stop the in-process feed scheduler."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from config.config_loader import load_config
from frontend import config_cache, config_editor, scheduler, state
from frontend.app import templates
from frontend.config_editor import get_config_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scheduler")


@router.post("/start", response_class=HTMLResponse)
async def start_scheduler(
    request: Request,
    interval_minutes: Annotated[int, Form()],
) -> HTMLResponse:
    """Start the scheduler and persist the setting to config.yaml."""
    await asyncio.to_thread(
        config_editor.update_scheduler,
        enabled=True,
        interval_minutes=interval_minutes,
    )
    cfg = await asyncio.to_thread(load_config, get_config_path())
    config_cache.set_config(cfg)
    scheduler.start(interval_minutes)
    return templates.TemplateResponse(
        request=request,
        name="partials/scheduler_fieldset.html",
        context={
            "scheduler_running": True,
            "pipeline_running": state.is_running(),
            "next_run_at": scheduler.get_next_run_at(),
            "interval_minutes": interval_minutes,
        },
    )


@router.post("/stop", response_class=HTMLResponse)
async def stop_scheduler(request: Request) -> HTMLResponse:
    """Stop the scheduler and persist the setting to config.yaml."""
    interval_minutes = scheduler.get_interval_minutes()
    scheduler.stop()
    await asyncio.to_thread(
        config_editor.update_scheduler,
        enabled=False,
        interval_minutes=interval_minutes,
    )
    cfg = await asyncio.to_thread(load_config, get_config_path())
    config_cache.set_config(cfg)
    return templates.TemplateResponse(
        request=request,
        name="partials/scheduler_fieldset.html",
        context={
            "scheduler_running": False,
            "pipeline_running": False,
            "next_run_at": None,
            "interval_minutes": interval_minutes,
        },
    )


@router.get("/partial", response_class=HTMLResponse)
async def scheduler_partial(request: Request) -> HTMLResponse:
    """Return the current scheduler fieldset HTML for polling and SSE-done refresh."""
    return templates.TemplateResponse(
        request=request,
        name="partials/scheduler_fieldset.html",
        context={
            "scheduler_running": scheduler.is_running(),
            "pipeline_running": state.is_running(),
            "next_run_at": scheduler.get_next_run_at(),
            "interval_minutes": scheduler.get_interval_minutes(),
        },
    )


@router.post("/stop-pipeline", response_class=HTMLResponse)
async def stop_scheduled_pipeline(request: Request) -> HTMLResponse:
    """Cancel the current pipeline run; leave the scheduler active for the next interval."""
    task = state.get_task()
    if task is not None and not task.done():
        task.cancel()
    return templates.TemplateResponse(
        request=request,
        name="partials/scheduler_fieldset.html",
        context={
            "scheduler_running": scheduler.is_running(),
            "pipeline_running": False,
            "next_run_at": scheduler.get_next_run_at(),
            "interval_minutes": scheduler.get_interval_minutes(),
        },
    )
