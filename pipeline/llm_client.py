import logging
from pathlib import Path
from typing import Any

import litellm
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from config_loader import InterpretationConfig, TranscriptionConfig
from pipeline.exceptions import LLMError, TranscriptionError

logger = logging.getLogger(__name__)
litellm.suppress_debug_info = True
litellm.drop_params = True  # drop unsupported params (e.g. timestamp_granularities on Groq)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def complete(
    messages: list[dict[str, str]],
    cfg: InterpretationConfig,
    *,
    response_format: dict[str, str] | None = None,
) -> tuple[str, float]:
    """Return (content, cost_usd) of the first completion choice."""
    kwargs: dict[str, Any] = {
        "model": cfg.provider_model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.api_base:
        kwargs["api_base"] = cfg.api_base
    if response_format:
        kwargs["response_format"] = response_format

    logger.debug("LLM request model=%s messages=%d", cfg.provider_model, len(messages))
    try:
        response = await litellm.acompletion(**kwargs)
    except litellm.APIError as exc:
        raise LLMError(f"LLM call failed: {exc}") from exc

    content: str = response.choices[0].message.content or ""
    cost: float = response._hidden_params.get("response_cost") or 0.0  # type: ignore[union-attr]
    logger.debug(
        "LLM response model=%s prompt_tokens=%s completion_tokens=%s cost_usd=%s",
        cfg.provider_model,
        response.usage.prompt_tokens,
        response.usage.completion_tokens,
        cost,
    )
    return content, cost


async def transcribe(audio_path: Path, cfg: TranscriptionConfig) -> tuple[dict[str, Any], float]:
    """Transcribe audio via litellm.atranscription. Returns (verbose JSON, cost_usd)."""
    logger.info("Transcribing %s with model=%s", audio_path.name, cfg.provider_model)
    with audio_path.open("rb") as f:
        try:
            kwargs: dict[str, Any] = {
                "model": cfg.provider_model,
                "file": f,
                "language": cfg.language,
                "response_format": "verbose_json",
                "timestamp_granularities": ["word"],
            }
            if cfg.api_base:
                kwargs["api_base"] = cfg.api_base
            result = await litellm.atranscription(**kwargs)
        except litellm.APIError as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

    cost: float = result._hidden_params.get("response_cost") or 0.0  # type: ignore[union-attr]
    logger.info(
        "Transcription complete segments=%d",
        len(result.get("words") or result.get("segments") or []),
    )
    return result, cost  # type: ignore[return-value]
