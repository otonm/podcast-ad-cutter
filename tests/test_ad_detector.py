"""Tests for AdDetector."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.ad_detector import AdDetector
from models.ad_detection import AdDetectionCost, AdSegmentDetection
from models.topic import TopicExtraction
from models.transcription import TranscriptionSegment
from utils.exceptions import AdDetectionError

_SEGMENTS = [
    TranscriptionSegment(guid="ep-1", start_ms=0, end_ms=4500, text="Welcome to the show."),
    TranscriptionSegment(guid="ep-1", start_ms=4500, end_ms=18000, text="Today's sponsor is Acme."),
    TranscriptionSegment(guid="ep-1", start_ms=18000, end_ms=35000, text="Use code PODCAST for 20% off."),
    TranscriptionSegment(guid="ep-1", start_ms=35000, end_ms=42000, text="Let's talk about AI today."),
]

_TOPIC = TopicExtraction(
    guid="ep-1",
    podcast="Tech Talk",
    title="AI Episode",
    topic="Hosts discuss AI advances.",
    hosts="Alice, Bob",
    show="Tech Talk",
)

_VALID_DETECTIONS = json.dumps([
    {"index": 1, "confidence": 0.95, "sponsor": "Acme", "ad_topic": "discount code"},
    {"index": 2, "confidence": 0.99, "sponsor": "Acme", "ad_topic": "promo offer"},
])

_EMPTY_DETECTIONS = json.dumps([])


def _make_response(
    content: str = _VALID_DETECTIONS,
    response_cost: float | None = 0.002,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp._hidden_params = {"response_cost": response_cost}
    return resp


def _make_detector(
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    api_key: str = "sk-test",
    max_input_tokens: int = 8192,
    max_retries: int = 3,
) -> AdDetector:
    model_info = {"max_input_tokens": max_input_tokens, "max_tokens": 4096}
    with patch("components.ad_detector.litellm.get_model_info", return_value=model_info):
        return AdDetector(provider=provider, model=model, api_key=api_key, max_retries=max_retries)


@pytest.fixture
def detector() -> AdDetector:
    return _make_detector()


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------

async def test_detect_returns_result_tuple(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
        guid, detections, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert guid == "ep-1"
    assert isinstance(detections, list)
    assert isinstance(cost, AdDetectionCost)


async def test_detect_populates_detection_fields(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
        _, detections, _ = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert len(detections) == 2
    assert isinstance(detections[0], AdSegmentDetection)
    assert detections[0].index == 1
    assert detections[0].confidence == pytest.approx(0.95)
    assert detections[0].sponsor == "Acme"
    assert detections[0].ad_topic == "discount code"


async def test_detect_empty_array_returns_empty_list(detector: AdDetector) -> None:
    mock_resp = _make_response(content=_EMPTY_DETECTIONS)
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
        _, detections, _ = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert detections == []


async def test_detect_cost_record(detector: AdDetector) -> None:
    mock_resp = _make_response(response_cost=0.005)
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
        _, _, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert cost.provider == "openai"
    assert cost.model == "gpt-4o-mini"
    assert cost.cost == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# topic_extraction=None
# ---------------------------------------------------------------------------

async def test_detect_with_none_topic_uses_placeholder(detector: AdDetector) -> None:
    mock_resp = _make_response(content=_EMPTY_DETECTIONS)
    with patch(
        "components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)
    ) as mock_call:
        await detector.detect("ep-1", _SEGMENTS, None)
    msgs = mock_call.call_args.kwargs["messages"]
    system_content = next(m["content"] for m in msgs if m["role"] == "system")
    assert "unknown" in system_content


# ---------------------------------------------------------------------------
# Model ID construction
# ---------------------------------------------------------------------------

async def test_openai_uses_bare_model_id() -> None:
    det = _make_detector(provider="openai", model="gpt-4o-mini")
    mock_resp = _make_response()
    with patch(
        "components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)
    ) as mock_call:
        await det.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.call_args.kwargs["model"] == "gpt-4o-mini"


async def test_non_openai_uses_provider_slash_model() -> None:
    det = _make_detector(provider="groq", model="llama-3.1-8b-instant")
    mock_resp = _make_response()
    with patch(
        "components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)
    ) as mock_call:
        await det.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.call_args.kwargs["model"] == "groq/llama-3.1-8b-instant"


# ---------------------------------------------------------------------------
# acompletion parameters
# ---------------------------------------------------------------------------

async def test_detect_passes_api_key(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch(
        "components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)
    ) as mock_call:
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.call_args.kwargs["api_key"] == "sk-test"


async def test_detect_segment_format_in_user_message(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch(
        "components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)
    ) as mock_call:
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    msgs = mock_call.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in msgs if m["role"] == "user")
    assert "[0][0][4500]" in user_content
    assert "[1][4500][18000]" in user_content


# ---------------------------------------------------------------------------
# Context window detection
# ---------------------------------------------------------------------------

async def test_get_model_info_not_found_falls_back_to_8192() -> None:
    with patch("components.ad_detector.litellm.get_model_info", side_effect=Exception("not found")):
        det = AdDetector(provider="openai", model="unknown-model", api_key="sk")
    assert det._max_input_tokens == 8192


# ---------------------------------------------------------------------------
# Cost extraction
# ---------------------------------------------------------------------------

async def test_cost_uses_hidden_params_when_positive(detector: AdDetector) -> None:
    mock_resp = _make_response(response_cost=0.007)
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
        _, _, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert cost.cost == pytest.approx(0.007)


async def test_cost_falls_back_to_completion_cost(detector: AdDetector) -> None:
    mock_resp = _make_response(response_cost=0.0)
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch("utils.llm.litellm.completion_cost", return_value=0.003),
    ):
        _, _, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert cost.cost == pytest.approx(0.003)


async def test_cost_returns_zero_when_completion_cost_raises(detector: AdDetector) -> None:
    mock_resp = _make_response(response_cost=None)
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch("utils.llm.litellm.completion_cost", side_effect=Exception("no pricing")),
    ):
        _, _, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert cost.cost == 0.0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

async def test_acompletion_exception_raises_ad_detection_error(detector: AdDetector) -> None:
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("API down"))),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message


async def test_malformed_json_raises_ad_detection_error(detector: AdDetector) -> None:
    mock_resp = _make_response(content="not json at all")
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message


async def test_non_list_json_raises_ad_detection_error(detector: AdDetector) -> None:
    mock_resp = _make_response(content=json.dumps({"index": 1}))
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message


async def test_json_missing_keys_raises_ad_detection_error(detector: AdDetector) -> None:
    mock_resp = _make_response(content=json.dumps([{"index": 1}]))
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message


# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------

async def test_detect_succeeds_on_first_attempt_calls_once(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch(
        "components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)
    ) as mock_call:
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 1


async def test_detect_retries_on_malformed_json_then_succeeds(detector: AdDetector) -> None:
    bad_resp = _make_response(content="not json", response_cost=0.001)
    good_resp = _make_response(content=_VALID_DETECTIONS, response_cost=0.002)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_resp, good_resp]),
    ) as mock_call:
        _, _, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert cost.cost == pytest.approx(0.003)


async def test_detect_raises_after_max_retries_exhausted(detector: AdDetector) -> None:
    bad_resp = _make_response(content="still not json")
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(return_value=bad_resp),
        ) as mock_call,
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 3
    assert "ep-1" in exc_info.value.message


async def test_detect_api_failure_does_not_retry(detector: AdDetector) -> None:
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("network error")),
        ) as mock_call,
        pytest.raises(AdDetectionError),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 1


async def test_detect_cost_accumulates_across_retries(detector: AdDetector) -> None:
    bad_resp = _make_response(content="bad", response_cost=0.001)
    good_resp = _make_response(content=_VALID_DETECTIONS, response_cost=0.005)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_resp, good_resp]),
    ):
        _, _, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert cost.cost == pytest.approx(0.006)


async def test_detect_api_failure_on_retry_raises_ad_detection_error(detector: AdDetector) -> None:
    bad_resp = _make_response(content="not json at all")
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[bad_resp, RuntimeError("network error on retry")]),
        ),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message


async def test_truncate_segments_when_over_budget(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch(
            "components.ad_detector.litellm.token_counter",
            return_value=999999,
        ),
    ):
        _, detections, _ = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert isinstance(detections, list)


async def test_cost_handles_type_error_in_float_conversion(detector: AdDetector) -> None:
    resp = _make_response(response_cost=None)
    resp._hidden_params = {"response_cost": "not-a-float-and-not-none"}
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=resp)),
        patch("utils.llm.litellm.completion_cost", return_value=0.001),
    ):
        _, _, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert cost.cost == pytest.approx(0.001)
