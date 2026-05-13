"""Tests for AdDetector."""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
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

_VALID_DETECTIONS = json.dumps({"ads": [
    {"indices": [1, 2], "confidence": 0.95, "sponsor": "Acme", "ad_topic": "discount code"},
    {"indices": [5, 6, 7], "confidence": 0.99, "sponsor": "Acme", "ad_topic": "promo offer"},
]})

_EMPTY_DETECTIONS = json.dumps({"ads": []})


def _make_response(
    content: str = _VALID_DETECTIONS,
    response_cost: float | None = 0.002,
    reasoning_content: str | None = None,
    reasoning: str | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.reasoning_content = reasoning_content
    msg.reasoning = reasoning
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
    max_retries: int = 3,
    context_window: int | None = None,
) -> AdDetector:
    return AdDetector(
        provider=provider,
        model=model,
        api_key=api_key,
        max_retries=max_retries,
        context_window=context_window,
    )


@pytest.fixture(autouse=True)
def _mock_supports_reasoning() -> Generator[None, None, None]:
    with patch("components.ad_detector.litellm.supports_reasoning", return_value=True):
        yield


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
    assert detections[0].indices == [1, 2]
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


async def test_detect_passes_reasoning_effort_string_when_supported(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)) as mock_call:
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.call_args.kwargs["reasoning_effort"] == "high"


async def test_detect_omits_reasoning_when_model_unsupported(caplog: pytest.LogCaptureFixture) -> None:
    mock_resp = _make_response()
    with (
        patch("components.ad_detector.litellm.supports_reasoning", return_value=False),
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)) as mock_call,
        caplog.at_level(logging.WARNING, logger="components.ad_detector"),
    ):
        await _make_detector().detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.call_args.kwargs["reasoning_effort"] is None
    assert any("does not support reasoning" in r.message for r in caplog.records)


async def test_detect_does_not_pass_thinking_param(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)) as mock_call:
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "thinking" not in mock_call.call_args.kwargs


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
    mock_resp = _make_response(content=json.dumps({"ads": "not_a_list"}))
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message


async def test_json_missing_keys_raises_ad_detection_error(detector: AdDetector) -> None:
    mock_resp = _make_response(content=json.dumps({"ads": [{"indices": [1, 2]}]}))
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message


async def test_indices_not_list_raises_ad_detection_error(detector: AdDetector) -> None:
    """When 'indices' is not a list (e.g. a single int), parse must raise TypeError -> AdDetectionError."""
    payload = json.dumps({"ads": [
        {"indices": 1, "confidence": 0.9, "sponsor": "Acme", "ad_topic": "promo"},
    ]})
    mock_resp = _make_response(content=payload)
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


async def test_truncate_segments_when_over_budget() -> None:
    det = _make_detector(context_window=8192)
    mock_resp = _make_response()
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch(
            "components.ad_detector.litellm.token_counter",
            return_value=999999,
        ),
    ):
        _, detections, _ = await det.detect("ep-1", _SEGMENTS, _TOPIC)
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


def _make_truncated_response(response_cost: float = 0.001) -> MagicMock:
    """Simulate a response where the model ran out of tokens (finish_reason='length')."""
    resp = _make_response(content="", response_cost=response_cost)
    resp.choices[0].finish_reason = "length"
    return resp


def _make_bad_request_error(message: str) -> litellm.BadRequestError:
    return litellm.BadRequestError(message=message, model="groq/model", llm_provider="groq")


# ---------------------------------------------------------------------------
# BadRequestError / json_validate_failed retry
# ---------------------------------------------------------------------------

async def test_finish_reason_length_retries_without_reasoning(detector: AdDetector) -> None:
    """finish_reason=length on first attempt retries without reasoning and succeeds."""
    truncated = _make_truncated_response(response_cost=0.001)
    good_resp = _make_response(content=_VALID_DETECTIONS, response_cost=0.002)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[truncated, good_resp]),
    ) as mock_call:
        _, detections, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert len(detections) == 2
    assert cost.cost == pytest.approx(0.003)


