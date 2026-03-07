"""Tests for the /settings/models/* endpoints."""

import pytest
import respx

OPENAI_MODELS_RESPONSE = {
    "data": [
        {"id": "gpt-4o"},
        {"id": "gpt-4o-mini"},
        {"id": "whisper-1"},
        {"id": "text-embedding-3-large"},
        {"id": "dall-e-3"},
    ]
}

GROQ_MODELS_RESPONSE = {
    "data": [
        {"id": "llama-3.3-70b-versatile"},
        {"id": "whisper-large-v3"},
        {"id": "distil-whisper-large-v3-en"},
        {"id": "gemma2-9b-it"},
    ]
}

OPENROUTER_MODELS_RESPONSE = {
    "data": [
        {"id": "openai/gpt-4o"},
        {"id": "anthropic/claude-3-5-sonnet"},
    ]
}


@pytest.fixture(autouse=True)
def clear_model_cache() -> None:
    from frontend.routes.models import _model_cache

    _model_cache.clear()


@pytest.fixture(autouse=True)
def set_fake_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")


@respx.mock
async def test_transcription_models_openai() -> None:
    from frontend.routes.models import transcription_models

    respx.get("https://api.openai.com/v1/models").respond(200, json=OPENAI_MODELS_RESPONSE)
    response = await transcription_models(transcription_provider="openai")
    assert "whisper-1" in response.body.decode()
    assert "gpt-4o" not in response.body.decode()
    assert "dall-e-3" not in response.body.decode()


@respx.mock
async def test_transcription_models_groq() -> None:
    from frontend.routes.models import transcription_models

    respx.get("https://api.groq.com/openai/v1/models").respond(200, json=GROQ_MODELS_RESPONSE)
    response = await transcription_models(transcription_provider="groq")
    html = response.body.decode()
    assert "whisper-large-v3" in html
    assert "distil-whisper-large-v3-en" in html
    assert "llama-3.3-70b-versatile" not in html


@respx.mock
async def test_transcription_models_openrouter_empty() -> None:
    """OpenRouter has no whisper models → empty datalist."""
    from frontend.routes.models import transcription_models

    respx.get("https://openrouter.ai/api/v1/models").respond(200, json=OPENROUTER_MODELS_RESPONSE)
    response = await transcription_models(transcription_provider="openrouter")
    html = response.body.decode()
    # No whisper models in openrouter fixture → empty datalist
    assert "<option" not in html


@respx.mock
async def test_interpretation_models_openai() -> None:
    from frontend.routes.models import interpretation_models

    respx.get("https://api.openai.com/v1/models").respond(200, json=OPENAI_MODELS_RESPONSE)
    response = await interpretation_models(interpretation_provider="openai")
    html = response.body.decode()
    assert "gpt-4o" in html
    assert "whisper-1" not in html
    assert "text-embedding-3-large" not in html
    assert "dall-e-3" not in html


@respx.mock
async def test_interpretation_models_openrouter() -> None:
    from frontend.routes.models import interpretation_models

    respx.get("https://openrouter.ai/api/v1/models").respond(200, json=OPENROUTER_MODELS_RESPONSE)
    response = await interpretation_models(interpretation_provider="openrouter")
    html = response.body.decode()
    assert "openai/gpt-4o" in html
    assert "anthropic/claude-3-5-sonnet" in html
    assert "openai/whisper" not in html


@respx.mock
async def test_no_api_key_returns_empty_datalist(monkeypatch: pytest.MonkeyPatch) -> None:
    from frontend.routes.models import transcription_models

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = await transcription_models(transcription_provider="openai")
    assert "<option" not in response.body.decode()
    assert "datalist" in response.body.decode()


@respx.mock
async def test_api_error_returns_empty_datalist() -> None:
    from frontend.routes.models import transcription_models

    respx.get("https://api.openai.com/v1/models").respond(500)
    response = await transcription_models(transcription_provider="openai")
    assert "<option" not in response.body.decode()


@respx.mock
async def test_cache_prevents_second_api_call() -> None:
    from frontend.routes.models import transcription_models

    route = respx.get("https://api.groq.com/openai/v1/models").respond(
        200, json=GROQ_MODELS_RESPONSE
    )
    await transcription_models(transcription_provider="groq")
    await transcription_models(transcription_provider="groq")
    assert route.call_count == 1
