"""Feed management routes — GET/POST/PATCH/DELETE /api/v1/feeds."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import TYPE_CHECKING

import yaml
from aiohttp import web
from pydantic import ValidationError
from slugify import slugify

from config.config_loader import AppConfig, FeedConfig
from database.connection import Database

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def _find_feed_by_slug(slug: str, feeds: list[FeedConfig]) -> FeedConfig | None:
    """Return the FeedConfig whose slugified title matches slug, or None."""
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed
    return None


def _write_config_sync(config_path: Path, cfg: AppConfig) -> None:
    """Write cfg to config_path atomically using a temp file on the same filesystem.

    Uses tempfile.NamedTemporaryFile with dir=config_path.parent so that the temp
    file and target are on the same filesystem, guaranteeing POSIX atomicity via
    os.replace.
    """
    data = cfg.model_dump(mode="json")
    tmp_name: str
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=config_path.parent,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        tmp_name = f.name
    os.replace(tmp_name, config_path)  # noqa: PTH105 — plan mandates os.replace for POSIX atomicity


def create_feeds_router(config_path: Path, db_path: Path) -> web.RouteTableDef:  # noqa: C901, PLR0915
    """Build and return a RouteTableDef with GET/POST/PATCH/DELETE /api/v1/feeds registered.

    Args:
        config_path: Path to the config.yaml file on disk.
        db_path: Path to the SQLite database file.

    Returns:
        RouteTableDef with feeds handlers registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/feeds")
    async def get_feeds(_request: web.Request) -> web.Response:
        with config_path.open() as f:
            raw = yaml.safe_load(f)
        cfg = AppConfig.model_validate(raw)
        result = []
        async with Database(db_path) as db:
            for feed in cfg.feeds:
                cursor = await db.conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE podcast = ?",
                    (feed.title,),
                )
                row = await cursor.fetchone()
                count = row[0] if row is not None else 0
                result.append(
                    {
                        "slug": slugify(feed.title),
                        "title": feed.title,
                        "url": feed.url,
                        "enabled": feed.enabled,
                        "episodes_to_keep": feed.episodes_to_keep,
                        "episode_count": count,
                    }
                )
        return web.json_response(result)

    @routes.post("/api/v1/feeds")
    async def post_feed(request: web.Request) -> web.Response:
        payload = await request.json()
        with config_path.open() as f:
            raw = yaml.safe_load(f)
        cfg = AppConfig.model_validate(raw)
        # Duplicate check before validation
        if any(feed.title == payload.get("title") for feed in cfg.feeds):
            raise web.HTTPConflict(
                text='{"error": "feed title already exists"}',
                content_type="application/json",
            )
        try:
            new_feed = FeedConfig.model_validate(payload)
        except ValidationError as exc:
            raise web.HTTPUnprocessableEntity(
                text=exc.json(), content_type="application/json"
            ) from exc
        existing_feeds = [f.model_dump(mode="json") for f in cfg.feeds]
        new_cfg = AppConfig.model_validate(
            {**cfg.model_dump(mode="json"), "feeds": [*existing_feeds, new_feed.model_dump(mode="json")]}
        )
        await asyncio.to_thread(_write_config_sync, config_path, new_cfg)
        logger.info(f"Feed added: {new_feed.title!r}")
        return web.json_response(new_feed.model_dump(mode="json"), status=201)

    @routes.patch("/api/v1/feeds/{slug}")
    async def patch_feed(request: web.Request) -> web.Response:
        slug = request.match_info["slug"]
        payload = await request.json()
        payload.pop("title", None)  # D-11 — title changes would break slug/DB linkage
        with config_path.open() as f:
            raw = yaml.safe_load(f)
        cfg = AppConfig.model_validate(raw)
        found = _find_feed_by_slug(slug, cfg.feeds)
        if found is None:
            raise web.HTTPNotFound(
                text='{"error": "feed not found"}',
                content_type="application/json",
            )
        existing = found.model_dump()
        merged = {**existing, **payload}
        try:
            updated_feed = FeedConfig.model_validate(merged)
        except ValidationError as exc:
            raise web.HTTPUnprocessableEntity(
                text=exc.json(), content_type="application/json"
            ) from exc
        new_feeds = [
            updated_feed if f is found else f
            for f in cfg.feeds
        ]
        new_cfg = AppConfig.model_validate(
            {**cfg.model_dump(mode="json"), "feeds": [f.model_dump(mode="json") for f in new_feeds]}
        )
        await asyncio.to_thread(_write_config_sync, config_path, new_cfg)
        logger.info(f"Feed updated: {updated_feed.title!r} (slug={slug!r})")
        return web.json_response(updated_feed.model_dump(mode="json"))

    @routes.delete("/api/v1/feeds/{slug}")
    async def delete_feed(request: web.Request) -> web.Response:
        slug = request.match_info["slug"]
        with config_path.open() as f:
            raw = yaml.safe_load(f)
        cfg = AppConfig.model_validate(raw)
        found = _find_feed_by_slug(slug, cfg.feeds)
        if found is None:
            raise web.HTTPNotFound(
                text='{"error": "feed not found"}',
                content_type="application/json",
            )
        new_feeds = [f for f in cfg.feeds if f is not found]
        try:
            new_cfg = AppConfig.model_validate(
                {**cfg.model_dump(mode="json"), "feeds": [f.model_dump(mode="json") for f in new_feeds]}
            )
        except ValidationError as exc:
            raise web.HTTPUnprocessableEntity(
                text=exc.json(), content_type="application/json"
            ) from exc
        await asyncio.to_thread(_write_config_sync, config_path, new_cfg)
        logger.info(f"Feed deleted: slug={slug!r}")
        return web.Response(status=204)

    return routes
