from pathlib import Path

import pytest


def test_load_valid_config():
    from config.config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    assert cfg.interpretation.provider == "openai"
    assert cfg.interpretation.model == "gpt-4o"
    assert cfg.interpretation.provider_model == "openai/gpt-4o"
    assert cfg.interpretation.temperature == 0
    assert cfg.feeds[0].name == "Test Podcast"
    assert cfg.ad_detection.min_confidence == 0.75
    assert cfg.audio.output_format == "mp3"


def test_config_paths_are_path_objects():
    from config.config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    assert isinstance(cfg.paths.output_dir, Path)
    assert isinstance(cfg.paths.database, Path)


def test_missing_config_raises():
    from config.config_loader import load_config
    from pipeline.exceptions import ConfigError

    with pytest.raises(ConfigError):
        load_config(Path("nonexistent.yaml"))


def test_invalid_config_raises(tmp_path):
    from config.config_loader import load_config
    from pipeline.exceptions import ConfigError

    bad = tmp_path / "bad.yaml"
    bad.write_text("feeds: not_a_list\n")
    with pytest.raises((ConfigError, Exception)):
        load_config(bad)


def test_prompts_defaults_are_non_empty_strings():
    from config.config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    assert isinstance(cfg.prompts.ad_detection, str)
    assert len(cfg.prompts.ad_detection) > 10
    assert isinstance(cfg.prompts.topic_extraction, str)
    assert len(cfg.prompts.topic_extraction) > 10


def test_prompts_defaults_include_json_suffix():
    from config.config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    # Behavior text present
    assert "advertisement" in cfg.prompts.ad_detection.lower()
    # JSON suffix present
    assert "JSON array" in cfg.prompts.ad_detection
    assert "start_sec" in cfg.prompts.ad_detection
    # Behavior text present
    assert "transcript" in cfg.prompts.topic_extraction.lower()
    # JSON suffix present
    assert "JSON object" in cfg.prompts.topic_extraction
    assert "domain" in cfg.prompts.topic_extraction


def test_prompts_can_be_overridden(tmp_path):
    import shutil

    from config.config_loader import load_config

    dst = tmp_path / "config.yaml"
    shutil.copy(Path("tests/fixtures/test_config.yaml"), dst)
    with dst.open("a") as f:
        f.write(
            "\nprompts:\n"
            "  ad_detection: 'Custom ad prompt'\n"
            "  topic_extraction: 'Custom topic prompt'\n"
        )
    cfg = load_config(dst)
    assert "Custom ad prompt" in cfg.prompts.ad_detection
    assert "JSON array" in cfg.prompts.ad_detection        # suffix was appended
    assert "Custom topic prompt" in cfg.prompts.topic_extraction
    assert "JSON object" in cfg.prompts.topic_extraction   # suffix was appended
