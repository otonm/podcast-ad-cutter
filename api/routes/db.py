"""Read-only DB viewer routes — GET /api/v1/db/episodes|transcriptions|ads|costs."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import yaml
from aiohttp import web
from slugify import slugify

from config.config_loader import AppConfig
from database.connection import Database

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_SQL_EPISODES = """
SELECT
    e.id, e.podcast, e.guid, e.title, e.pubdate, e.skipped,
    e.url, e.description, e.explicit, e.duration, e.image_url,
    e.episode_type, e.itunes_author, e.itunes_subtitle, e.itunes_summary,
    e.content_encoded, e.link, e.author, e.itunes_title,
    e.episode_number, e.season_number, e.itunes_block, e.length, e.source_url,
    CASE
        WHEN e.skipped = 1 THEN 'skipped'
        WHEN adr.guid IS NOT NULL THEN 'processed'
        WHEN t.guid IS NOT NULL THEN 'transcribed'
        WHEN eam.guid IS NOT NULL THEN 'downloaded'
        ELSE 'pending'
    END AS pipeline_state_db
FROM episodes e
LEFT JOIN episode_audio_metadata eam ON e.guid = eam.guid
LEFT JOIN transcriptions t ON e.guid = t.guid
LEFT JOIN ad_detection_runs adr ON e.guid = adr.guid
{where}
ORDER BY e.pubdate IS NULL ASC, e.pubdate DESC
LIMIT ? OFFSET ?
"""

_EPISODE_COLUMNS = [
    "id", "podcast", "guid", "title", "pubdate", "skipped",
    "url", "description", "explicit", "duration", "image_url",
    "episode_type", "itunes_author", "itunes_subtitle", "itunes_summary",
    "content_encoded", "link", "author", "itunes_title",
    "episode_number", "season_number", "itunes_block", "length", "source_url",
    "pipeline_state_db",
]


def _resolve_slug(slug: str, feeds: list) -> str | None:
    """Return the feed title whose slugified name matches slug, or None."""
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None


def _is_complete(row_pubdate: str | None, title: str, podcast: str, output_dir: Path) -> bool:
    """Return True if the episode's output file exists on disk."""
    if row_pubdate is None:
        return False
    pub_date = datetime.fromisoformat(row_pubdate).astimezone()
    pub_date_str = pub_date.strftime("%d.%m.%Y")
    feed_slug = slugify(podcast)
    title_slug = slugify(title)
    output_feed_dir = output_dir / feed_slug
    return any(output_feed_dir.glob(f"{pub_date_str}-{title_slug}.*"))


