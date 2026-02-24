import logging
from enum import StrEnum
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, computed_field, field_validator

from pipeline.exceptions import ConfigError

logger = logging.getLogger(__name__)


class AudioFormat(StrEnum):
    MP3 = "mp3"
    M4A = "m4a"


SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"groq", "openai", "openrouter"})


class FeedConfig(BaseModel, frozen=True):
    name: str
    url: str
    enabled: bool = True


class PathsConfig(BaseModel, frozen=True):
    output_dir: Path
    database: Path


class TranscriptionConfig(BaseModel, frozen=True):
    provider: str
    model: str
    language: str | None = "en"
    api_base: str | None = None

    @field_validator("provider")
    @classmethod
    def check_provider(cls, v: str) -> str:
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported provider '{v}'. Allowed: {sorted(SUPPORTED_PROVIDERS)}"
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def provider_model(self) -> str:
        return f"{self.provider}/{self.model}"


class InterpretationConfig(BaseModel, frozen=True):
    provider: str
    model: str
    api_base: str | None = None
    temperature: float = 0
    max_tokens: int = 2048
    topic_excerpt_words: int = 2000

    @field_validator("provider")
    @classmethod
    def check_provider(cls, v: str) -> str:
        if v not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported provider '{v}'. Allowed: {sorted(SUPPORTED_PROVIDERS)}"
            )
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def provider_model(self) -> str:
        return f"{self.provider}/{self.model}"


class AdDetectionConfig(BaseModel, frozen=True):
    chunk_duration_sec: int = 300
    chunk_overlap_sec: int = 30
    min_confidence: float = 0.75
    merge_gap_sec: int = 5


class AudioConfig(BaseModel, frozen=True):
    output_format: AudioFormat = AudioFormat.MP3
    cbr_bitrate: str = "192k"


class LoggingConfig(BaseModel, frozen=True):
    level: str = "INFO"
    log_file: str | None = None

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR"}
        if v.upper() not in valid:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid}")
        return v.upper()


class RetryConfig(BaseModel, frozen=True):
    max_attempts: int = 3
    backoff_factor: int = 2


class AppConfig(BaseModel, frozen=True):
    feeds: list[FeedConfig]
    paths: PathsConfig
    transcription: TranscriptionConfig
    interpretation: InterpretationConfig
    ad_detection: AdDetectionConfig
    audio: AudioConfig
    logging: LoggingConfig
    retry: RetryConfig
    episodes_to_keep: int = 5


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
    except Exception as exc:
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
