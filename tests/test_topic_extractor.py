"""Tests for TopicExtractor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import litellm
import pytest

from components.topic_extractor import TopicExtractor
from models.topic import TopicExtraction, TopicExtractionCost, TopicExtractionSchema
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
    max_retries: int = 3,
    context_window: int | None = None,
) -> TopicExtractor:
    return TopicExtractor(
        provider=provider,
        model=model,
        api_key=api_key,
        max_retries=max_retries,
        context_window=context_window,
    )


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
        guid, extraction, cost = await extractor.extract("ep-1", "my-podcast", "Ep Title", "My Show", _TRANSCRIPT)
    assert guid == "ep-1"
    assert isinstance(extraction, TopicExtraction)
    assert isinstance(cost, TopicExtractionCost)


async def test_extract_populates_topic_fields(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, extraction, _ = await extractor.extract("ep-1", "my-podcast", "Ep Title", "My Show", _TRANSCRIPT)
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
        _, _, cost = await extractor.extract("ep-1", "my-podcast", "Ep Title", "My Show", _TRANSCRIPT)
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
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    call_kwargs = mock_call.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"


async def test_non_openai_uses_provider_slash_model() -> None:
    ex = _make_extractor(provider="groq", model="llama-3.1-8b-instant")
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.call_args.kwargs["api_key"] == "sk-test"


async def test_extract_passes_json_response_format(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.call_args.kwargs["response_format"] is TopicExtractionSchema


async def test_extract_passes_messages_list(extractor: TopicExtractor) -> None:
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    msgs = mock_call.call_args.kwargs["messages"]
    assert isinstance(msgs, list)
    roles = [m["role"] for m in msgs]
    assert "system" in roles
    assert "user" in roles


# ---------------------------------------------------------------------------
# Context block in user message
# ---------------------------------------------------------------------------

async def test_build_messages_includes_context_block_with_description(extractor: TopicExtractor) -> None:
    """Context block with show, episode title, and description appears before the transcript."""
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "Episode One", "Tech Talk Weekly", _TRANSCRIPT, "AI news.")
    msgs = mock_call.call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in msgs if m["role"] == "user")
    assert "Show: Tech Talk Weekly" in user_msg
    assert "Episode title: Episode One" in user_msg
    assert "Episode description: AI news." in user_msg
    assert user_msg.index("Show:") < user_msg.index("Transcript:")


async def test_build_messages_omits_description_when_none(extractor: TopicExtractor) -> None:
    """When description is None, the description line must not appear in the user message."""
    mock_resp = _make_response()
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ) as mock_call:
        await extractor.extract("ep-1", "pod", "Episode One", "Tech Talk Weekly", _TRANSCRIPT)
    msgs = mock_call.call_args.kwargs["messages"]
    user_msg = next(m["content"] for m in msgs if m["role"] == "user")
    assert "Show: Tech Talk Weekly" in user_msg
    assert "Episode title: Episode One" in user_msg
    assert "Episode description:" not in user_msg


# ---------------------------------------------------------------------------
# Context window truncation with override
# ---------------------------------------------------------------------------

async def test_transcript_truncated_when_over_limit() -> None:
    """When context_window is set and token count exceeds it, the transcript is shortened."""
    long_transcript = "word " * 5000  # very long
    mock_resp = _make_response()
    token_counts = iter([10000, 100])
    with (
        patch("components.topic_extractor.litellm.token_counter", side_effect=lambda **_: next(token_counts)),
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ) as mock_call,
    ):
        ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk", context_window=500)
        await ex.extract("ep-1", "pod", "title", "My Show", long_transcript)

    msgs = mock_call.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in msgs if m["role"] == "user")
    assert "Transcript:\n\n" in user_content
    transcript_part = user_content.split("Transcript:\n\n", 1)[1]
    assert len(transcript_part) < len(long_transcript)


async def test_transcript_not_truncated_when_within_limit() -> None:
    """When token count is within the context_window limit, the full transcript is sent."""
    mock_resp = _make_response()
    with (
        patch("components.topic_extractor.litellm.token_counter", return_value=50),
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(return_value=mock_resp),
        ) as mock_call,
    ):
        await _make_extractor(context_window=8192).extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)

    msgs = mock_call.call_args.kwargs["messages"]
    user_content = next(m["content"] for m in msgs if m["role"] == "user")
    assert _TRANSCRIPT in user_content


# ---------------------------------------------------------------------------
# Cost extraction
# ---------------------------------------------------------------------------

async def test_cost_uses_hidden_params_when_positive(extractor: TopicExtractor) -> None:
    mock_resp = _make_response(response_cost=0.007)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        _, _, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        _, _, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        _, _, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        _, _, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        _, _, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        _, extraction, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)

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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        _, _, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 2


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

async def test_finish_reason_length_retries_without_reasoning(extractor: TopicExtractor) -> None:
    """finish_reason=length on first attempt retries without reasoning and succeeds."""
    truncated = _make_truncated_response(response_cost=0.001)
    good_resp = _make_response(content=_VALID_JSON, response_cost=0.002)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(side_effect=[truncated, good_resp]),
    ) as mock_call:
        _, extraction, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 2
    assert extraction.show == "Tech Talk"
    assert cost.cost == pytest.approx(0.003)


async def test_finish_reason_length_exhausted_raises(extractor: TopicExtractor) -> None:
    """finish_reason=length on every attempt raises TopicExtractionError after max_retries."""
    truncated = _make_truncated_response()
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[truncated, truncated, truncated]),
        ) as mock_call,
        pytest.raises(TopicExtractionError) as exc_info,
    ):
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 3
    assert "ep-1" in exc_info.value.message


async def test_json_validate_failed_retries_without_schema(extractor: TopicExtractor) -> None:
    """json_validate_failed on first attempt retries and succeeds on second."""
    err = _make_bad_request_error("GroqException - json_validate_failed")
    good_resp = _make_response(content=_VALID_JSON, response_cost=0.002)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(side_effect=[err, good_resp]),
    ) as mock_call:
        _, extraction, _ = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 2
    assert extraction.show == "Tech Talk"


async def test_json_validate_failed_exhausted_raises(extractor: TopicExtractor) -> None:
    """json_validate_failed on every attempt raises TopicExtractionError after max_retries."""
    err = _make_bad_request_error("GroqException - json_validate_failed")
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[err, err, err]),
        ) as mock_call,
        pytest.raises(TopicExtractionError) as exc_info,
    ):
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 3
    assert "ep-1" in exc_info.value.message


async def test_non_json_validate_bad_request_raises_immediately(extractor: TopicExtractor) -> None:
    """A BadRequestError that is NOT json_validate_failed raises immediately without retrying."""
    err = _make_bad_request_error("invalid_api_key")
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=err),
        ) as mock_call,
        pytest.raises(TopicExtractionError),
    ):
        await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 1


async def test_json_validate_failed_after_parse_fail_then_succeeds(extractor: TopicExtractor) -> None:
    """Parse failure on attempt 0, json_validate_failed on attempt 1, success on attempt 2."""
    bad_parse_resp = _make_response(content="not json", response_cost=0.001)
    err = _make_bad_request_error("GroqException - json_validate_failed")
    good_resp = _make_response(content=_VALID_JSON, response_cost=0.003)
    with patch(
        "components.topic_extractor.litellm.acompletion",
        new=AsyncMock(side_effect=[bad_parse_resp, err, good_resp]),
    ) as mock_call:
        _, extraction, cost = await extractor.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 3
    assert extraction.hosts == "Alice, Bob"
    assert cost.cost == pytest.approx(0.004)  # 0.001 + 0.003 (schema err has no cost)


# ---------------------------------------------------------------------------
# Context window: no upfront truncation by default
# ---------------------------------------------------------------------------

async def test_extractor_init_does_not_call_get_model_info() -> None:
    """TopicExtractor.__init__ must NOT call litellm.get_model_info (lazy resolution only)."""
    with patch("components.topic_extractor.litellm.get_model_info") as mock_info:
        TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk")
    mock_info.assert_not_called()


async def test_no_truncation_when_context_window_not_set() -> None:
    """When context_window is None (default), token_counter is never called."""
    ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk")
    mock_resp = _make_response()
    with (
        patch("components.topic_extractor.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch("components.topic_extractor.litellm.token_counter") as mock_counter,
    ):
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    mock_counter.assert_not_called()


async def test_truncation_applied_when_context_window_set() -> None:
    """When context_window is set, token_counter is called to measure and truncate."""
    ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk", context_window=100)
    mock_resp = _make_response()
    with (
        patch("components.topic_extractor.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        patch("components.topic_extractor.litellm.token_counter", return_value=999999) as mock_counter,
    ):
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
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
    ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk")
    cw_err = _make_context_window_error()
    good_resp = _make_response(content=_VALID_JSON)
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, good_resp]),
        ) as mock_call,
        patch("components.topic_extractor.litellm.get_model_info", return_value=model_info),
        patch("components.topic_extractor.litellm.token_counter", return_value=100),
    ):
        _, extraction, _ = await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 2
    assert extraction.show == "Tech Talk"


async def test_context_window_error_resets_messages_from_scratch() -> None:
    """After ContextWindowExceededError, retry uses clean 2-message history (no accumulated retries)."""
    ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk")
    cw_err = _make_context_window_error()
    good_resp = _make_response(content=_VALID_JSON)
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, good_resp]),
        ) as mock_call,
        patch("components.topic_extractor.litellm.get_model_info", return_value=model_info),
        patch("components.topic_extractor.litellm.token_counter", return_value=100),
    ):
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    retry_msgs = mock_call.call_args_list[1].kwargs["messages"]
    assert len(retry_msgs) == 2
    assert retry_msgs[0]["role"] == "system"
    assert retry_msgs[1]["role"] == "user"


async def test_context_window_error_falls_back_to_8192_when_model_info_unavailable() -> None:
    """When model info is unavailable after a context window error, falls back to 8192 tokens."""
    ex = TopicExtractor(provider="openai", model="unknown-model", api_key="sk")
    cw_err = _make_context_window_error()
    good_resp = _make_response(content=_VALID_JSON)
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, good_resp]),
        ) as mock_call,
        patch("components.topic_extractor.litellm.get_model_info", side_effect=Exception("not found")),
        patch("components.topic_extractor.litellm.token_counter", return_value=100),
    ):
        _, extraction, _ = await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 2
    assert extraction.show == "Tech Talk"


async def test_context_window_error_counts_as_retry_attempt() -> None:
    """ContextWindowExceededError consumes a retry slot; exhausting retries raises TopicExtractionError."""
    ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk", max_retries=2)
    cw_err = _make_context_window_error()
    bad_resp = _make_response(content="not json")
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, bad_resp]),
        ) as mock_call,
        patch("components.topic_extractor.litellm.get_model_info", return_value=model_info),
        patch("components.topic_extractor.litellm.token_counter", return_value=100),
        pytest.raises(TopicExtractionError),
    ):
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 2


async def test_context_window_error_on_last_attempt_raises() -> None:
    """When every attempt raises ContextWindowExceededError, TopicExtractionError is raised."""
    ex = TopicExtractor(provider="openai", model="gpt-4o-mini", api_key="sk", max_retries=2)
    cw_err = _make_context_window_error()
    model_info = {"max_input_tokens": 4096, "max_tokens": 4096}
    with (
        patch(
            "components.topic_extractor.litellm.acompletion",
            new=AsyncMock(side_effect=[cw_err, cw_err]),
        ) as mock_call,
        patch("components.topic_extractor.litellm.get_model_info", return_value=model_info),
        patch("components.topic_extractor.litellm.token_counter", return_value=100),
        pytest.raises(TopicExtractionError) as exc_info,
    ):
        await ex.extract("ep-1", "pod", "title", "My Show", _TRANSCRIPT)
    assert mock_call.await_count == 2
    assert "ep-1" in exc_info.value.message
