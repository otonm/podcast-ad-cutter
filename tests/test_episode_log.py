"""Tests for per-episode file logging helpers."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from utils.episode_log import close_episode_log, open_episode_log


@pytest.fixture(autouse=True)
def restore_root_logger() -> object:
    """Save and restore root logger state around each test."""
    root = logging.getLogger()
    original_level = root.level
    original_handlers = root.handlers[:]

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    yield

    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)

    root.setLevel(original_level)
    for handler in original_handlers:
        root.addHandler(handler)


class TestOpenEpisodeLog:
    def test_creates_episodes_subdirectory(self, tmp_path: Path) -> None:
        episodes_dir = tmp_path / "episodes"
        assert not episodes_dir.exists()
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        assert episodes_dir.is_dir()

    def test_log_filename_matches_expected_pattern(self, tmp_path: Path) -> None:
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        files = list((tmp_path / "episodes").glob("*.log"))
        assert len(files) == 1
        pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.my-podcast\.my-episode\.log$"
        assert re.match(pattern, files[0].name), f"Unexpected filename: {files[0].name}"

    def test_handler_attached_to_root_logger(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        before_count = len(root.handlers)
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        assert len(root.handlers) == before_count + 1
        assert handler in root.handlers
        close_episode_log(handler)

    def test_handler_level_matches_file_level_arg(self, tmp_path: Path) -> None:
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="WARNING",
        )
        assert handler.level == logging.WARNING
        close_episode_log(handler)

    def test_handler_level_defaults_to_debug(self, tmp_path: Path) -> None:
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        assert handler.level == logging.DEBUG
        close_episode_log(handler)

    def test_returns_episode_logger_with_correct_name(self, tmp_path: Path) -> None:
        episode_logger, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        assert isinstance(episode_logger, logging.Logger)
        assert episode_logger.name == "episode.ep-1"

    def test_messages_from_any_logger_written_to_episode_file(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        some_logger = logging.getLogger("components.ad_detector")
        some_logger.debug("ad detection triggered")
        close_episode_log(handler)

        log_file = next((tmp_path / "episodes").glob("*.log"))
        content = log_file.read_text()
        assert "ad detection triggered" in content

    def test_episode_logger_messages_written_to_episode_file(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        episode_logger, handler = open_episode_log(
            guid="ep-42",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        episode_logger.debug("explicit episode message")
        close_episode_log(handler)

        log_file = next((tmp_path / "episodes").glob("*.log"))
        content = log_file.read_text()
        assert "explicit episode message" in content

    def test_slugifies_podcast_and_episode_titles(self, tmp_path: Path) -> None:
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Great Podcast!",
            episode_title="Episode 1: The Beginning",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        files = list((tmp_path / "episodes").glob("*.log"))
        assert len(files) == 1
        name = files[0].name
        assert "my-great-podcast" in name
        assert "episode-1-the-beginning" in name

    def test_open_lowers_root_level_when_above_file_handler_level(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        root.setLevel(logging.WARNING)
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="DEBUG",
        )
        assert root.level == logging.DEBUG
        close_episode_log(handler)

    def test_open_does_not_change_root_level_when_already_at_or_below_file_level(
        self, tmp_path: Path
    ) -> None:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="DEBUG",
        )
        assert root.level == logging.DEBUG
        close_episode_log(handler)

    def test_debug_message_reaches_file_when_root_was_at_warning(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        root.setLevel(logging.WARNING)
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="DEBUG",
        )
        logging.getLogger("components.ad_detector").debug("reasoning text here")
        close_episode_log(handler)
        log_file = next((tmp_path / "episodes").glob("*.log"))
        assert "reasoning text here" in log_file.read_text()


class TestCloseEpisodeLog:
    def test_handler_removed_from_root_logger(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        assert handler in root.handlers
        close_episode_log(handler)
        assert handler not in root.handlers

    def test_handler_closed_after_removal(self, tmp_path: Path) -> None:
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        # FileHandler.stream is None when closed
        assert handler.stream is None

    def test_close_restores_root_level(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        root.setLevel(logging.WARNING)
        _, handler = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="DEBUG",
        )
        assert root.level == logging.DEBUG
        close_episode_log(handler)
        assert root.level == logging.WARNING