async def test_finish_reason_length_exhausted_raises(detector: AdDetector) -> None:
    """finish_reason=length on every attempt raises AdDetectionError after max_retries."""
    truncated = _make_truncated_response()
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[truncated, truncated, truncated]),
        ) as mock_call,
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 3
    assert "ep-1" in exc_info.value.message


async def test_json_validate_failed_retries_without_schema(detector: AdDetector) -> None:
    """json_validate_failed on first attempt retries and succeeds on second."""
    err = _make_bad_request_error("GroqException - json_validate_failed")
    good_resp = _make_response(content=_VALID_DETECTIONS, response_cost=0.002)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[err, good_resp]),
    ) as mock_call:
        _, detections, _ = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert len(detections) == 2


async def test_json_validate_failed_exhausted_raises(detector: AdDetector) -> None:
    """json_validate_failed on every attempt raises AdDetectionError after max_retries."""
    err = _make_bad_request_error("GroqException - json_validate_failed")
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[err, err, err]),
        ) as mock_call,
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 3
    assert "ep-1" in exc_info.value.message


async def test_non_json_validate_bad_request_raises_immediately(detector: AdDetector) -> None:
    """A BadRequestError that is NOT json_validate_failed raises immediately without retrying."""
    err = _make_bad_request_error("invalid_api_key")
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=err),
        ) as mock_call,
        pytest.raises(AdDetectionError),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 1


async def test_json_validate_failed_after_parse_fail_then_succeeds(detector: AdDetector) -> None:
    """Parse failure on attempt 0, json_validate_failed on attempt 1, success on attempt 2."""
    bad_parse_resp = _make_response(content="not json", response_cost=0.001)
    err = _make_bad_request_error("GroqException - json_validate_failed")
    good_resp = _make_response(content=_VALID_DETECTIONS, response_cost=0.003)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_parse_resp, err, good_resp]),
    ) as mock_call:
        _, detections, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 3
    assert len(detections) == 2
    assert cost.cost == pytest.approx(0.004)  # 0.001 + 0.003 (schema err has no cost)


# ---------------------------------------------------------------------------
# Single-index retry
# ---------------------------------------------------------------------------

def _single_index_response(response_cost: float = 0.001) -> MagicMock:
    """Response containing an ad block with only one index."""
    content = json.dumps({"ads": [
        {"indices": [2], "confidence": 0.9, "sponsor": "Acme", "ad_topic": "promo"},
    ]})
    return _make_response(content=content, response_cost=response_cost)


def _multi_index_response(response_cost: float = 0.002) -> MagicMock:
    """Response containing ad blocks with multiple indices."""
    return _make_response(content=_VALID_DETECTIONS, response_cost=response_cost)


async def test_single_index_detection_retries_once(detector: AdDetector) -> None:
    """When a detection has only one index, one additional retry is issued."""
    single = _single_index_response(response_cost=0.001)
    multi = _multi_index_response(response_cost=0.002)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[single, multi]),
    ) as mock_call:
        _, detections, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert detections[0].indices == [1, 2]
    assert cost.cost == pytest.approx(0.003)


async def test_single_index_retry_appends_specific_prompt(detector: AdDetector) -> None:
    """The retry message for single-index results must reference 'indices'."""
    single = _single_index_response()
    multi = _multi_index_response()
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[single, multi]),
    ) as mock_call:
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    retry_msgs = mock_call.call_args_list[1].kwargs["messages"]
    user_msgs = [m["content"] for m in retry_msgs if m["role"] == "user"]
    assert any("indices" in m for m in user_msgs)


async def test_single_index_retry_not_repeated(detector: AdDetector) -> None:
    """If second response still has a single-index block, it is accepted without further retrying."""
    single1 = _single_index_response(response_cost=0.001)
    single2 = _single_index_response(response_cost=0.002)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(side_effect=[single1, single2]),
    ) as mock_call:
        _, detections, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert detections[0].indices == [2]
    assert cost.cost == pytest.approx(0.003)


