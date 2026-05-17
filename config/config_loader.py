"""Configuration loader for podcast-ad-cutter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils.exceptions import ConfigError

# Maps provider name to the Credentials field that holds its API key
PROVIDER_KEY_MAP: dict[str, str] = {
    "groq": "groq_api_key",
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
}


class FeedConfig(BaseModel):
    """Configuration for a single podcast feed."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    enabled: bool = True
    episodes_to_keep: int = Field(default=10, ge=1)


class LLMConfig(BaseModel):
    """Configuration for a single LLM provider and model."""

    provider: Literal["groq", "openai", "openrouter"]
    model: str
    context_window: int | None = Field(default=None, gt=0)


class ModelsConfig(BaseModel):
    """LLM model assignments for each pipeline stage."""

    transcription: LLMConfig
    context_extraction: LLMConfig
    ad_detection: LLMConfig


class PathsConfig(BaseModel):
    """Directory paths for outputs and caches."""

    output_dir: Path
    cache_dir: Path
    data_dir: Path
    log_dir: Path


class AdDetectionConfig(BaseModel):
    """Settings for the ad detection algorithm."""

    min_duration: int = Field(gt=0, description="Minimum ad duration in milliseconds")
    min_confidence: float = Field(ge=0.0, le=1.0)


class OutputConfig(BaseModel):
    """Settings for the processed audio output files."""

    file_type: Literal["mp3", "m4a", "ogg", "opus", "flac"]
    bitrate: str

    @field_validator("bitrate")
    @classmethod
    def _validate_bitrate(cls, v: str) -> str:
        if not re.fullmatch(r"\d+k", v):
            msg = f"bitrate must be in '<number>k' format (e.g. '128k'), got {v!r}"
            raise ValueError(msg)
        return v


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    to_file: bool
    file_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "DEBUG"
    rotate: bool = False
    keep_last: int = 10
    per_episode: bool = False


class AppConfig(BaseModel):
    """Top-level application configuration loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    feeds: list[FeedConfig] = Field(min_length=1)
    models: ModelsConfig
    paths: PathsConfig
    ad_detection: AdDetectionConfig
    output: OutputConfig
    log: LoggingConfig
    base_url: str


class Credentials(BaseSettings):
    """API credentials loaded from environment variables."""

    # ClassVar tells ruff (RUF012) this is not a pydantic model field;
    # pydantic-settings still reads it correctly as the settings config dict.
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="", case_sensitive=False
    )

    groq_api_key: str | None = None        # reads GROQ_API_KEY
    openai_api_key: str | None = None      # reads OPENAI_API_KEY
    openrouter_api_key: str | None = None  # reads OPENROUTER_API_KEY


class Config(BaseModel):
    """Combined application config and credentials container.

    Env vars are read once during Credentials() construction and frozen here.
    This class itself has no env-reading behaviour.
    """

    app: AppConfig
    credentials: Credentials


def load_config(config_path: Path) -> Config:
    """Load and validate configuration from a YAML file.

    Loads environment variables from .env, parses and validates the YAML
    config, then verifies that API keys exist for all configured providers.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validated Config instance with app config and credentials.

    Raises:
        ConfigError: If the file is missing, YAML is malformed, schema
            validation fails, or required API keys are absent or empty.

    """
    load_dotenv()

    try:
        with config_path.open() as f:
            raw: Any = yaml.safe_load(f)
    except FileNotFoundError as exc:
        msg = f"Config file not found: {config_path}"
        raise ConfigError(msg) from exc
    except yaml.YAMLError as exc:
        msg = f"Failed to parse config file: {exc}"
        raise ConfigError(msg) from exc

    try:
        app_config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f"Config validation failed:\n{exc}"
        raise ConfigError(msg) from exc

    # Collect unique providers used across all model pipeline stages
    models_cfg = app_config.models
    required_providers: set[str] = {
        models_cfg.transcription.provider,
        models_cfg.context_extraction.provider,
        models_cfg.ad_detection.provider,
    }

    credentials = Credentials()

    # Check that each required provider has a non-empty API key
    missing_keys = [
        PROVIDER_KEY_MAP[provider]
        for provider in sorted(required_providers)
        if not getattr(credentials, PROVIDER_KEY_MAP[provider])
    ]

    if missing_keys:
        keys_str = ", ".join(missing_keys)
        msg = f"Missing required API keys for configured providers: {keys_str}"
        raise ConfigError(msg)

    return Config(app=app_config, credentials=credentials)
