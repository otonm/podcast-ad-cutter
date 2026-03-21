"""Tests for custom exception hierarchy."""

from __future__ import annotations

from utils.exceptions import ConfigError, PodcastAdCutterError


def test_config_error_is_podcast_error() -> None:
    assert issubclass(ConfigError, PodcastAdCutterError)
