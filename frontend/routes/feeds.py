"""Feed management routes."""

import logging
from typing import Any, cast
from urllib.parse import unquote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from config.config_loader import load_config
from frontend import config_cache, config_editor
from frontend.app import templates
from frontend.config_editor import get_config_path
from frontend.state import FeedStatus, is_running

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds")


def _feed_status(enabled: bool) -> str:
    """Return the display status string for a feed."""
    if is_running():
        return FeedStatus.RUNNING if enabled else FeedStatus.DISABLED
    return FeedStatus.ENABLED if enabled else FeedStatus.DISABLED


@router.get("", response_class=HTMLResponse)
async def list_feeds(request: Request) -> HTMLResponse:
    """Return the feed table rows partial."""
    cfg = config_cache.get_config()
    return templates.TemplateResponse(
        request=request,
        name="partials/feed_table.html",
        context={"feeds": cfg.feeds},
    )


@router.get("/add-form", response_class=HTMLResponse)
async def add_feed_form(request: Request) -> HTMLResponse:
    """Return the inline add-feed form row."""
    return templates.TemplateResponse(
        request=request,
        name="partials/add_feed_form.html",
        context={},
    )


@router.get("/cancel-add", response_class=HTMLResponse)
async def cancel_add(request: Request) -> HTMLResponse:  # noqa: ARG001
    """Return an empty div to replace the add-feed form row."""
    return HTMLResponse('<div id="add-feed-row"></div>')


@router.post("", response_class=HTMLResponse)
async def add_feed(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    enabled: str = Form(default=""),
) -> HTMLResponse:
    """Add a new feed and return the full updated feed table body."""
    is_enabled = enabled.lower() in ("true", "on", "1", "yes")
    config_editor.add_feed(name, url, enabled=is_enabled)
    cfg = load_config(get_config_path())
    config_cache.set_config(cfg)
    return templates.TemplateResponse(
        request=request,
        name="partials/feed_table.html",
        context={"feeds": cfg.feeds},
    )


@router.delete("/{name}", response_class=HTMLResponse)
async def delete_feed(name: str) -> HTMLResponse:
    """Delete a feed and return an empty string to remove the row."""
    config_editor.delete_feed(unquote(name))
    return HTMLResponse("")


@router.put("/reorder")
async def reorder_feeds(request: Request) -> Response:
    """Reorder feeds in config.yaml to match the provided name order."""
    body = cast(dict[str, Any], await request.json())
    names = [str(n) for n in body.get("names", [])]
    config_editor.reorder_feeds(names)
    return Response(status_code=200)


@router.put("/{name}/toggle", response_class=HTMLResponse)
async def toggle_feed(request: Request, name: str) -> HTMLResponse:
    """Toggle a feed's enabled state and return the updated row."""
    decoded_name = unquote(name)
    config_editor.toggle_feed(decoded_name)
    cfg = load_config(get_config_path())
    config_cache.set_config(cfg)
    feed = next((f for f in cfg.feeds if f.name == decoded_name), None)
    if feed is None:
        return HTMLResponse("")
    return templates.TemplateResponse(
        request=request,
        name="partials/feed_row.html",
        context={"feed": feed, "status": _feed_status(feed.enabled)},
    )
