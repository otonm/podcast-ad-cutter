"""Control routes — GET /api/v1/status."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from api.event_bus import EventBus
    from api.run_state import RunState
    from config.config_loader import Config

logger = logging.getLogger(__name__)


def _run_state_to_dict(rs: RunState) -> dict:  # type: ignore[type-arg]
    return {
        "state": rs.state,
        "started_at": rs.started_at.isoformat() if rs.started_at is not None else None,
        "active_feed_slug": rs.active_feed_slug,
        "current_episode_guid": rs.current_episode_guid,
        "feeds": {
            slug: {
                "episodes_total": counts.episodes_total,
                "episodes_done": counts.episodes_done,
                "episodes_failed": counts.episodes_failed,
            }
            for slug, counts in rs.feeds.items()
        },
    }


def create_control_router(
    config: Config,  # noqa: ARG001
    event_bus: EventBus,  # noqa: ARG001
    run_state: RunState,
) -> web.RouteTableDef:
    """Build and return a RouteTableDef with control endpoints registered.

    Args:
        config: Application configuration.
        event_bus: Shared event bus instance.
        run_state: Shared pipeline run state.

    Returns:
        RouteTableDef with the control handlers registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/status")
    async def status(_request: web.Request) -> web.Response:
        return web.json_response(_run_state_to_dict(run_state))

    return routes