async def test_single_index_no_retry_on_last_attempt(detector: AdDetector) -> None:
    """With max_retries=1, a single-index result is returned immediately without retrying."""
    det = _make_detector(max_retries=1)
    single = _single_index_response(response_cost=0.001)
    with patch(
        "components.ad_detector.litellm.acompletion",
        new=AsyncMock(return_value=single),
    ) as mock_call:
        _, detections, _ = await det.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 1
    assert detections[0].indices == [2]


# ---------------------------------------------------------------------------
# LLM reasoning logging
# ---------------------------------------------------------------------------

_INBOUNDS_DETECTIONS = json.dumps({"ads": [
    {"indices": [1, 2], "confidence": 0.95, "sponsor": "Acme", "ad_topic": "discount code"},
]})


async def test_log_llm_reasoning_emits_debug_when_present(
    detector: AdDetector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When reasoning_content is set, a DEBUG message containing the reasoning is logged."""
    mock_resp = _make_response(
        content=_INBOUNDS_DETECTIONS,
        reasoning_content="Segment 1 mentions a sponsor discount code — this is an ad.",
    )
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.ad_detector"),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert any(
        "Segment 1 mentions a sponsor discount code" in r.message
        for r in caplog.records
    )


async def test_log_llm_reasoning_silent_when_absent(
    detector: AdDetector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When reasoning_content is None, no reasoning DEBUG message is logged."""
    mock_resp = _make_response(content=_INBOUNDS_DETECTIONS, reasoning_content=None)
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.ad_detector"),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert not any("LLM reasoning" in r.message for r in caplog.records)


async def test_log_llm_reasoning_falls_back_to_reasoning_field(
    detector: AdDetector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When reasoning_content is None but reasoning is set, reasoning is still logged."""
    mock_resp = _make_response(
        content=_INBOUNDS_DETECTIONS,
        reasoning_content=None,
        reasoning="Segment 1 has a promo code.",
    )
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.ad_detector"),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert any(
        "Segment 1 has a promo code." in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Detection summary logging
# ---------------------------------------------------------------------------

async def test_log_detection_summary_logs_each_detection(
    detector: AdDetector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After successful parse, one DEBUG line per detection is logged with key fields."""
    mock_resp = _make_response(content=_INBOUNDS_DETECTIONS)
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.ad_detector"),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    ad_logs = [r.message for r in caplog.records if r.message.startswith("AD ")]
    assert len(ad_logs) == 1
    assert "[1, 2]" in ad_logs[0]
    assert "4500ms" in ad_logs[0]
    assert "35000ms" in ad_logs[0]
    assert "95%" in ad_logs[0]
    assert "Acme" in ad_logs[0]
    assert "discount code" in ad_logs[0]


async def test_log_detection_summary_logs_non_ad_indices(
    detector: AdDetector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After successful parse, non-ad segment indices are logged."""
    mock_resp = _make_response(content=_INBOUNDS_DETECTIONS)
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.ad_detector"),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    non_ad_logs = [r.message for r in caplog.records if "Non-ad segment" in r.message]
    assert len(non_ad_logs) == 1
    # _SEGMENTS has 4 segments (0-3); detection covers [1,2] → non-ad are [0, 3]
    assert "0" in non_ad_logs[0]
    assert "3" in non_ad_logs[0]


# ---------------------------------------------------------------------------
# Context window: no upfront truncation by default
# ---------------------------------------------------------------------------

async def test_detector_init_does_not_call_get_model_info() -> None:
    """AdDetector.__init__ must NOT call litellm.get_model_info (lazy resolution only)."""
    with patch("components.ad_detector.litellm.get_model_info") as mock_info:
        AdDetector(provider="openai", model="gpt-4o-mini", api_key="sk")
    mock_info.assert_not_called()


async def test_no_truncation_when_context_window_not_set() -> None:
    """When context_window is None (default), token_counter is never called."""
    det = AdDetector(provider="openai", model="gpt-4o-mini", api_key="sk")
    mock_resp = _make_response()
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch("components.ad_detector.litellm.token_counter") as mock_counter,
    ):
        await det.detect("ep-1", _SEGMENTS, _TOPIC)
    mock_counter.assert_not_called()


async def test_truncation_applied_when_context_window_set() -> None:
    """When context_window is set, token_counter is called to measure and truncate."""
    det = AdDetector(provider="openai", model="gpt-4o-mini", api_key="sk", context_window=100)
    mock_resp = _make_response()
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch("components.ad_detector.litellm.token_counter", return_value=999999) as mock_counter,
    ):
        await det.detect("ep-1", _SEGMENTS, _TOPIC)
    mock_counter.assert_called()


# ---------------------------------------------------------------------------
# Context window: reactive truncation on ContextWindowExceededError
# ---------------------------------------------------------------------------

def _make_context_window_error() -> litellm.ContextWindowExceededError:
    return litellm.ContextWindowExceededError(
        message="Input too long",
        model="gpt-4o-mini",
        llm_provider="openai",
    )


async def test_context_window_error_triggers_truncation_and_retry() -> None:
    """ContextWindowExceededError causes lazy limit resolution, truncation, and retry."""
    det = AdDetector(provider="openai", model="gpt-4o-mini", api_key="sk")
    cw_err = _make_context_window_error()
    good_resp = _make_response(content=_VALID_DETECTIONS)
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, good_resp]),
        ) as mock_call,
        patch("components.ad_detector.litellm.get_model_info", return_value=model_info),
        patch("components.ad_detector.litellm.token_counter", return_value=100),
    ):
        _, detections, _ = await det.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert len(detections) == 2


async def test_context_window_error_resets_messages_from_scratch() -> None:
    """After ContextWindowExceededError, retry uses clean 2-message history (no accumulated retries)."""
    det = AdDetector(provider="openai", model="gpt-4o-mini", api_key="sk")
    cw_err = _make_context_window_error()
    good_resp = _make_response(content=_VALID_DETECTIONS)
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, good_resp]),
        ) as mock_call,
        patch("components.ad_detector.litellm.get_model_info", return_value=model_info),
        patch("components.ad_detector.litellm.token_counter", return_value=100),
    ):
        await det.detect("ep-1", _SEGMENTS, _TOPIC)
    retry_msgs = mock_call.call_args_list[1].kwargs["messages"]
    assert len(retry_msgs) == 2
    assert retry_msgs[0]["role"] == "system"
    assert retry_msgs[1]["role"] == "user"


async def test_context_window_error_falls_back_to_8192_when_model_info_unavailable() -> None:
    """When model info is unavailable after a context window error, falls back to 8192 tokens."""
    det = AdDetector(provider="openai", model="unknown-model", api_key="sk")
    cw_err = _make_context_window_error()
    good_resp = _make_response(content=_VALID_DETECTIONS)
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, good_resp]),
        ) as mock_call,
        patch("components.ad_detector.litellm.get_model_info", side_effect=Exception("not found")),
        patch("components.ad_detector.litellm.token_counter", return_value=100),
    ):
        _, detections, _ = await det.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert len(detections) == 2


async def test_context_window_error_counts_as_retry_attempt() -> None:
    """ContextWindowExceededError consumes a retry slot; exhausting retries raises AdDetectionError."""
    det = AdDetector(provider="openai", model="gpt-4o-mini", api_key="sk", max_retries=2)
    cw_err = _make_context_window_error()
    bad_resp = _make_response(content="not json")
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, bad_resp]),
        ) as mock_call,
        patch("components.ad_detector.litellm.get_model_info", return_value=model_info),
        patch("components.ad_detector.litellm.token_counter", return_value=100),
        pytest.raises(AdDetectionError),
    ):
        await det.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2


async def test_context_window_error_on_last_attempt_raises() -> None:
    """When every attempt raises ContextWindowExceededError, AdDetectionError is raised."""
    det = AdDetector(provider="openai", model="gpt-4o-mini", api_key="sk", max_retries=2)
    cw_err = _make_context_window_error()
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.ad_detector.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, cw_err]),
        ) as mock_call,
        patch("components.ad_detector.litellm.get_model_info", return_value=model_info),
        patch("components.ad_detector.litellm.token_counter", return_value=100),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await det.detect("ep-1", _SEGMENTS, _TOPIC)
    assert mock_call.await_count == 2
    assert "ep-1" in exc_info.value.message
