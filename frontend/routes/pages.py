"""Page routes: GET / and GET /cost."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from db.repositories.llm_call_repo import LLMCallRepository
from frontend import config_cache
from frontend.app import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the main page."""
    cfg = config_cache.get_config()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"feeds": cfg.feeds},
    )


@router.get("/cost", response_class=HTMLResponse)
async def cost_badge(request: Request) -> HTMLResponse:
    """Return the cost badge partial with the current total LLM cost."""
    total_cost = 0.0
    try:
        cfg = config_cache.get_config()
        async with get_db(cfg.paths.database) as db:
            total_cost = await LLMCallRepository(db).get_global_total_cost()
    except Exception:
        logger.debug("Could not fetch cost from database (may not exist yet)")

    return templates.TemplateResponse(
        request=request,
        name="partials/cost_badge.html",
        context={"total_cost": total_cost},
    )
