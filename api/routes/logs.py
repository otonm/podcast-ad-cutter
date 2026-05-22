"""Log file access routes — GET /api/v1/logs/{tail:.*}."""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)


def create_logs_router(log_dir: Path) -> web.RouteTableDef:
    """Build and return a RouteTableDef for log file access handlers.

    No handlers are registered in this skeleton — they are added in plans 02 and 03.

    Args:
        log_dir: Path to the directory where log files are stored.

    Returns:
        RouteTableDef (empty skeleton) for log access handlers.

    """
    routes = web.RouteTableDef()
    return routes
