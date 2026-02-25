import logging
import os
from pathlib import Path
from typing import Any

import litellm
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

from config.config_loader import AppConfig, InterpretationConfig, TranscriptionConfig
from pipeline.exceptions import ConfigError, LLMError, TranscriptionError

logger = logging.getLogger(__name__)
litellm.suppress_debug_info = True
litellm.drop_params = True  # drop unsupported params (e.g. timestamp_granularities on Groq)

_PROVIDER_ENV_VAR: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


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
    except litellm.APIError as exc:  # type: ignore[attr-defined]  # litellm stubs omit APIError
        raise LLMError(f"LLM call failed: {exc}") from exc

    content: str = response.choices[0].message.content or ""
    cost: float = response._hidden_params.get("response_cost") or 0.0
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
            logger.debug(
                f"Transcription request model={cfg.provider_model} language={cfg.language}"
            )
            result = await litellm.atranscription(**kwargs)
        except litellm.APIError as exc:  # type: ignore[attr-defined]  # litellm stubs omit APIError
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

    cost: float = result._hidden_params.get("response_cost") or 0.0
    if not cost:
        duration: float = result.get("duration", 0.0)
        model_info: dict[str, Any] = litellm.model_cost.get(cfg.provider_model, {})
        rate: float = float(model_info.get("input_cost_per_second", 0.0))
        cost = duration * rate
    n_segments = len(result.get("words") or result.get("segments") or [])
    logger.info(f"Transcription complete segments={n_segments}")

    return result, cost


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


def validate_api_keys(cfg: AppConfig) -> None:
    """Probe each configured provider's API key. Raises ConfigError on missing or invalid keys.

    Deduplicates by provider — a provider used for both transcription and interpretation
    is probed only once.
    """
    seen: set[str] = set()
    for sub_cfg in (cfg.transcription, cfg.interpretation):
        if sub_cfg.provider in seen:
            continue
        seen.add(sub_cfg.provider)

        env_var = _PROVIDER_ENV_VAR.get(sub_cfg.provider, f"{sub_cfg.provider.upper()}_API_KEY")
        key = os.environ.get(env_var, "")
        if not key:
            raise ConfigError(
                f"Missing {env_var} environment variable (required for {sub_cfg.provider_model})"
            )
        if not litellm.check_valid_key(model=sub_cfg.provider_model, api_key=key):
            raise ConfigError(
                f"Invalid API key for {sub_cfg.provider_model} (check {env_var})"
            )

    logger.info("API key validation passed")
