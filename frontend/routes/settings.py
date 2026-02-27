"""Settings routes."""

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from config.config_loader import SUPPORTED_PROVIDERS, AppConfig, load_config
from frontend import config_cache, config_editor
from frontend.app import templates
from frontend.config_editor import get_config_path

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request) -> HTMLResponse:
    """Return the settings form partial with current values."""
    cfg = config_cache.get_config()
    return templates.TemplateResponse(
        request=request,
        name="partials/settings_form.html",
        context={
            "cfg": cfg,
            "providers": sorted(SUPPORTED_PROVIDERS),
            "error": None,
        },
    )


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    transcription_provider: str = Form(...),
    transcription_model: str = Form(...),
    interpretation_provider: str = Form(...),
    interpretation_model: str = Form(...),
    min_confidence: float = Form(...),
) -> HTMLResponse:
    """Validate and save settings. Returns empty on success (collapses accordion)."""
    cfg: AppConfig | None = None
    error: str | None = None

    try:
        config_editor.update_settings(
            transcription_provider=transcription_provider,
            transcription_model=transcription_model,
            interpretation_provider=interpretation_provider,
            interpretation_model=interpretation_model,
            min_confidence=min_confidence,
        )
        validated = load_config(get_config_path())
        config_cache.set_config(validated)
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
        context={
            "cfg": cfg,
            "providers": sorted(SUPPORTED_PROVIDERS),
            "error": error,
        },
    )
