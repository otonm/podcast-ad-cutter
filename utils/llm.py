"""Shared LiteLLM utility helpers."""

from __future__ import annotations

import logging
import re

import litellm

logger = logging.getLogger(__name__)

# Fallback when the error body is lost: OpenAI's message text, e.g. "Unsupported value: 'temperature' ..."
_UNSUPPORTED_RE = re.compile(r"Unsupported (?:value|parameter): '(\w+)'")
_REQUIRED_PARAMS = frozenset({"model", "messages"})


def _rejected_param(exc: litellm.BadRequestError) -> str | None:
    """Return the request param a 400 rejects as unsupported, or None."""
    # openai-SDK errors keep the parsed body: param='temperature', code='unsupported_value'
    param = getattr(exc, "param", None)
    if param and "unsupported" in str(getattr(exc, "code", "")):
        return param
    match = _UNSUPPORTED_RE.search(str(exc))
    return match.group(1) if match else None


async def acompletion_with_param_fallback(
    dropped: set[str], **kwargs: object
) -> litellm.ModelResponse:
    """Call ``litellm.acompletion``, retrying without params the model rejects.

    ``drop_params=True`` only covers models litellm knows about. For new or
    unknown models the provider returns a 400 naming the unsupported param;
    we drop it (falling back to the provider default) and retry. Rejected
    params are added to ``dropped`` so later calls skip them up front.

    Args:
        dropped: Caller-owned set of param names to omit; mutated in place.
        **kwargs: Arguments forwarded to ``litellm.acompletion``.

    """
    while True:
        call_kwargs = {k: v for k, v in kwargs.items() if k not in dropped}
        try:
            return await litellm.acompletion(**call_kwargs)
        except litellm.BadRequestError as exc:
            param = _rejected_param(exc)
            if param not in call_kwargs or param in _REQUIRED_PARAMS:
                raise
            logger.warning(
                f"Model '{kwargs.get('model')}' rejected param '{param}', retrying without it"
            )
            dropped.add(param)


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
