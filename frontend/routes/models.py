"""HTMX endpoints that return <datalist> model options per provider."""

import logging
import os
import time
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------

_PROVIDER_MODELS_URL: dict[str, str] = {
    "openai": "https://api.openai.com/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}

_PROVIDER_KEY_ENV: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

_CACHE_TTL_SECS: int = 300
_model_cache: dict[str, tuple[float, list[str]]] = {}

# Exclude from interpretation model list
_INTERPRETATION_EXCLUDE: frozenset[str] = frozenset(
    {"whisper", "embedding", "tts", "dall-e", "moderation"}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fetch_all_models(provider: str) -> list[str]:
    """Return all model IDs for a provider, using a 5-minute in-memory cache."""
    now = time.monotonic()
    if provider in _model_cache:
        ts, cached = _model_cache[provider]
        if now - ts < _CACHE_TTL_SECS:
            return cached

    url = _PROVIDER_MODELS_URL.get(provider)
    env_key = _PROVIDER_KEY_ENV.get(provider)
    if not url or not env_key:
        return []

    api_key = os.environ.get(env_key)
    if not api_key:
        logger.warning(f"No API key found for provider '{provider}' ({env_key} not set)")
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()
        models: list[str] = [m["id"] for m in data.get("data", []) if isinstance(m.get("id"), str)]
    except Exception as exc:
        logger.warning(f"Failed to fetch models for provider '{provider}': {exc}")
        return []

    _model_cache[provider] = (now, models)
    return models


def _filter(models: list[str], *, usage: Literal["transcription", "interpretation"]) -> list[str]:
    """Filter model list to those relevant for the given usage type."""
    match usage:
        case "transcription":
            return sorted(m for m in models if "whisper" in m.lower())
        case "interpretation":
            return sorted(
                m for m in models
                if not any(kw in m.lower() for kw in _INTERPRETATION_EXCLUDE)
            )


def _datalist_html(datalist_id: str, models: list[str]) -> str:
    """Render a <datalist> element with the given model IDs as options."""
    options = "".join(f'<option value="{m}"></option>' for m in models)
    return f'<datalist id="{datalist_id}">{options}</datalist>'


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/settings/models/transcription", response_class=HTMLResponse)
async def transcription_models(
    transcription_provider: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Return a <datalist> of Whisper models for the given provider."""
    provider = transcription_provider.strip()
    all_models = await _fetch_all_models(provider) if provider else []
    filtered = _filter(all_models, usage="transcription")
    return HTMLResponse(_datalist_html("transcription-models", filtered))


@router.get("/settings/models/interpretation", response_class=HTMLResponse)
async def interpretation_models(
    interpretation_provider: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Return a <datalist> of chat models for the given provider."""
    provider = interpretation_provider.strip()
    all_models = await _fetch_all_models(provider) if provider else []
    filtered = _filter(all_models, usage="interpretation")
    return HTMLResponse(_datalist_html("interpretation-models", filtered))
