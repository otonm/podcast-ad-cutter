"""Settings routes."""

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from config.config_loader import SUPPORTED_PROVIDERS, AppConfig, load_config
from frontend import config_editor
from frontend.app import templates
from frontend.config_editor import get_config_path

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request) -> HTMLResponse:
    """Return the settings form partial with current values."""
    cfg = load_config(get_config_path())
    return templates.TemplateResponse(
        request=request,
        name="partials/settings_form.html",
        context={
            "cfg": cfg,
            "providers": sorted(SUPPORTED_PROVIDERS),
            "error": None,
            "saved": False,
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
    """Validate and save settings, then re-render the settings form."""
    cfg: AppConfig | None = None
    error: str | None = None
    saved = False

    try:
        config_editor.update_settings(
            transcription_provider=transcription_provider,
            transcription_model=transcription_model,
            interpretation_provider=interpretation_provider,
            interpretation_model=interpretation_model,
            min_confidence=min_confidence,
        )
        # Validate by loading — raises ConfigError or ValidationError on bad input.
        cfg = load_config(get_config_path())
        saved = True
    except (ValidationError, Exception) as exc:
        logger.warning(f"Settings save failed: {exc}")
        error = str(exc)
        try:
            cfg = load_config(get_config_path())
        except Exception:
            cfg = None

    return templates.TemplateResponse(
        request=request,
        name="partials/settings_form.html",
        context={
            "cfg": cfg,
            "providers": sorted(SUPPORTED_PROVIDERS),
            "error": error,
            "saved": saved,
        },
    )
