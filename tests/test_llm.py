"""Tests for shared LiteLLM utility helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import litellm
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
    exc = _bad_request("Unsupported value: {'param': 'messages', 'code': 'unsupported_value'}")
    dropped: set[str] = set()
    with (
        patch("utils.llm.litellm.acompletion", new=AsyncMock(side_effect=exc)),
        pytest.raises(litellm.BadRequestError),
    ):
        await acompletion_with_param_fallback(dropped, model="m", messages=[])
    assert dropped == set()
