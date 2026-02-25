import logging
from pathlib import Path
from typing import Any

import litellm
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from config.config_loader import InterpretationConfig, TranscriptionConfig
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

    logger.debug(f"LLM request model={cfg.provider_model} messages={len(messages)}")

    try:
        response = await litellm.acompletion(**kwargs)
    except litellm.APIError as exc:
        raise LLMError(f"LLM call failed: {exc}") from exc

    content: str = response.choices[0].message.content or ""
    cost: float = response._hidden_params.get("response_cost") or 0.0  # type: ignore[union-attr]
    logger.debug(
        f"LLM response model={cfg.provider_model}"
        f" prompt_tokens={response.usage.prompt_tokens}"
        f" completion_tokens={response.usage.completion_tokens}"
        f" cost_usd={cost}"
    )

    return content, cost


async def transcribe(audio_path: Path, cfg: TranscriptionConfig) -> tuple[dict[str, Any], float]:
    """Transcribe audio via litellm.atranscription. Returns (verbose JSON, cost_usd)."""

    logger.info(f"Transcribing {audio_path.name} with model={cfg.provider_model}")

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
            logger.debug(f"kwargs: {kwargs}")
            result = await litellm.atranscription(**kwargs)
        except litellm.APIError as exc:
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

    cost: float = result._hidden_params.get("response_cost") or 0.0  # type: ignore[union-attr]
    if not cost:
        duration: float = result.get("duration", 0.0)
        model_info: dict[str, object] = litellm.model_cost.get(cfg.provider_model, {})
        rate: float = float(model_info.get("input_cost_per_second", 0.0))
        cost = duration * rate
    n_segments = len(result.get("words") or result.get("segments") or [])
    logger.info(f"Transcription complete segments={n_segments}")

    return result, cost  # type: ignore[return-value]


def fits_in_context(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_output_tokens: int,
) -> bool:
    """Return True if messages fit within 85% of the model's context window.

    Returns True when the context window is unknown (e.g. local or custom
    models not in litellm's registry), so those models always use the
    single-call path.
    """
    max_ctx: int | None = litellm.get_max_tokens(model)
    logger.debug(f"LLM {model} context window size: {max_ctx}")

    if max_ctx is None:
        logger.info(f"Context window unknown for {model}, sending full transcript")
        return True

    safe_budget = int(max_ctx * 0.85)
    token_count: int = litellm.token_counter(model=model, messages=messages)
    
    return token_count + max_output_tokens <= safe_budget