def create_db_router(db_path: Path, output_dir: Path, config_path: Path) -> web.RouteTableDef:  # noqa: C901, PLR0915
    """Build and return a RouteTableDef with 4 read-only GET handlers registered.

    Args:
        db_path: Path to the SQLite database file.
        output_dir: Path to the processed audio output directory.
        config_path: Path to the config.yaml file on disk (for slug reverse-lookup).

    Returns:
        RouteTableDef with DB viewer handlers registered.

    """
    routes = web.RouteTableDef()

    @routes.get("/api/v1/db/episodes")
    async def get_episodes(request: web.Request) -> web.Response:
        try:
            limit = min(int(request.rel_url.query.get("limit", 50)), 200)
            offset = max(0, int(request.rel_url.query.get("offset", 0)))
        except ValueError:
            raise web.HTTPBadRequest(
                text='{"error": "offset and limit must be integers"}',
                content_type="application/json",
            ) from None

        feed_slug = request.rel_url.query.get("feed")
        podcast_title: str | None = None
        if feed_slug is not None:
            with config_path.open() as f:
                raw = yaml.safe_load(f)
            cfg = AppConfig.model_validate(raw)
            podcast_title = _resolve_slug(feed_slug, cfg.feeds)
            if podcast_title is None:
                return web.json_response([])

        if podcast_title is not None:
            sql = _SQL_EPISODES.format(where="WHERE e.podcast = ?")
            params: tuple = (podcast_title, limit, offset)
        else:
            sql = _SQL_EPISODES.format(where="")
            params = (limit, offset)

        async with Database(db_path) as db:
            cursor = await db.conn.execute(sql, params)
            rows = await cursor.fetchall()

        result = []
        for row in rows:
            row_dict = dict(zip(_EPISODE_COLUMNS, row, strict=False))
            pipeline_state_db: str = row_dict.pop("pipeline_state_db")

            if pipeline_state_db != "skipped":
                pipeline_state = (
                    "complete"
                    if _is_complete(row_dict["pubdate"], row_dict["title"], row_dict["podcast"], output_dir)
                    else pipeline_state_db
                )
            else:
                pipeline_state = "skipped"

            row_dict["feed_slug"] = slugify(row_dict["podcast"])
            row_dict["pipeline_state"] = pipeline_state
            result.append(row_dict)

        return web.json_response(result)

    @routes.get("/api/v1/db/transcriptions/{guid}")
    async def get_transcription(request: web.Request) -> web.Response:
        guid = request.match_info["guid"]
        async with Database(db_path) as db:
            async with db.conn.execute(
                "SELECT transcription FROM transcriptions WHERE guid = ?", (guid,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise web.HTTPNotFound(
                    text='{"error": "not found"}',
                    content_type="application/json",
                )
            async with db.conn.execute(
                "SELECT start_ms, end_ms, text FROM transcription_segments WHERE guid = ? ORDER BY start_ms ASC",
                (guid,),
            ) as cursor:
                segments = await cursor.fetchall()

        return web.json_response({
            "guid": guid,
            "text": row[0],
            "segments": [{"start": r[0], "end": r[1], "text": r[2]} for r in segments],
        })

    @routes.get("/api/v1/db/ads/{guid}")
    async def get_ads(request: web.Request) -> web.Response:
        guid = request.match_info["guid"]
        async with Database(db_path) as db:
            async with db.conn.execute(
                "SELECT id FROM ad_detection_runs WHERE guid = ?", (guid,)
            ) as cursor:
                run_row = await cursor.fetchone()
            if run_row is None:
                raise web.HTTPNotFound(
                    text='{"error": "not found"}',
                    content_type="application/json",
                )
            async with db.conn.execute(
                "SELECT start_ms, end_ms, confidence, sponsor, ad_topic "
                "FROM ad_segments WHERE guid = ? ORDER BY start_ms ASC",
                (guid,),
            ) as cursor:
                segs = await cursor.fetchall()

        return web.json_response({
            "guid": guid,
            "detected": True,
            "segments": [
                {"start_ms": r[0], "end_ms": r[1], "confidence": r[2], "sponsor": r[3], "ad_topic": r[4]}
                for r in segs
            ],
        })

    @routes.get("/api/v1/db/costs")
    async def get_costs(request: web.Request) -> web.Response:
        feed_slug = request.rel_url.query.get("feed")
        podcast_title: str | None = None
        if feed_slug is not None:
            with config_path.open() as f:
                raw = yaml.safe_load(f)
            cfg = AppConfig.model_validate(raw)
            podcast_title = _resolve_slug(feed_slug, cfg.feeds)
            if podcast_title is None:
                return web.json_response({"total": 0.0, "by_model": [], "by_episode": []})

        async with Database(db_path) as db:
            if podcast_title is not None:
                # Feed-filtered queries
                cursor = await db.conn.execute(
                    "SELECT SUM(ct.cost) AS total "
                    "FROM cost_tracking ct "
                    "JOIN episodes e ON ct.guid = e.guid "
                    "WHERE e.podcast = ?",
                    (podcast_title,),
                )
                total_row = await cursor.fetchone()
                total = total_row[0] if total_row and total_row[0] is not None else 0.0

                cursor = await db.conn.execute(
                    "SELECT ct.provider, ct.model, SUM(ct.cost) AS cost "
                    "FROM cost_tracking ct "
                    "JOIN episodes e ON ct.guid = e.guid "
                    "WHERE e.podcast = ? "
                    "GROUP BY ct.provider, ct.model",
                    (podcast_title,),
                )
                by_model_rows = await cursor.fetchall()

                cursor = await db.conn.execute(
                    "SELECT ct.guid, SUM(ct.cost) AS cost "
                    "FROM cost_tracking ct "
                    "JOIN episodes e ON ct.guid = e.guid "
                    "WHERE e.podcast = ? AND ct.guid IS NOT NULL "
                    "GROUP BY ct.guid",
                    (podcast_title,),
                )
                by_episode_rows = await cursor.fetchall()
            else:
                # Unfiltered queries
                cursor = await db.conn.execute("SELECT SUM(cost) AS total FROM cost_tracking")
                total_row = await cursor.fetchone()
                total = total_row[0] if total_row and total_row[0] is not None else 0.0

                cursor = await db.conn.execute(
                    "SELECT provider, model, SUM(cost) AS cost "
                    "FROM cost_tracking "
                    "GROUP BY provider, model",
                )
                by_model_rows = await cursor.fetchall()

                cursor = await db.conn.execute(
                    "SELECT guid, SUM(cost) AS cost "
                    "FROM cost_tracking "
                    "WHERE guid IS NOT NULL "
                    "GROUP BY guid",
                )
                by_episode_rows = await cursor.fetchall()

        return web.json_response({
            "total": total,
            "by_model": [{"provider": r[0], "model": r[1], "cost": r[2]} for r in by_model_rows],
            "by_episode": [{"guid": r[0], "cost": r[1]} for r in by_episode_rows],
        })

    return routes
