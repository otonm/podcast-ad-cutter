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
    """Read the application version from package metadata or pyproject.toml (D-12)."""
    raise NotImplementedError


def create_health_router(start_time: float) -> web.RouteTableDef:
    """Build and return a RouteTableDef with GET /api/v1/health registered.

    Args:
        start_time: Monotonic timestamp of server start.

    Returns:
        RouteTableDef with the health handler registered.

    """
    raise NotImplementedError
