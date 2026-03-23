"""Tests for transcription dataclasses."""

from __future__ import annotations

from models.transcription import Transcription, TranscriptionCost, TranscriptionSegment


def test_transcription_fields() -> None:
    t = Transcription(guid="ep1", text="Hello world")
    assert t.guid == "ep1"
    assert t.text == "Hello world"


def test_transcription_segment_fields() -> None:
    seg = TranscriptionSegment(guid="ep1", start_ms=0, end_ms=1500, text="Hello")
    assert seg.guid == "ep1"
    assert seg.start_ms == 0
    assert seg.end_ms == 1500
    assert seg.text == "Hello"


def test_transcription_cost_fields() -> None:
    cost = TranscriptionCost(provider="groq", model="whisper-large-v3-turbo", cost=0.001)
    assert cost.provider == "groq"
    assert cost.model == "whisper-large-v3-turbo"
    assert cost.cost == 0.001
