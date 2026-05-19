"""API server — AppRunner + TCPSite lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

from api.event_bus import EventBus
from api.routes.control import create_control_router
from api.routes.db import create_db_router
from api.routes.events import create_events_router
from api.routes.feeds import create_feeds_router
from api.routes.health import create_health_router
from api.routes.settings import create_settings_router
from api.run_state import RunState

if TYPE_CHECKING:
    from pathlib import Path

    from config.config_loader import Config

logger = logging.getLogger(__name__)


def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
    config_path: Path,
) -> web.Application:
    """Build and return a configured aiohttp Application.

    No side effects — safe to call in tests with TestClient.

    Args:
        event_bus: Shared event bus instance.
        start_time: Monotonic timestamp of server start (for uptime calculation).
        run_state: Shared pipeline run state.
        config: Application configuration.
        config_path: Path to the config.yaml file on disk.

    Returns:
        Configured web.Application instance.

    """
    app = web.Application()
    app["event_bus"] = event_bus
    app["run_state"] = run_state
    app["config_path"] = config_path
    app.add_routes(create_health_router(start_time))
    app.add_routes(create_events_router(event_bus))
    app.add_routes(create_control_router(config, event_bus, run_state))
    app.add_routes(create_settings_router(config_path))
    app.add_routes(create_feeds_router(config_path, config.app.paths.data_dir / "data.db"))
    app.add_routes(create_db_router(
        config.app.paths.data_dir / "data.db",
        config.app.paths.output_dir,
        config_path,
    ))
    return app


async def serve(host: str, port: int, config: Config, config_path: Path) -> None:
    """Start the aiohttp server and keep it running until cancelled.

    Uses AppRunner + TCPSite per CLAUDE.md mandate — blocking server calls
    are forbidden because they prevent sharing the asyncio event loop.

    Args:
        host: Host to bind to.
        port: Port to bind to.
        config: Application configuration.
        config_path: Path to the config.yaml file on disk.

    """
    start_time = time.monotonic()
    event_bus = EventBus()
    run_state = RunState()
    app = create_app(event_bus, start_time, run_state, config, config_path)
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
