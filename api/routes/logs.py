"""Log file access routes — GET /api/v1/logs/{tail:.*}."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def _validate_path(log_dir: Path, tail: str) -> Path:
    """Resolve and validate that tail does not escape log_dir.

    Args:
        log_dir: The trusted root directory for log files.
        tail: The path segment from the URL match.

    Returns:
        The resolved absolute path.

    Raises:
        web.HTTPBadRequest: If the resolved path is outside log_dir (traversal).

    """
    target = (log_dir / tail).resolve()
    if not target.is_relative_to(log_dir.resolve()):
        raise web.HTTPBadRequest(text="Invalid path")
    return target


def _list_logs_sync(log_dir: Path) -> dict:
    """Build the D-01 hierarchical log listing synchronously.

    Args:
        log_dir: Path to the root log directory.

    Returns:
        Dict with app_logs (list) and episode_logs (dict keyed by feed slug).

    """
    if not log_dir.exists():
        return {"app_logs": [], "episode_logs": {}}

    def entry(f: Path) -> dict:
        s = f.stat()
        return {
            "filename": str(f.relative_to(log_dir)),
            "size_bytes": s.st_size,
            "last_modified": datetime.fromtimestamp(s.st_mtime, tz=UTC).isoformat(),
        }

    app_logs = [entry(f) for f in sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)]

    episode_logs: dict[str, list] = {}
    episodes_dir = log_dir / "episodes"
    if episodes_dir.exists():
        for feed_dir in sorted(episodes_dir.iterdir()):
            if feed_dir.is_dir():
                episode_logs[feed_dir.name] = [
                    entry(f) for f in sorted(feed_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
                ]

    return {"app_logs": app_logs, "episode_logs": episode_logs}


def create_logs_router(log_dir: Path) -> web.RouteTableDef:
    """Build and return a RouteTableDef for log file access handlers.

    Args:
        log_dir: Path to the directory where log files are stored.

    Returns:
        RouteTableDef with log access handlers registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/logs")
    async def list_logs(_request: web.Request) -> web.Response:
        result = await asyncio.to_thread(_list_logs_sync, log_dir)
        return web.json_response(result)

    # IMPORTANT: /tail route MUST be registered before the glob route (D-04).
    # aiohttp matches more specific (longer) patterns first only when registered first.
    @routes.get("/api/v1/logs/{tail:.*}/tail")
    async def tail_log(_request: web.Request) -> web.Response:
        raise web.HTTPNotImplemented

    @routes.get("/api/v1/logs/{tail:.*}")
    async def read_log(request: web.Request) -> web.Response:
        tail = request.match_info["tail"]
        log_path = _validate_path(log_dir, tail)

        if not await asyncio.to_thread(log_path.exists):
            raise web.HTTPNotFound

        try:
            raw_offset = request.rel_url.query.get("offset")
            raw_limit = request.rel_url.query.get("limit")
            offset = int(raw_offset) if raw_offset is not None else None
            limit = int(raw_limit) if raw_limit is not None else None
        except ValueError:
            raise web.HTTPBadRequest(text="offset and limit must be integers") from None

        def read_slice(path: Path) -> tuple[bytes, int, int, int]:
            data = path.read_bytes()
            total = len(data)
            start = offset or 0
            chunk = data[start : start + limit] if limit is not None else data[start:]
            return chunk, total, start, len(chunk)

        chunk, total, start, returned = await asyncio.to_thread(read_slice, log_path)

        return web.Response(
            body=chunk,
            content_type="text/plain",
            charset="utf-8",
            headers={
                "X-Log-Size": str(total),
                "X-Log-Offset": str(start),
                "X-Log-Limit": str(returned),
            },
        )

    return routes
