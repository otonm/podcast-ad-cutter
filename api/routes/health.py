"""Health check route — GET /api/v1/health."""

from __future__ import annotations

import importlib.metadata
import logging
import time
import tomllib
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)


def _read_version() -> str:
    """Read the application version from package metadata or pyproject.toml (D-12).

    Tries importlib.metadata first (works when installed as a distribution).
    Falls back to reading pyproject.toml directly via tomllib (works when
    running from source without a [build-system] table).
    """
    try:
        return importlib.metadata.version("podcast-ad-cutter")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        return str(data["project"]["version"])
    except (FileNotFoundError, KeyError):
        return "unknown"


def create_health_router(start_time: float) -> web.RouteTableDef:
    """Build and return a RouteTableDef with GET /api/v1/health registered.

    Args:
        start_time: Monotonic timestamp of server start.

    Returns:
        RouteTableDef with the health handler registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/health")
    async def health(_request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "uptime_seconds": round(time.monotonic() - start_time, 2),
            "version": _read_version(),
        })

    return routes
