"""Shared LiteLLM utility helpers."""

from __future__ import annotations

import logging

import litellm

logger = logging.getLogger(__name__)


def compute_completion_cost(response: object, model_id: str) -> float:
    """Extract cost from a litellm chat-completion response.

    Tries ``_hidden_params["response_cost"]`` first; falls back to
    ``litellm.completion_cost()``.

    Args:
        response: The litellm completion response object.
        model_id: Model identifier used in log and warning messages.

    Returns:
        Cost in USD, or ``0.0`` if it cannot be determined.

    """
    hidden = getattr(response, "_hidden_params", {}) or {}
    raw = hidden.get("response_cost")
    if raw is not None:
        try:
            val = float(raw)
        except (TypeError, ValueError):
            pass
        else:
            if val > 0.0:
                logger.debug(f"LLM cost for {model_id} is {val}")
                return val

    try:
        cost = litellm.completion_cost(completion_response=response)
        logger.debug(f"LLM cost for {model_id} (completion_cost fallback) is {cost}")
        return float(cost)
    except Exception as exc:  # noqa: BLE001 — litellm may raise for unknown models
        logger.warning(
            f"Could not compute cost for {model_id} ({exc}), cost will be $0.00"
        )
        return 0.0


def extract_llm_reasoning(response: object) -> str | None:
    """Extract reasoning/thinking text from a completion response.

    Tries reasoning_content (Anthropic, Deepseek) then reasoning (Alibaba/Qwen
    and other providers that do not normalise the field name).

    Args:
        response: The litellm completion response object.

    Returns:
        The reasoning text as a string, or ``None`` if no reasoning field is present.

    """
    msg = response.choices[0].message  # type: ignore[union-attr]
    return getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
