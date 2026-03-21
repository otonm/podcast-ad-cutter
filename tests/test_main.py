"""Tests for CLI entry point: argument parsing and logging configuration."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import pytest

from main import configure_logging, parse_args

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def restore_root_logger() -> object:
    """Save and restore root logger state around each test.

    Clears all handlers before the test so each test starts clean,
    then closes handlers added by the test and reinstates the originals.
    """
    root = logging.getLogger()
    original_level = root.level
    original_handlers = root.handlers[:]

    # Start each test with a blank slate
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    yield

    # Close and remove any handlers the test left behind
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)

    # Restore pre-test state
    root.setLevel(original_level)
    for handler in original_handlers:
        root.addHandler(handler)


# ---------------------------------------------------------------------------
# configure_logging tests
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    def test_info_level(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_to_file=False, log_dir=tmp_path)
        assert logging.getLogger().level == logging.INFO

    def test_debug_level(self, tmp_path: Path) -> None:
        configure_logging(level="DEBUG", log_to_file=False, log_dir=tmp_path)
        assert logging.getLogger().level == logging.DEBUG

    def test_warning_level(self, tmp_path: Path) -> None:
        configure_logging(level="WARNING", log_to_file=False, log_dir=tmp_path)
        assert logging.getLogger().level == logging.WARNING

    def test_stream_handler_always_present(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_to_file=False, log_dir=tmp_path)
        # FileHandler is a subclass of StreamHandler; exclude it
        stream_only = [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_only) == 1

    def test_no_file_handler_without_flag(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_to_file=False, log_dir=tmp_path)
        file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 0

    def test_log_to_file_creates_directory(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        assert not log_dir.exists()
        configure_logging(level="INFO", log_to_file=True, log_dir=log_dir)
        assert log_dir.is_dir()

    def test_log_to_file_adds_file_handler(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_to_file=True, log_dir=tmp_path)
        file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1

    def test_log_filename_matches_datetime_pattern(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_to_file=True, log_dir=tmp_path)
        file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        filename = Path(file_handlers[0].baseFilename).name
        # Expected: YYYY-MM-DDTHH:MM:SS.log (local timezone, ISO 8601)
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.log$", filename), f"Unexpected filename: {filename}"

    def test_debug_level_with_log_to_file(self, tmp_path: Path) -> None:
        configure_logging(level="DEBUG", log_to_file=True, log_dir=tmp_path)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1


# ---------------------------------------------------------------------------
# parse_args tests
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_debug_short_flag(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sys, "argv", ["main.py", "-d"])
            args = parse_args()
        assert args.debug is True

    def test_debug_long_flag(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sys, "argv", ["main.py", "--debug"])
            args = parse_args()
        assert args.debug is True

    def test_debug_defaults_to_false(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sys, "argv", ["main.py"])
            args = parse_args()
        assert args.debug is False

    def test_log_to_file_flag(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sys, "argv", ["main.py", "--log-to-file"])
            args = parse_args()
        assert args.log_to_file is True

    def test_log_to_file_defaults_to_false(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sys, "argv", ["main.py"])
            args = parse_args()
        assert args.log_to_file is False

    def test_feed_flag_sets_name(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sys, "argv", ["main.py", "--feed", "My Podcast"])
            args = parse_args()
        assert args.feed == "My Podcast"

    def test_feed_defaults_to_none(self) -> None:
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(sys, "argv", ["main.py"])
            args = parse_args()
        assert args.feed is None
