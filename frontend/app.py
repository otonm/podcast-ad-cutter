"""FastAPI application factory for the Podcast Ad Cutter web frontend."""

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

_TEMPLATE_DIR = Path(__file__).parent / "templates"

templates: Jinja2Templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# Jinja2 filter: convert a string to a URL-safe slug
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _NON_ALNUM.sub("-", value.lower()).strip("-")


templates.env.filters["slugify"] = _slugify


def create_app() -> FastAPI:
    """Application factory — called by uvicorn when factory=True."""
    from frontend.routes.feeds import router as feeds_router
    from frontend.routes.pages import router as pages_router
    from frontend.routes.pipeline import router as pipeline_router
    from frontend.routes.settings import router as settings_router

    app = FastAPI(title="Podcast Ad Cutter", docs_url=None, redoc_url=None)

    app.include_router(pages_router)
    app.include_router(feeds_router)
    app.include_router(pipeline_router)
    app.include_router(settings_router)

    return app
