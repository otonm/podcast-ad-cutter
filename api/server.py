"""API server — AppRunner + TCPSite lifecycle (never web.run_app)."""

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
    raise NotImplementedError


async def serve(host: str, port: int) -> None:
    """Start the aiohttp server and keep it running until cancelled.

    Args:
        host: Host to bind to.
        port: Port to bind to.

    """
    raise NotImplementedError
