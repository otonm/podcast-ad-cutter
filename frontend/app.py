"""FastAPI application factory for the Podcast Ad Cutter web frontend."""

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from watchfiles import awatch

from config.config_loader import load_config
from frontend import config_cache
from frontend.config_editor import get_config_path

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

templates: Jinja2Templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    return _NON_ALNUM.sub("-", value.lower()).strip("-")


templates.env.filters["slugify"] = _slugify


async def _watch_config() -> None:
    """Reload config whenever config.yaml is modified on disk (external edits)."""
    async for _ in awatch(get_config_path()):
        try:
            config_cache.set_config(await asyncio.to_thread(load_config, get_config_path()))
            logger.info("Config reloaded from disk")
        except Exception:
            logger.exception("Config reload failed")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Load config on startup; watch for file changes; cancel watcher on shutdown."""
    from frontend import scheduler as _scheduler

    cfg = await asyncio.to_thread(load_config, get_config_path())
    config_cache.set_config(cfg)
    if cfg.scheduler.enabled:
        _scheduler.start(cfg.scheduler.interval_minutes)
    task = asyncio.create_task(_watch_config())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    _scheduler.stop()


def create_app() -> FastAPI:
    """Application factory — called by uvicorn when factory=True."""
    from frontend.routes.feeds import router as feeds_router
    from frontend.routes.pages import router as pages_router
    from frontend.routes.pipeline import router as pipeline_router
    from frontend.routes.scheduler import router as scheduler_router
    from frontend.routes.settings import router as settings_router

    app = FastAPI(
        title="Podcast Ad Cutter",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    app.include_router(pages_router)
    app.include_router(feeds_router)
    app.include_router(pipeline_router)
    app.include_router(settings_router)
    app.include_router(scheduler_router)

    return app
