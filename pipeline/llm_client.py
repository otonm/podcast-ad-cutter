import logging
from pathlib import Path
from typing import Any

import litellm
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.config_loader import AppConfig, InterpretationConfig, TranscriptionConfig
from pipeline.exceptions import ConfigError, LLMError, TranscriptionError

logger = logging.getLogger(__name__)
litellm.suppress_debug_info = True
litellm.drop_params = True  # drop unsupported params (e.g. timestamp_granularities on Groq)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    retry=retry_if_exception_type(LLMError),
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

    if response_format:
        kwargs["response_format"] = response_format

    logger.debug(f"LLM request model={cfg.provider_model} messages={len(messages)}")

    try:
        response = await litellm.acompletion(**kwargs)
    except litellm.AuthenticationError as exc:  # type: ignore[attr-defined]
        raise ConfigError(f"Invalid API key for {cfg.provider_model}: {exc}") from exc
    except litellm.BadRequestError as exc:  # type: ignore[attr-defined]
        if "json_validate_failed" not in str(exc) or not response_format:
            raise LLMError(f"LLM call failed: {exc}") from exc
        logger.warning(
            f"Provider rejected JSON mode for {cfg.provider_model}; retrying without response_format"
        )
        kwargs.pop("response_format", None)
        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.APIError as retry_exc:  # type: ignore[attr-defined]
            raise LLMError(f"LLM call failed: {retry_exc}") from retry_exc
    except litellm.APIError as exc:  # type: ignore[attr-defined]  # litellm stubs omit APIError
        raise LLMError(f"LLM call failed: {exc}") from exc

    content: str = response.choices[0].message.content or ""
    cost: float = response._hidden_params.get("response_cost") or 0.0  # noqa: SLF001
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
            logger.debug(
                f"Transcription request model={cfg.provider_model} language={cfg.language}"
            )
            result = await litellm.atranscription(**kwargs)
        except litellm.AuthenticationError as exc:  # type: ignore[attr-defined]
            raise ConfigError(f"Invalid API key for {cfg.provider_model}: {exc}") from exc
        except litellm.APIError as exc:  # type: ignore[attr-defined]  # litellm stubs omit APIError
            raise TranscriptionError(f"Transcription failed: {exc}") from exc

    cost: float = result._hidden_params.get("response_cost") or 0.0  # noqa: SLF001
    if not cost:
        duration: float = result.get("duration", 0.0)
        model_info: dict[str, Any] = litellm.model_cost.get(cfg.provider_model, {})
        rate: float = float(model_info.get("input_cost_per_second", 0.0))
        cost = duration * rate
        logger.debug(
            f"Transcription cost fallback: duration={duration:.1f}s"
            f" rate={rate} cost_usd={cost:.6f}"
        )
    n_segments = len(result.get("words") or result.get("segments") or [])
    logger.info(f"Transcription complete segments={n_segments} cost_usd={cost:.6f}")

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
    """Check that required API key env vars are present for each configured model.

    Uses litellm.validate_environment — a local check with no network calls.
    Deduplicates by provider so a shared provider is checked only once.
    """
    seen: set[str] = set()
    for sub_cfg in (cfg.transcription, cfg.interpretation):
        if sub_cfg.provider in seen:
            continue
        seen.add(sub_cfg.provider)

        result: dict[str, Any] = litellm.validate_environment(model=sub_cfg.provider_model)
        if not result["keys_in_environment"]:
            missing = ", ".join(result["missing_keys"])
            raise ConfigError(
                f"Missing environment variable(s) for {sub_cfg.provider_model}: {missing}"
            )

    logger.info("API key validation passed")


def append_json_correction(
    messages: list[dict[str, str]],
    bad_response: str,
    *,
    schema_hint: str,
) -> list[dict[str, str]]:
    """Append a correction request to the message list for JSON parse failures."""
    return [
        *messages,
        {"role": "assistant", "content": bad_response},
        {
            "role": "user",
            "content": (
                f"Your response was not valid JSON. Please respond with only a valid"
                f" JSON {schema_hint}, no markdown, no code blocks, no preamble."
            ),
        },
    ]
