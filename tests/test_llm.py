"""Tests for shared LiteLLM utility helpers."""

from __future__ import annotations

from types import SimpleNamespace

from utils.llm import extract_llm_reasoning


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
