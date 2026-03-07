"""Tests for config_editor mutations."""

from pathlib import Path

import pytest
import yaml

from frontend import config_editor


@pytest.fixture
def minimal_config(tmp_path: Path) -> Path:
    """Write a minimal config.yaml to a temp path and point config_editor at it."""
    cfg = {
        "feeds": [{"name": "Test", "url": "https://example.com/feed.rss", "enabled": True}],
        "paths": {"output_dir": "./output", "database": ":memory:"},
        "transcription": {"provider": "openai", "model": "whisper-1", "language": "en"},
        "interpretation": {
            "provider": "openai",
            "model": "gpt-4o",
            "temperature": 0,
            "max_tokens": 2048,
            "topic_excerpt_words": 2000,
        },
        "ad_detection": {
            "chunk_duration_sec": 300,
            "chunk_overlap_sec": 30,
            "min_confidence": 0.75,
            "merge_gap_sec": 5,
        },
        "audio": {"output_format": "mp3", "cbr_bitrate": "192k"},
        "logging": {"level": "INFO", "log_file": None},
        "retry": {"max_attempts": 3, "backoff_factor": 2},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg))
    config_editor.set_config_path(p)
    return p


def _read(p: Path) -> dict[str, object]:
    import yaml as _yaml

    raw = _yaml.safe_load(p.read_text())
    assert isinstance(raw, dict)
    return raw  # type: ignore[return-value]


def test_update_settings_saves_base_url(minimal_config: Path) -> None:
    config_editor.update_settings(
        transcription_provider="openai",
        transcription_model="whisper-1",
        interpretation_provider="openai",
        interpretation_model="gpt-4o",
        min_confidence=0.75,
        verbose_log=False,
        base_url="https://example.com",
        max_episodes_per_feed=None,
        ad_detection_prompt="Detect ads.",
        topic_extraction_prompt="Extract topic.",
    )
    data = _read(minimal_config)
    pub = data["publishing"]
    assert isinstance(pub, dict)
    assert pub["base_url"] == "https://example.com"


def test_update_settings_saves_max_episodes(minimal_config: Path) -> None:
    config_editor.update_settings(
        transcription_provider="openai",
        transcription_model="whisper-1",
        interpretation_provider="openai",
        interpretation_model="gpt-4o",
        min_confidence=0.75,
        verbose_log=False,
        base_url=None,
        max_episodes_per_feed=10,
        ad_detection_prompt="Detect ads.",
        topic_extraction_prompt="Extract topic.",
    )
    data = _read(minimal_config)
    pub = data["publishing"]
    assert isinstance(pub, dict)
    assert pub["max_episodes_per_feed"] == 10


def test_update_settings_saves_prompts(minimal_config: Path) -> None:
    config_editor.update_settings(
        transcription_provider="openai",
        transcription_model="whisper-1",
        interpretation_provider="openai",
        interpretation_model="gpt-4o",
        min_confidence=0.75,
        verbose_log=False,
        base_url=None,
        max_episodes_per_feed=None,
        ad_detection_prompt="Custom ad prompt.",
        topic_extraction_prompt="Custom topic prompt.",
    )
    data = _read(minimal_config)
    prompts = data["prompts"]
    assert isinstance(prompts, dict)
    assert prompts["ad_detection"] == "Custom ad prompt."
    assert prompts["topic_extraction"] == "Custom topic prompt."


def test_get_raw_prompts_returns_defaults_when_not_set(minimal_config: Path) -> None:  # noqa: ARG001
    from config.config_loader import DEFAULT_AD_DETECTION_PROMPT, DEFAULT_TOPIC_EXTRACTION_PROMPT

    raw = config_editor.get_raw_prompts()
    assert raw["ad_detection"] == DEFAULT_AD_DETECTION_PROMPT
    assert raw["topic_extraction"] == DEFAULT_TOPIC_EXTRACTION_PROMPT


def test_get_raw_prompts_returns_stored_values(minimal_config: Path) -> None:  # noqa: ARG001
    config_editor.update_settings(
        transcription_provider="openai",
        transcription_model="whisper-1",
        interpretation_provider="openai",
        interpretation_model="gpt-4o",
        min_confidence=0.75,
        verbose_log=False,
        base_url=None,
        max_episodes_per_feed=None,
        ad_detection_prompt="My custom ad prompt.",
        topic_extraction_prompt="My custom topic prompt.",
    )
    raw = config_editor.get_raw_prompts()
    assert raw["ad_detection"] == "My custom ad prompt."
    assert raw["topic_extraction"] == "My custom topic prompt."
