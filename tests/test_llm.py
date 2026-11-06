"""Tests for shared LiteLLM utility helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import litellm
import openai
import pytest

from utils.llm import acompletion_with_param_fallback, extract_llm_reasoning


def _make_response(**msg_attrs: object) -> SimpleNamespace:
    msg = SimpleNamespace(**msg_attrs)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def test_extract_reasoning_content_field() -> None:
    """Returns reasoning_content when present."""
    resp = _make_response(reasoning_content="thinking here")
    assert extract_llm_reasoning(resp) == "thinking here"


def test_extract_reasoning_field_fallback() -> None:
    """Falls back to reasoning when reasoning_content is absent."""
    resp = _make_response(reasoning="thinking here via reasoning field")
    assert extract_llm_reasoning(resp) == "thinking here via reasoning field"


def test_extract_returns_none_when_neither_field_present() -> None:
    """Returns None when neither reasoning_content nor reasoning is present."""
    resp = _make_response()
    assert extract_llm_reasoning(resp) is None


def test_extract_falls_through_empty_reasoning_content() -> None:
    """Empty string reasoning_content falls through to reasoning field."""
    resp = _make_response(reasoning_content="", reasoning="actual thinking")
    assert extract_llm_reasoning(resp) == "actual thinking"


# ── acompletion_with_param_fallback ──────────────────────────────────────────

_TEMP_ERR = (
    "Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' does not "
    'support 0.5 with this model. Only the default (1) value is supported.", '
    "'type': 'invalid_request_error', 'param': 'temperature', 'code': 'unsupported_value'}}"
)

# What OpenAI actually puts in error.message; litellm keeps only this text in str(exc)
_OPENAI_TEMP_MSG = (
    "Unsupported value: 'temperature' does not support 0.5 with this model. "
    "Only the default (1) value is supported."
)


def _bad_request(message: str) -> litellm.BadRequestError:
    return litellm.BadRequestError(message=message, model="m", llm_provider="openai")


async def test_fallback_drops_rejected_param_and_remembers_it() -> None:
    """Retries without the rejected param and skips it on later calls."""
    mock = AsyncMock(side_effect=[_bad_request(_TEMP_ERR), "ok", "ok2"])
    dropped: set[str] = set()
    with patch("utils.llm.litellm.acompletion", new=mock):
        assert await acompletion_with_param_fallback(dropped, model="m", temperature=0.5) == "ok"
        assert await acompletion_with_param_fallback(dropped, model="m", temperature=0.5) == "ok2"
    assert dropped == {"temperature"}
    assert "temperature" in mock.call_args_list[0].kwargs
    assert "temperature" not in mock.call_args_list[1].kwargs
    assert "temperature" not in mock.call_args_list[2].kwargs


async def test_fallback_reraises_unrelated_bad_request() -> None:
    """A 400 that doesn't name an unsupported param propagates unchanged."""
    exc = _bad_request("json_validate_failed")
    with (
        patch("utils.llm.litellm.acompletion", new=AsyncMock(side_effect=exc)),
        pytest.raises(litellm.BadRequestError),
    ):
        await acompletion_with_param_fallback(set(), model="m", temperature=0.5)


async def test_fallback_never_drops_required_params() -> None:
    """An unsupported 'model' or 'messages' error is not retried."""
    exc = _bad_request("Unsupported value: 'messages' is not supported with this model.")
    dropped: set[str] = set()
    with (
        patch("utils.llm.litellm.acompletion", new=AsyncMock(side_effect=exc)),
        pytest.raises(litellm.BadRequestError),
    ):
        await acompletion_with_param_fallback(dropped, model="m", messages=[])
    assert dropped == set()


async def test_fallback_end_to_end_through_litellm_exception_mapping() -> None:
    """The real litellm-wrapped OpenAI 400 is recognised (its message lacks the raw body)."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": _OPENAI_TEMP_MSG, "type": "invalid_request_error",
                                                       "param": "temperature", "code": "unsupported_value"}})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "gpt-5.4-nano",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    client = openai.AsyncOpenAI(
        api_key="x", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    dropped: set[str] = set()
    async with client:
        response = await acompletion_with_param_fallback(
            dropped, model="gpt-5.4-nano", messages=[{"role": "user", "content": "hi"}],
            temperature=0.5, api_key="x", client=client,
        )
    assert response.choices[0].message.content == "ok"
    assert dropped == {"temperature"}
    assert [("temperature" in b) for b in sent] == [True, False]
