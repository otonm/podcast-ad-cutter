"""Helpers for extracting and calculating LLM call costs."""

import logging
from collections.abc import Mapping

import litellm

from models import CallType

LOGGER = logging.getLogger(__name__)


def extract_or_calculate_cost(
    response: object,
    provider: str,
    model: str,
    call_type: CallType,
) -> float | None:
    """Extract hidden response cost or compute it using LiteLLM helpers."""
    hidden_response_cost = _read_hidden_response_cost(response)
    if hidden_response_cost is not None:
        return hidden_response_cost

    canonical_model = f"{provider}/{model}" if "/" not in model else model

    try:
        return float(
            litellm.completion_cost(
                completion_response=response,
                model=canonical_model,
                call_type=call_type.value,
                custom_llm_provider=provider,
            )
        )
    except (ValueError, Exception) as error:
        LOGGER.warning(
            f"litellm.completion_cost failed for provider={provider} "
            f"model={canonical_model} call_type={call_type.value}: {error}"
        )

    direct_cost = _compute_cost_from_response(
        response=response,
        provider=provider,
        model=model,
        call_type=call_type,
    )
    if direct_cost is not None:
        return direct_cost

    LOGGER.warning(
        f"Unable to resolve call cost: provider={provider} model={canonical_model} call_type={call_type.value}"
    )
    return None


def _read_hidden_response_cost(response: object) -> float | None:
    hidden_params = getattr(response, "_hidden_params", None)
    if not isinstance(hidden_params, Mapping):
        return None

    raw_cost = hidden_params.get("response_cost")
    if raw_cost is None:
        return None

    try:
        return float(raw_cost)
    except (TypeError, ValueError):
        return None


def _extract_transcription_duration(response: object) -> float | None:
    """Extract audio duration in seconds from transcription response.

    Tries response.duration first, then falls back to segment end times.
    """
    duration = getattr(response, "duration", None)
    if duration is not None:
        try:
            return float(duration)
        except (TypeError, ValueError):
            pass

    segments = getattr(response, "segments", None)
    if isinstance(segments, list) and segments:
        last_segment = segments[-1]
        if isinstance(last_segment, dict):
            end = last_segment.get("end")
            if end is not None:
                try:
                    return float(end)
                except (TypeError, ValueError):
                    pass

    return None


def _extract_chat_tokens(response: object) -> tuple[int, int] | None:
    """Extract (prompt_tokens, completion_tokens) from chat response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)

    if prompt_tokens is None or completion_tokens is None:
        return None

    try:
        return int(prompt_tokens), int(completion_tokens)
    except (TypeError, ValueError):
        return None


def _compute_cost_from_response(
    response: object,
    provider: str,
    model: str,
    call_type: CallType,
) -> float | None:
    """Compute cost directly from response data and model pricing.

    Generic fallback when litellm.completion_cost fails due to provider
    mapping issues (e.g., non-OpenAI Whisper providers mapped to openai).
    """
    canonical_key = f"{provider}/{model}" if "/" not in model else model

    model_info = litellm.model_cost.get(canonical_key)
    if model_info is None:
        LOGGER.debug(f"Model {canonical_key} not in litellm.model_cost, cannot compute cost")
        return None

    input_cost_per_second = model_info.get("input_cost_per_second")
    output_cost_per_second = model_info.get("output_cost_per_second")
    input_cost_per_token = model_info.get("input_cost_per_token")
    output_cost_per_token = model_info.get("output_cost_per_token")

    if input_cost_per_second is not None or output_cost_per_second is not None:
        duration = _extract_transcription_duration(response)
        if duration is None:
            LOGGER.debug(f"Cannot extract duration for {canonical_key} {call_type.value}, cannot compute cost")
            return None
        prompt_cost = (input_cost_per_second or 0.0) * duration
        completion_cost = (output_cost_per_second or 0.0) * duration
        total = prompt_cost + completion_cost
        LOGGER.debug(f"Direct cost for {canonical_key}: {duration}s * rate = ${total:.6f}")
        return total

    if input_cost_per_token is not None or output_cost_per_token is not None:
        tokens = _extract_chat_tokens(response)
        if tokens is None:
            LOGGER.debug(f"Cannot extract tokens for {canonical_key} {call_type.value}, cannot compute cost")
            return None
        prompt_tokens, completion_tokens = tokens
        prompt_cost = (input_cost_per_token or 0.0) * prompt_tokens
        completion_cost = (output_cost_per_token or 0.0) * completion_tokens
        total = prompt_cost + completion_cost
        LOGGER.debug(f"Direct cost for {canonical_key}: {prompt_tokens}+{completion_tokens} tokens = ${total:.6f}")
        return total

    LOGGER.debug(f"No cost rate found for {canonical_key}")
    return None