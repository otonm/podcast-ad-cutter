"""Control routes — GET /api/v1/status, POST /api/v1/run, POST /api/v1/run/stop, POST /api/v1/feeds/{slug}/run."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from aiohttp import web
from slugify import slugify

from api.run_state import VALID_STAGES
from components.feed_publisher import FeedPublisher
from components.pipeline import Pipeline
from config.config_loader import load_config
from database.connection import Database
from database.episode_store import EpisodeStore
from utils.exceptions import ConfigError

if TYPE_CHECKING:
    from pathlib import Path

    from api.event_bus import EventBus
    from api.run_state import RunState
    from config.config_loader import Config

logger = logging.getLogger(__name__)


def _resolve_slug(slug: str, feeds: list) -> str | None:  # type: ignore[type-arg]
    """Return the feed title whose slugified title matches slug, or None."""
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None


async def _run_pipeline_task(pipeline: Pipeline, run_state: RunState) -> None:
    """Wrap pipeline.run() with lifecycle reset and CancelledError re-raise."""
    try:
        await pipeline.run()
    except asyncio.CancelledError:
        logger.info("Pipeline task cancelled (force stop)")
        raise
    except Exception:
        logger.exception("Pipeline run failed")
    finally:
        run_state.reset_to_idle()


async def _load_run_config(config_path: Path) -> Config:
    """Re-read config.yaml so settings and feed edits apply to the run being started.

    Raises:
        web.HTTPUnprocessableEntity: When the config file fails to load or validate.

    """
    try:
        return await asyncio.to_thread(load_config, config_path)
    except ConfigError as exc:
        raise web.HTTPUnprocessableEntity(
            text=json.dumps({"error": str(exc)}),
            content_type="application/json",
        ) from exc


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


def create_control_router(  # noqa: C901, PLR0915
    config_path: Path,
    db_path: Path,
    output_dir: Path,
    event_bus: EventBus,
    run_state: RunState,
) -> web.RouteTableDef:
    """Build and return a RouteTableDef with control endpoints registered.

    Args:
        config_path: Path to config.yaml, re-read at every run start.
        db_path: Path to the SQLite database file.
        output_dir: Processed audio output directory (episode delete removes files here).
        event_bus: Shared event bus instance.
        run_state: Shared pipeline run state.

    Returns:
        RouteTableDef with the control handlers registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/status")
    async def status(_request: web.Request) -> web.Response:
        return web.json_response(_run_state_to_dict(run_state))

    @routes.post("/api/v1/run")
    async def start_run(_request: web.Request) -> web.Response:
        if run_state.state != "idle":
            raise web.HTTPConflict(
                text='{"error": "a run is already active"}',
                content_type="application/json",
            )
        run_state.state = "running"  # claim the slot before awaiting, so a concurrent request gets 409
        try:
            config = await _load_run_config(config_path)
        except web.HTTPException:
            run_state.reset_to_idle()
            raise
        run_state.started_at = datetime.now(UTC)
        run_state.active_feed_slug = None
        run_state.stop_event.clear()
        run_state.feeds.clear()
        pipeline = Pipeline(
            config,
            feed_name=None,
            event_bus=event_bus,
            stop_event=run_state.stop_event,
            run_state=run_state,
        )
        run_state.task = asyncio.create_task(_run_pipeline_task(pipeline, run_state))
        return web.json_response(
            {"status": "started", "started_at": run_state.started_at.isoformat()},
            status=202,
        )

    @routes.post("/api/v1/run/stop")
    async def stop_run(request: web.Request) -> web.Response:
        if run_state.state == "idle":
            raise web.HTTPConflict(
                text='{"error": "no run is active"}',
                content_type="application/json",
            )
        force = request.rel_url.query.get("force", "").lower() == "true"
        if force and run_state.task is not None:
            run_state.task.cancel()
            return web.json_response({"status": "stopping", "mode": "force"}, status=202)
        run_state.stop_event.set()
        run_state.state = "stopping"
        return web.json_response({"status": "stopping", "mode": "graceful"}, status=202)

    @routes.post("/api/v1/feeds/{slug}/run")
    async def start_feed_run(request: web.Request) -> web.Response:
        if run_state.state != "idle":
            raise web.HTTPConflict(
                text='{"error": "a run is already active"}',
                content_type="application/json",
            )
        slug = request.match_info["slug"]
        run_state.state = "running"  # claim the slot before awaiting, so a concurrent request gets 409
        try:
            config = await _load_run_config(config_path)
            feed_title = _resolve_slug(slug, config.app.feeds)
            if feed_title is None:
                raise web.HTTPNotFound(
                    text='{"error": "feed not found"}',
                    content_type="application/json",
                )
        except web.HTTPException:
            run_state.reset_to_idle()
            raise
        run_state.started_at = datetime.now(UTC)
        run_state.active_feed_slug = slug
        run_state.stop_event.clear()
        run_state.feeds.clear()
        pipeline = Pipeline(
            config,
            feed_name=feed_title,
            event_bus=event_bus,
            stop_event=run_state.stop_event,
            run_state=run_state,
        )
        run_state.task = asyncio.create_task(_run_pipeline_task(pipeline, run_state))
        return web.json_response(
            {
                "status": "started",
                "feed": slug,
                "started_at": run_state.started_at.isoformat(),
            },
            status=202,
        )

    @routes.post("/api/v1/episodes/{guid}/skip")
    async def skip_episode_handler(request: web.Request) -> web.Response:
        if run_state.state != "idle":
            raise web.HTTPConflict(
                text='{"error": "cannot modify episodes while a run is active"}',
                content_type="application/json",
            )
        guid = request.match_info["guid"]
        async with Database(db_path) as db:
            store = EpisodeStore(db.conn)
            ok = await store.skip_episode(guid)
        if not ok:
            raise web.HTTPNotFound(
                text=f'{{"error": "episode not found: {guid}"}}',
                content_type="application/json",
            )
        return web.json_response({"status": "skipped", "guid": guid})

    @routes.delete("/api/v1/episodes/{guid}/skip")
    async def unskip_episode_handler(request: web.Request) -> web.Response:
        if run_state.state != "idle":
            raise web.HTTPConflict(
                text='{"error": "cannot modify episodes while a run is active"}',
                content_type="application/json",
            )
        guid = request.match_info["guid"]
        async with Database(db_path) as db:
            store = EpisodeStore(db.conn)
            ok = await store.unskip_episode(guid)
        if not ok:
            raise web.HTTPNotFound(
                text=json.dumps({"error": f"episode not found: {guid}"}),
                content_type="application/json",
            )
        return web.json_response({"status": "unskipped", "guid": guid})

    @routes.delete("/api/v1/episodes/{guid}")
    async def delete_episode_handler(request: web.Request) -> web.Response:
        if run_state.state != "idle":
            raise web.HTTPConflict(
                text='{"error": "cannot modify episodes while a run is active"}',
                content_type="application/json",
            )
        guid = request.match_info["guid"]
        async with Database(db_path) as db:
            store = EpisodeStore(db.conn)
            location = await store.get_episode_location(guid)
            if location is None:
                raise web.HTTPNotFound(
                    text=json.dumps({"error": f"episode not found: {guid}"}),
                    content_type="application/json",
                )
            await store.skip_episode(guid)
            await store.reset_episode(guid)
        podcast, title, pubdate = location
        removed: list[str] = []
        if pubdate is not None:
            pattern = FeedPublisher.episode_glob(datetime.fromisoformat(pubdate), title)
            for path in sorted((output_dir / slugify(podcast)).glob(pattern)):
                path.unlink()
                removed.append(path.name)
        # ponytail: RSS still lists the item until the next run regenerates it;
        # add FeedPublisher.remove_episode if the gap matters
        logger.info(f"Episode {guid!r}: deleted, removed files {removed}")
        return web.json_response({"status": "deleted", "guid": guid, "files_removed": removed})

    @routes.post("/api/v1/episodes/{guid}/reprocess")
    async def reprocess_handler(request: web.Request) -> web.Response:
        if run_state.state != "idle":
            raise web.HTTPConflict(
                text='{"error": "cannot modify episodes while a run is active"}',
                content_type="application/json",
            )
        guid = request.match_info["guid"]
        stage = request.rel_url.query.get("stage")
        if stage is not None and stage not in VALID_STAGES:
            raise web.HTTPUnprocessableEntity(
                text=f'{{"error": "invalid stage: {stage}"}}',
                content_type="application/json",
            )
        async with Database(db_path) as db:
            store = EpisodeStore(db.conn)
            ok = await store.reset_episode(guid, from_stage=stage)
        if not ok:
            raise web.HTTPNotFound(
                text=f'{{"error": "episode not found: {guid}"}}',
                content_type="application/json",
            )
        return web.json_response({"status": "reset", "guid": guid, "from_stage": stage})

    return routes
