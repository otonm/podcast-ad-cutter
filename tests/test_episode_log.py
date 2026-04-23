"""Tests for per-episode file logging helpers."""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import pytest

from utils.episode_log import close_episode_log, open_episode_log, rotate_episode_logs


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
    def test_creates_feed_subdirectory(self, tmp_path: Path) -> None:
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        feed_dir = tmp_path / "episodes" / "my-podcast"
        assert feed_dir.is_dir()

    def test_log_filename_matches_expected_pattern(self, tmp_path: Path) -> None:
        _, handler, log_path = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        files = list((tmp_path / "episodes" / "my-podcast").glob("*.log"))
        assert len(files) == 1
        pattern = r"^my-episode\.\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.log$"
        assert re.match(pattern, files[0].name), f"Unexpected filename: {files[0].name}"
        assert log_path == files[0]

    def test_handler_attached_to_root_logger(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        before_count = len(root.handlers)
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        assert len(root.handlers) == before_count + 1
        assert handler in root.handlers
        close_episode_log(handler)

    def test_handler_level_matches_file_level_arg(self, tmp_path: Path) -> None:
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="WARNING",
        )
        assert handler.level == logging.WARNING
        close_episode_log(handler)

    def test_handler_level_defaults_to_debug(self, tmp_path: Path) -> None:
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        assert handler.level == logging.DEBUG
        close_episode_log(handler)

    def test_returns_episode_logger_with_correct_name(self, tmp_path: Path) -> None:
        episode_logger, handler, _ = open_episode_log(
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
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        some_logger = logging.getLogger("components.ad_detector")
        some_logger.debug("ad detection triggered")
        close_episode_log(handler)

        log_file = next((tmp_path / "episodes" / "my-podcast").glob("*.log"))
        content = log_file.read_text()
        assert "ad detection triggered" in content

    def test_episode_logger_messages_written_to_episode_file(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        episode_logger, handler, _ = open_episode_log(
            guid="ep-42",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        episode_logger.debug("explicit episode message")
        close_episode_log(handler)

        log_file = next((tmp_path / "episodes" / "my-podcast").glob("*.log"))
        content = log_file.read_text()
        assert "explicit episode message" in content

    def test_slugifies_podcast_and_episode_titles(self, tmp_path: Path) -> None:
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Great Podcast!",
            episode_title="Episode 1: The Beginning",
            log_dir=tmp_path,
        )
        close_episode_log(handler)
        feed_dir = tmp_path / "episodes" / "my-great-podcast"
        files = list(feed_dir.glob("*.log"))
        assert len(files) == 1
        name = files[0].name
        assert name.startswith("episode-1-the-beginning.")

    def test_open_lowers_root_level_when_above_file_handler_level(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        root.setLevel(logging.WARNING)
        _, handler, _ = open_episode_log(
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
        _, handler, _ = open_episode_log(
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
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="DEBUG",
        )
        logging.getLogger("components.ad_detector").debug("reasoning text here")
        close_episode_log(handler)
        log_file = next((tmp_path / "episodes" / "my-podcast").glob("*.log"))
        assert "reasoning text here" in log_file.read_text()


class TestCloseEpisodeLog:
    def test_handler_removed_from_root_logger(self, tmp_path: Path) -> None:
        root = logging.getLogger()
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
        )
        assert handler in root.handlers
        close_episode_log(handler)
        assert handler not in root.handlers

    def test_handler_closed_after_removal(self, tmp_path: Path) -> None:
        _, handler, _ = open_episode_log(
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
        _, handler, _ = open_episode_log(
            guid="ep-1",
            podcast_title="My Podcast",
            episode_title="My Episode",
            log_dir=tmp_path,
            file_level="DEBUG",
        )
        assert root.level == logging.DEBUG
        close_episode_log(handler)
        assert root.level == logging.WARNING


class TestRotateEpisodeLogs:
    def _make_log_file(self, feed_dir: Path, episode_slug: str, timestamp: str) -> Path:
        p = feed_dir / f"{episode_slug}.{timestamp}.log"
        p.write_text("log content")
        return p

    def test_keeps_keep_last_most_recent_per_episode_slug(self, tmp_path: Path) -> None:
        feed_dir = tmp_path / "my-podcast"
        feed_dir.mkdir()
        self._make_log_file(feed_dir, "ep-one", "2026-04-20T10-00-00")
        self._make_log_file(feed_dir, "ep-one", "2026-04-21T10-00-00")
        self._make_log_file(feed_dir, "ep-one", "2026-04-22T10-00-00")

        rotate_episode_logs(feed_dir, keep_last=2)

        remaining = sorted(feed_dir.glob("ep-one.*.log"))
        assert len(remaining) == 2
        assert remaining[0].name == "ep-one.2026-04-21T10-00-00.log"
        assert remaining[1].name == "ep-one.2026-04-22T10-00-00.log"

    def test_deletes_oldest_by_mtime_not_name(self, tmp_path: Path) -> None:
        feed_dir = tmp_path / "my-podcast"
        feed_dir.mkdir()
        # Create "newer name" file first — if rotation used name order it would keep this one
        f_name_newer = self._make_log_file(feed_dir, "ep-one", "2026-04-22T10-00-00")
        # Create "older name" file second
        f_name_older = self._make_log_file(feed_dir, "ep-one", "2026-04-20T10-00-00")
        # Force mtime: f_name_newer has an older mtime, f_name_older has a newer mtime
        now = time.time()
        os.utime(f_name_newer, (now, now))
        os.utime(f_name_older, (now + 1.0, now + 1.0))

        rotate_episode_logs(feed_dir, keep_last=1)

        remaining = list(feed_dir.glob("ep-one.*.log"))
        assert len(remaining) == 1
        assert remaining[0] == f_name_older  # mtime-newer wins despite older-looking name

    def test_keep_last_zero_deletes_all(self, tmp_path: Path) -> None:
        feed_dir = tmp_path / "my-podcast"
        feed_dir.mkdir()
        self._make_log_file(feed_dir, "ep-one", "2026-04-20T10-00-00")
        self._make_log_file(feed_dir, "ep-one", "2026-04-21T10-00-00")

        rotate_episode_logs(feed_dir, keep_last=0)

        assert list(feed_dir.glob("*.log")) == []

    def test_does_not_affect_other_episode_slugs(self, tmp_path: Path) -> None:
        feed_dir = tmp_path / "my-podcast"
        feed_dir.mkdir()
        self._make_log_file(feed_dir, "ep-one", "2026-04-20T10-00-00")
        self._make_log_file(feed_dir, "ep-one", "2026-04-21T10-00-00")
        self._make_log_file(feed_dir, "ep-two", "2026-04-20T10-00-00")

        rotate_episode_logs(feed_dir, keep_last=1)

        assert len(list(feed_dir.glob("ep-one.*.log"))) == 1
        assert len(list(feed_dir.glob("ep-two.*.log"))) == 1

    def test_noop_when_feed_dir_missing(self, tmp_path: Path) -> None:
        feed_dir = tmp_path / "does-not-exist"
        rotate_episode_logs(feed_dir, keep_last=2)

    def test_noop_when_fewer_files_than_keep_last(self, tmp_path: Path) -> None:
        feed_dir = tmp_path / "my-podcast"
        feed_dir.mkdir()
        f = self._make_log_file(feed_dir, "ep-one", "2026-04-20T10-00-00")

        rotate_episode_logs(feed_dir, keep_last=5)

        assert f.exists()

    def test_raises_on_negative_keep_last(self, tmp_path: Path) -> None:
        feed_dir = tmp_path / "my-podcast"
        feed_dir.mkdir()
        with pytest.raises(ValueError, match="keep_last must be >= 0"):
            rotate_episode_logs(feed_dir, keep_last=-1)
