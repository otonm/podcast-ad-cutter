"""Settings routes."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from config.config_loader import SUPPORTED_PROVIDERS, AppConfig, load_config
from frontend import config_cache, config_editor
from frontend.app import templates
from frontend.config_editor import get_config_path

logger = logging.getLogger(__name__)

router = APIRouter()


def _settings_context(
    cfg: AppConfig | None,
    *,
    error: str | None,
) -> dict[str, object]:
    return {
        "cfg": cfg,
        "providers": sorted(SUPPORTED_PROVIDERS),
        "error": error,
    }


@router.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request) -> HTMLResponse:
    """Return the settings form partial with current values."""
    cfg = config_cache.get_config()
    return templates.TemplateResponse(
        request=request,
        name="partials/settings_form.html",
        context=_settings_context(cfg, error=None),
    )


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    transcription_provider: Annotated[str, Form()],
    transcription_model: Annotated[str, Form()],
    interpretation_provider: Annotated[str, Form()],
    interpretation_model: Annotated[str, Form()],
    min_confidence: Annotated[float, Form()],
    episodes_to_keep: Annotated[int, Form()],
    verbose_log: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Validate and save settings. Returns empty on success (collapses accordion)."""
    cfg: AppConfig | None = None
    error: str | None = None

    try:
        await asyncio.to_thread(
            config_editor.update_settings,
            transcription_provider=transcription_provider,
            transcription_model=transcription_model,
            interpretation_provider=interpretation_provider,
            interpretation_model=interpretation_model,
            min_confidence=min_confidence,
            episodes_to_keep=episodes_to_keep,
            verbose_log=verbose_log is not None,
        )
        validated = await asyncio.to_thread(load_config, get_config_path())
        config_cache.set_config(validated)
        _debug = verbose_log is not None
        for _name in ("pipeline", "frontend", "config"):
            logging.getLogger(_name).setLevel(logging.DEBUG if _debug else logging.NOTSET)
        # Return empty — HTMX clears #settings-accordion, collapsing the panel.
        return HTMLResponse("")
    except Exception as exc:
        logger.warning(f"Settings save failed: {exc}")
        error = str(exc)
        try:
            cfg = config_cache.get_config()
        except RuntimeError:
            cfg = None

    return templates.TemplateResponse(
        request=request,
        name="partials/settings_form.html",
        context=_settings_context(cfg, error=error),
    )
