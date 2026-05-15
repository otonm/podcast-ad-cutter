"""SSE events route — GET /api/v1/events."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from api.event_bus import EventBus

logger = logging.getLogger(__name__)


def create_events_router(event_bus: EventBus) -> web.RouteTableDef:
    """Build and return a RouteTableDef with GET /api/v1/events registered.

    Args:
        event_bus: Shared event bus instance.

    Returns:
        RouteTableDef with the SSE handler registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/events")
    async def events(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        await resp.prepare(request)
        queue = event_bus.subscribe()
        try:
            while True:
                event = await queue.get()
                data = f"event: {event.type}\ndata: {json.dumps(event.payload)}\n\n"
                await resp.write(data.encode())
        finally:
            event_bus.unsubscribe(queue)
        return resp  # pragma: no cover

    return routes
