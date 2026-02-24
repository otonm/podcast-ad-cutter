"""Tests for pipeline/ad_detector.py — focusing on _create_chunks()."""

from unittest.mock import AsyncMock, patch

import pytest

from config.config_loader import AppConfig
from models.ad_segment import TopicContext
from models.transcript import Segment, Transcript
from pipeline.ad_detector import _create_chunks, _detect_single
from pipeline.llm_client import fits_in_context


def _make_transcript(n_segments: int, seg_duration_ms: int = 1000) -> Transcript:
    """Build a Transcript with n_segments, each lasting seg_duration_ms."""
    segments = tuple(
        Segment(
            start_ms=i * seg_duration_ms,
            end_ms=(i + 1) * seg_duration_ms,
            text=f"word{i}",
        )
        for i in range(n_segments)
    )
    return Transcript(
        episode_guid="test-guid",
        segments=segments,
        full_text=" ".join(f"word{i}" for i in range(n_segments)),
        language="en",
        provider_model="test/model",
    )


def test_create_chunks_empty_transcript() -> None:
    transcript = _make_transcript(0)
    assert _create_chunks(transcript, chunk_duration_sec=300, overlap_sec=30) == []


def test_create_chunks_no_overlap_single_chunk() -> None:
    """200 1-second segments, 300-second chunk, no overlap → one chunk."""
    transcript = _make_transcript(200)
    chunks = _create_chunks(transcript, chunk_duration_sec=300, overlap_sec=0)
    assert len(chunks) == 1
    assert chunks[0].start_sec == 0.0
    assert chunks[0].end_sec == pytest.approx(200.0, abs=1.0)


def test_create_chunks_terminates_with_overlap() -> None:
    """Regression: 200 1-sec segments with overlap_sec=30 must not loop forever.

    Before the fix, _create_chunks() with a short transcript (200 segments)
    and overlap_sec=30 (→ subtract 300 indices) caused current_pos to reset
    to 0 each iteration, looping forever.
    """
    transcript = _make_transcript(200)
    # Should return quickly (one chunk), not hang.
    chunks = _create_chunks(transcript, chunk_duration_sec=300, overlap_sec=30)
    assert len(chunks) == 1


def test_create_chunks_multi_chunk_with_overlap() -> None:
    """600 1-sec segments, 300-sec chunks, 30-sec overlap → chunks cover full range."""
    transcript = _make_transcript(600)
    chunks = _create_chunks(transcript, chunk_duration_sec=300, overlap_sec=30)
    # First chunk starts at 0, last chunk covers the end.
    assert chunks[0].start_sec == pytest.approx(0.0)
    assert chunks[-1].end_sec == pytest.approx(600.0, abs=2.0)
    # There must be more than one chunk.
    assert len(chunks) >= 2
    # Each chunk other than the first should start before the previous one ended
    # (overlap), but not as far back as the chunk start.
    for i in range(1, len(chunks)):
        assert chunks[i].start_sec < chunks[i - 1].end_sec  # overlap present


def test_create_chunks_progress_always_advances() -> None:
    """current_pos must strictly increase each iteration — no infinite loop possible."""
    transcript = _make_transcript(50, seg_duration_ms=500)  # 25 seconds total
    chunks = _create_chunks(transcript, chunk_duration_sec=10, overlap_sec=3)
    # Verify start times are strictly increasing (progress guaranteed).
    starts = [c.start_sec for c in chunks]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts), "Duplicate chunk start times — progress stalled"


# ── fits_in_context tests ──────────────────────────────────────────────────────

def test_fits_in_context_unknown_model_returns_true() -> None:
    """When get_max_tokens returns None, assume transcript fits — no chunking."""
    messages = [{"role": "user", "content": "hello"}]
    with patch("litellm.get_max_tokens", return_value=None):
        assert fits_in_context(messages, model="ollama/custom", max_output_tokens=512) is True


def test_fits_in_context_returns_true_when_prompt_fits() -> None:
    messages = [{"role": "user", "content": "hello"}]
    with (
        patch("litellm.get_max_tokens", return_value=200_000),
        patch("litellm.token_counter", return_value=5_000),
    ):
        assert fits_in_context(messages, model="openai/gpt-4o", max_output_tokens=2048) is True


def test_fits_in_context_returns_false_when_prompt_exceeds_budget() -> None:
    """token_count=3500 > 4000 * 0.85 = 3400 → does not fit."""
    messages = [{"role": "user", "content": "hello"}]
    with (
        patch("litellm.get_max_tokens", return_value=4_000),
        patch("litellm.token_counter", return_value=3_500),
    ):
        assert fits_in_context(messages, model="ollama/llama2", max_output_tokens=512) is False


# ── _detect_single tests ───────────────────────────────────────────────────────

@pytest.fixture
def topic_ctx() -> TopicContext:
    return TopicContext(domain="tech", topic="rust programming", hosts=("Alice",), notes="")


async def test_detect_single_parses_llm_response(
    app_config: AppConfig, topic_ctx: TopicContext
) -> None:
    transcript = _make_transcript(10)
    mock_json = (
        '[{"start_sec": 10.0, "end_sec": 30.0, "confidence": 0.9, '
        '"reason": "promo code", "sponsor": "ACME"}]'
    )
    with patch(
        "pipeline.ad_detector.complete",
        new_callable=AsyncMock,
        return_value=(mock_json, 0.05),
    ):
        segments, cost = await _detect_single(transcript, topic_ctx, app_config)

    assert len(segments) == 1
    assert segments[0].start_ms == 10_000
    assert segments[0].end_ms == 30_000
    assert segments[0].confidence == pytest.approx(0.9)
    assert segments[0].sponsor_name == "ACME"
    assert cost == pytest.approx(0.05)


async def test_detect_single_returns_empty_on_llm_failure(
    app_config: AppConfig, topic_ctx: TopicContext
) -> None:
    transcript = _make_transcript(10)
    with patch(
        "pipeline.ad_detector.complete",
        new_callable=AsyncMock,
        side_effect=Exception("LLM error"),
    ):
        segments, cost = await _detect_single(transcript, topic_ctx, app_config)

    assert segments == []
    assert cost == pytest.approx(0.0)

