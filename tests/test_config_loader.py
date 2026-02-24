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
