"""Tests for TopicExtractor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.topic_extractor import TopicExtractor
from models.topic import TopicExtraction, TopicExtractionCost
from utils.exceptions import TopicExtractionError

_VALID_JSON = json.dumps({
    "topic": "The hosts discuss AI advances. They explore practical applications. Safety concerns are raised.",
    "hosts": "Alice, Bob",
    "show": "Tech Talk",
})

_TRANSCRIPT = "Welcome to Tech Talk. I'm Alice and I'm Bob. Today we discuss AI."


def _make_response(
    content: str = _VALID_JSON,
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


def _make_extractor(
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    api_key: str = "sk-test",
    max_input_tokens: int = 8192,
    max_retries: int = 3,
) -> TopicExtractor:
    model_info = {"max_input_tokens": max_input_tokens, "max_tokens": 4096}
    with patch("components.topic_extractor.litellm.get_model_info", return_value=model_info):
        return TopicExtractor(provider=provider, model=model, api_key=api_key, max_retries=max_retries)


@pytest.fixture
def extractor() -> TopicExtractor:
    return _make_extractor()


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------

async def test_extract_returns_result_tuple(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ):
        guid, extraction, cost = await extractor.extract("ep-1", "my-podcast", "Ep Title", _TRANSCRIPT)
    assert guid == "ep-1"
    assert isinstance(extraction, TopicExtraction)
    assert isinstance(cost, TopicExtractionCost)


async def test_extract_populates_topic_fields(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, extraction, _ = await extractor.extract("ep-1", "my-podcast", "Ep Title", _TRANSCRIPT)
    assert extraction.guid == "ep-1"
    assert extraction.podcast == "my-podcast"
    assert extraction.title == "Ep Title"
    assert "AI advances" in extraction.topic
    assert extraction.hosts == "Alice, Bob"
    assert extraction.show == "Tech Talk"


async def test_extract_cost_record(extractor: TopicExtractor) -> None:
    mock_resp = _make_response(response_cost=0.005)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, cost = await extractor.extract("ep-1", "my-podcast", "Ep Title", _TRANSCRIPT)
    assert cost.provider == "openai"
    assert cost.model == "gpt-4o-mini"
    assert cost.cost == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# Model ID construction
# ---------------------------------------------------------------------------

async def test_openai_uses_bare_model_id() -> None:
    ex = _make_extractor(provider="openai", model="gpt-4o-mini")
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await ex.extract("ep-1", "pod", "title", _TRANSCRIPT)
    call_kwargs = mock_call.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"


async def test_non_openai_uses_provider_slash_model() -> None:
    ex = _make_extractor(provider="groq", model="llama-3.1-8b-instant")
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await ex.extract("ep-1", "pod", "title", _TRANSCRIPT)
    call_kwargs = mock_call.call_args.kwargs
    assert call_kwargs["model"] == "groq/llama-3.1-8b-instant"


# ---------------------------------------------------------------------------
# acompletion parameters
# ---------------------------------------------------------------------------

async def test_extract_passes_api_key(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.call_args.kwargs["api_key"] == "sk-test"


async def test_extract_passes_json_response_format(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.call_args.kwargs["response_format"] == {"type": "json_object"}


async def test_extract_passes_messages_list(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    msgs = mock_call.call_args.kwargs["messages"]
    assert isinstance(msgs, list)
    roles = [m["role"] for m in msgs]
    assert "system" in roles
    assert "user" in roles


# ---------------------------------------------------------------------------
# Context window detection
# ---------------------------------------------------------------------------

async def test_get_model_info_not_found_falls_back_to_8192() -> None:
    with patch(
        "components.topic_extractor.litellm.get_model_info",
        side_effect=Exception("model not found"),
    ):
        ex = TopicExtractor(provider="openai", model="unknown-model", api_key="sk")
    assert ex._max_input_tokens == 8192


async def test_get_model_info_uses_max_input_tokens() -> None:
    model_info = {"max_input_tokens": 32000, "max_tokens": 4096}
    with patch("components.topic_extractor.litellm.get_model_info", return_value=model_info):
        ex = TopicExtractor(provider="openai", model="gpt-4o", api_key="sk")
    assert ex._max_input_tokens == 32000


async def test_get_model_info_falls_back_to_max_tokens_when_no_max_input() -> None:
    model_info = {"max_tokens": 4096}
    with patch("components.topic_extractor.litellm.get_model_info", return_value=model_info):
        ex = TopicExtractor(provider="openai", model="gpt-4o", api_key="sk")
    assert ex._max_input_tokens == 4096


async def test_transcript_truncated_when_over_limit() -> None:
    """When token count exceeds limit, the transcript sent to the LLM must be shorter."""
    long_transcript = "word " * 5000  # very long

    mock_resp = _make_response()

    # token_counter returns over-limit on first call, then under-limit after truncation
    token_counts = iter([10000, 100])

    with (
        patch("components.topic_extractor.litellm.get_model_info", return_value={"max_input_tokens": 500}),
        patch("components.topic_extractor.litellm.token_counter", side_effect=lambda **_: next(token_counts)),
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ) as mock_call,
    ):
        ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk")
        await ex.extract("ep-1", "pod", "title", long_transcript)

    # The user message content must be shorter than the original transcript
    msgs = mock_call.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in msgs if m["role"] == "user")
    assert len(user_content) < len(long_transcript)


async def test_transcript_not_truncated_when_within_limit() -> None:
    """When token count is within limit, the full transcript is sent."""
    mock_resp = _make_response()
    with (
        patch("components.topic_extractor.litellm.token_counter", return_value=50),
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ) as mock_call,
    ):
        await extractor_with_8192().extract("ep-1", "pod", "title", _TRANSCRIPT)

    msgs = mock_call.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in msgs if m["role"] == "user")
    assert _TRANSCRIPT in user_content


def extractor_with_8192() -> TopicExtractor:
    return _make_extractor(max_input_tokens=8192)


# ---------------------------------------------------------------------------
# Cost extraction
# ---------------------------------------------------------------------------

async def test_cost_uses_hidden_params_when_positive(extractor: TopicExtractor) -> None:
    mock_resp = _make_response(response_cost=0.007)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert cost.cost == pytest.approx(0.007)


async def test_cost_falls_back_to_completion_cost_when_hidden_params_zero(
    extractor: TopicExtractor,
) -> None:
    mock_resp = _make_response(response_cost=0.0)
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ),
        patch("utils.llm.litellm.completion_cost", return_value=0.003),
    ):
        _, _, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert cost.cost == pytest.approx(0.003)


async def test_cost_falls_back_to_completion_cost_when_hidden_params_none(
    extractor: TopicExtractor,
) -> None:
    mock_resp = _make_response(response_cost=None)
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ),
        patch("utils.llm.litellm.completion_cost", return_value=0.004),
    ):
        _, _, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert cost.cost == pytest.approx(0.004)


async def test_cost_returns_zero_when_completion_cost_raises(extractor: TopicExtractor) -> None:
    mock_resp = _make_response(response_cost=None)
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ),
        patch(
            "utils.llm.litellm.completion_cost",
            side_effect=Exception("no pricing"),
        ),
    ):
        _, _, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert cost.cost == 0.0


async def test_cost_returns_zero_when_hidden_params_non_numeric(extractor: TopicExtractor) -> None:
    mock_resp = _make_response(response_cost="not-a-number")
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ),
        patch("utils.llm.litellm.completion_cost", side_effect=Exception("no pricing")),
    ):
        _, _, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert cost.cost == 0.0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

async def test_acompletion_exception_raises_topic_extraction_error(
    extractor: TopicExtractor,
) -> None:
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("API down")),
        ),
        pytest.raises(TopicExtractionError) as exc_info,
    ):
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert "ep-1" in exc_info.value.message


async def test_malformed_json_raises_topic_extraction_error(extractor: TopicExtractor) -> None:
    mock_resp = _make_response(content="this is not json at all")
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ),
        pytest.raises(TopicExtractionError) as exc_info,
    ):
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert "ep-1" in exc_info.value.message


async def test_json_missing_keys_raises_topic_extraction_error(extractor: TopicExtractor) -> None:
    mock_resp = _make_response(content=json.dumps({"topic": "only topic"}))
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ),
        pytest.raises(TopicExtractionError) as exc_info,
    ):
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert "ep-1" in exc_info.value.message


# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------

async def test_extract_succeeds_on_first_attempt_calls_acompletion_once(
    extractor: TopicExtractor,
) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.await_count == 1


async def test_extract_retries_on_malformed_json_then_succeeds(
    extractor: TopicExtractor,
) -> None:
    """First response is invalid JSON; second is valid — succeeds on retry."""
    bad_resp = _make_response(content="not json", response_cost=0.001)
    good_resp = _make_response(content=_VALID_JSON, response_cost=0.002)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_resp, good_resp]),
    ) as mock_call:
        _, _, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.await_count == 2
    assert cost.cost == pytest.approx(0.003)  # costs accumulated


async def test_extract_retries_on_missing_keys_then_succeeds(
    extractor: TopicExtractor,
) -> None:
    """First response is JSON with missing keys; second is valid."""
    bad_resp = _make_response(content=json.dumps({"topic": "partial"}), response_cost=0.001)
    good_resp = _make_response(content=_VALID_JSON, response_cost=0.002)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_resp, good_resp]),
    ) as mock_call:
        _, extraction, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.await_count == 2
    assert extraction.hosts == "Alice, Bob"
    assert cost.cost == pytest.approx(0.003)


async def test_extract_retry_appends_correction_messages(extractor: TopicExtractor) -> None:
    """On retry, messages list must contain the bad assistant reply and a correction user turn."""
    bad_content = "oops not json"
    bad_resp = _make_response(content=bad_content)
    good_resp = _make_response(content=_VALID_JSON)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_resp, good_resp]),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)

    retry_msgs = mock_call.call_args_list[1].kwargs["messages"]
    roles = [m["role"] for m in retry_msgs]
    # must have the original system + user, then assistant (bad) + user (correction)
    assert roles.count("assistant") == 1
    assert roles.count("user") == 2
    assistant_msg = next(m for m in retry_msgs if m["role"] == "assistant")
    assert assistant_msg["content"] == bad_content


async def test_extract_raises_after_max_retries_exhausted(extractor: TopicExtractor) -> None:
    """When all 3 attempts return bad JSON, raises TopicExtractionError."""
    bad_resp = _make_response(content="still not json")
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=bad_resp),
        ) as mock_call,
        pytest.raises(TopicExtractionError) as exc_info,
    ):
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.await_count == 3  # default max_retries
    assert "ep-1" in exc_info.value.message


async def test_extract_api_failure_does_not_retry(extractor: TopicExtractor) -> None:
    """An acompletion exception must not trigger retries — it raises immediately."""
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=RuntimeError("network error")),
        ) as mock_call,
        pytest.raises(TopicExtractionError),
    ):
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.await_count == 1


async def test_extract_custom_max_retries() -> None:
    """max_retries=1 means only one attempt — raises immediately on bad JSON."""
    ex = _make_extractor(max_retries=1)
    bad_resp = _make_response(content="bad json")
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=bad_resp),
        ) as mock_call,
        pytest.raises(TopicExtractionError),
    ):
        await ex.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.await_count == 1


async def test_extract_cost_accumulates_across_retries(extractor: TopicExtractor) -> None:
    """Total cost is the sum of all attempt costs, including failed ones."""
    bad_resp = _make_response(content="bad", response_cost=0.001)
    bad_resp2 = _make_response(content="still bad", response_cost=0.001)
    good_resp = _make_response(content=_VALID_JSON, response_cost=0.005)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_resp, bad_resp2, good_resp]),
    ):
        _, _, cost = await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert cost.cost == pytest.approx(0.007)


async def test_extract_api_failure_on_retry_raises_immediately(extractor: TopicExtractor) -> None:
    """An acompletion exception on a retry call raises TopicExtractionError immediately."""
    bad_resp = _make_response(content="not json")
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[bad_resp, RuntimeError("network error on retry")]),
        ) as mock_call,
        pytest.raises(TopicExtractionError),
    ):
        await extractor.extract("ep-1", "pod", "title", _TRANSCRIPT)
    assert mock_call.await_count == 2
