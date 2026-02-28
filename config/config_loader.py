import logging
from enum import StrEnum
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, computed_field, field_validator

from pipeline.exceptions import ConfigError

logger = logging.getLogger(__name__)

_DEFAULT_AD_DETECTION_BEHAVIOR: str = (
    "Identify advertisements in this podcast transcript segment.\n"
    "An ad is any span where the host or another person or persons"
    " promote a product, service, or sponsor.\n"
    "Exclude brand mentions that are naturally part of the episode content."
)

_AD_DETECTION_JSON_SUFFIX: str = (
    "Return only a JSON array — no markdown, no preamble.\n"
    'Schema: [{"start_sec": float, "end_sec": float, "confidence": float,\n'
    '          "reason": str, "sponsor": str | null}]\n'
    "Return [] if no ads are found."
)

_DEFAULT_TOPIC_EXTRACTION_BEHAVIOR: str = (
    "Analyze the opening of this podcast transcript."
)

_TOPIC_EXTRACTION_JSON_SUFFIX: str = (
    "Return only a JSON object — no markdown, no preamble.\n"
    'Schema: {"domain": str, "topic": str, "hosts": list[str], "notes": str}'
)


class AudioFormat(StrEnum):
    """Supported audio output formats."""

    MP3 = "mp3"
    M4A = "m4a"


SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"groq", "openai", "openrouter"})


class FeedConfig(BaseModel, frozen=True):
    """Configuration for a single RSS feed."""

    name: str
    url: str
    enabled: bool = True


class PathsConfig(BaseModel, frozen=True):
    """File-system paths for outputs and the database."""

    output_dir: Path
    database: Path


class LLMProviderConfig(BaseModel, frozen=True):
    """Base configuration for an LLM provider and model pair."""

    provider: str
    model: str
    api_base: str | None = None

    @field_validator("provider")
    @classmethod
    def check_provider(cls, v: str) -> str:
        """Reject unknown provider names at config load time."""
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported provider '{v}'. Allowed: {sorted(SUPPORTED_PROVIDERS)}"
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def provider_model(self) -> str:
        """Return the litellm-prefixed model string, e.g. ``groq/llama3``."""
        return f"{self.provider}/{self.model}"


class TranscriptionConfig(LLMProviderConfig, frozen=True):
    """Configuration for the Whisper-compatible transcription model."""

    language: str | None = "en"


class InterpretationConfig(LLMProviderConfig, frozen=True):
    """Configuration for the chat completion model used for interpretation tasks."""

    temperature: float = 0
    max_tokens: int = 2048
    topic_excerpt_words: int = 2000


class AdDetectionConfig(BaseModel, frozen=True):
    """Tuning parameters for the ad detection stage."""

    chunk_duration_sec: int = 300
    chunk_overlap_sec: int = 30
    min_confidence: float = 0.75
    merge_gap_sec: int = 5


class AudioConfig(BaseModel, frozen=True):
    """Audio export settings."""

    output_format: AudioFormat = AudioFormat.MP3
    cbr_bitrate: str = "192k"


class LoggingConfig(BaseModel, frozen=True):
    """Logging level and optional log file path."""

    level: str = "INFO"
    log_file: str | None = None

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        """Normalise and validate the log level string."""
        valid = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
        return v.upper()


class RetryConfig(BaseModel, frozen=True):
    """Retry parameters for transient LLM failures."""

    max_attempts: int = 3
    backoff_factor: int = 2


class PromptsConfig(BaseModel, frozen=True):
    """Configurable system prompts for LLM stages."""

    ad_detection: str = Field(default=_DEFAULT_AD_DETECTION_BEHAVIOR, validate_default=True)
    topic_extraction: str = Field(default=_DEFAULT_TOPIC_EXTRACTION_BEHAVIOR, validate_default=True)

    @field_validator("ad_detection", mode="after")
    @classmethod
    def _append_ad_suffix(cls, v: str) -> str:
        return v.rstrip("\n") + "\n" + _AD_DETECTION_JSON_SUFFIX

    @field_validator("topic_extraction", mode="after")
    @classmethod
    def _append_topic_suffix(cls, v: str) -> str:
        return v.rstrip("\n") + "\n" + _TOPIC_EXTRACTION_JSON_SUFFIX


class AppConfig(BaseModel, frozen=True):
    """Root configuration object parsed from ``config.yaml``."""

    feeds: list[FeedConfig]
    paths: PathsConfig
    transcription: TranscriptionConfig
    interpretation: InterpretationConfig
    ad_detection: AdDetectionConfig
    audio: AudioConfig
    logging: LoggingConfig
    retry: RetryConfig
    episodes_to_keep: int = 5
    prompts: PromptsConfig = PromptsConfig()


def load_config(config_path: Path) -> AppConfig:
    """Load and validate config from YAML. Loads .env for API keys."""
    load_dotenv()

    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config must be a YAML mapping, got {type(raw).__name__}")

    _check_no_secrets(raw)

    try:
        cfg = AppConfig(**raw)
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"Config validation failed: {exc}") from exc

    logger.info(f"Config loaded from {config_path}")
    return cfg


def _check_no_secrets(raw: dict[str, object]) -> None:
    """Raise if any value looks like an API key."""
    secret_prefixes = ("sk-ant-", "sk-", "gsk_", "sk-or-")
    for section in raw.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, str) and any(
                value.startswith(prefix) for prefix in secret_prefixes
            ):
                raise ConfigError(
                    f"API key detected in config.yaml field '{key}'. "
                    "Move secrets to .env, not config.yaml."
                )
