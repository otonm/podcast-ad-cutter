"""API server — AppRunner + TCPSite lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import web

from api.event_bus import EventBus
from api.routes.health import create_health_router

logger = logging.getLogger(__name__)


def create_app(event_bus: EventBus, start_time: float) -> web.Application:
    """Build and return a configured aiohttp Application.

    No side effects — safe to call in tests with TestClient.

    Args:
        event_bus: Shared event bus instance.
        start_time: Monotonic timestamp of server start (for uptime calculation).

    Returns:
        Configured web.Application instance.

    """
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(start_time))
    return app


async def serve(host: str, port: int) -> None:
    """Start the aiohttp server and keep it running until cancelled.

    Uses AppRunner + TCPSite per CLAUDE.md mandate — blocking server calls
    are forbidden because they prevent sharing the asyncio event loop.

    Args:
        host: Host to bind to.
        port: Port to bind to.

    """
    start_time = time.monotonic()
    event_bus = EventBus()
    app = create_app(event_bus, start_time)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"API server listening on {host}:{port}")
        # Block until cancelled — KeyboardInterrupt in main() → asyncio.run cancels all tasks
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
