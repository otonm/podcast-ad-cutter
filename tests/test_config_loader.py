"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from config.config_loader import Config, load_config
from utils.exceptions import ConfigError

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

VALID_YAML: str = """\
feeds:
  - title: "Test Podcast"
    url: "https://example.com/feed.rss"
    enabled: true
    episodes_to_keep: 10
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
paths:
  output_dir: "./output"
  cache_dir: "./cache"
  data_dir: "./data"
  log_dir: "./logs"
ad_detection:
  min_duration: 10000
  min_confidence: 0.7
output:
  file_type: "mp3"
  bitrate: "128k"
log:
  level: "ERROR"
  to_file: false
base_url: "http://localhost:8080"
"""

# YAML with feeds: [] to test empty-list validation
EMPTY_FEEDS_YAML: str = """\
feeds: []
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
paths:
  output_dir: "./output"
  cache_dir: "./cache"
  data_dir: "./data"
  log_dir: "./logs"
ad_detection:
  min_duration: 10000
  min_confidence: 0.7
output:
  file_type: "mp3"
  bitrate: "128k"
log:
  level: "ERROR"
  to_file: false
base_url: "http://localhost:8080"
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Write VALID_YAML to a temp file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(VALID_YAML)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_valid_config(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert isinstance(cfg, Config)
    assert cfg.app.base_url == "http://localhost:8080"
    assert cfg.credentials.groq_api_key == "test-groq-key"
    assert cfg.app.feeds[0].title == "Test Podcast"
    assert isinstance(cfg.app.paths.output_dir, Path)
    assert cfg.credentials.groq_api_key == "test-groq-key"


def test_load_dotenv_called(tmp_path):
    # load_dotenv must be called before YAML reading — assert it is called
    # even when the config path does not exist (ConfigError raised after the call)
    with patch("config.config_loader.load_dotenv") as mock_dotenv:
        with pytest.raises(ConfigError):
            load_config(tmp_path / "nonexistent.yaml")
    mock_dotenv.assert_called_once()


def test_missing_config_file(tmp_path):
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")


def test_invalid_yaml(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("key: [unclosed")
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="Failed to parse"):
            load_config(bad)


def test_schema_validation_error(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(VALID_YAML.replace("min_confidence: 0.7", "min_confidence: 2.0"))
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="validation"):
            load_config(bad)


def test_empty_feeds_list(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(EMPTY_FEEDS_YAML)
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError):
            load_config(bad)


def test_missing_required_api_key(config_file, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="groq_api_key"):
            load_config(config_file)


def test_empty_string_api_key(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="groq_api_key"):
            load_config(config_file)


def test_unreferenced_keys_not_required(config_file, monkeypatch):
    # All providers are "groq" in VALID_YAML — only GROQ_API_KEY is required
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert cfg.credentials.groq_api_key == "test-key"


def test_path_coercion(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert isinstance(cfg.app.paths.output_dir, Path)


def test_logging_config_rotate_defaults_false(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert cfg.app.log.rotate is False


def test_logging_config_keep_last_defaults_10(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert cfg.app.log.keep_last == 10


def test_logging_config_file_level_defaults_debug(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert cfg.app.log.file_level == "DEBUG"
