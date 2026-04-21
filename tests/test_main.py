"""Tests for CLI entry point: argument parsing and logging configuration."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main import _rotate_logs, configure_logging, main, parse_args
from utils.exceptions import ConfigError

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
        # Expected: YYYY-MM-DDTHH-MM-SS.log (local timezone, no shell-special characters)
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.log$", filename), f"Unexpected filename: {filename}"

    def test_debug_level_with_log_to_file(self, tmp_path: Path) -> None:
        configure_logging(level="DEBUG", log_to_file=True, log_dir=tmp_path)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1

    def test_file_level_applied_to_file_handler(self, tmp_path: Path) -> None:
        configure_logging(level="WARNING", log_to_file=True, log_dir=tmp_path, file_level="DEBUG")
        file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers[0].level == logging.DEBUG

    def test_stream_handler_level_matches_level_arg(self, tmp_path: Path) -> None:
        configure_logging(level="WARNING", log_to_file=True, log_dir=tmp_path, file_level="DEBUG")
        stream_only = [
            h for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert stream_only[0].level == logging.WARNING

    def test_root_level_is_min_of_level_and_file_level(self, tmp_path: Path) -> None:
        configure_logging(level="ERROR", log_to_file=True, log_dir=tmp_path, file_level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_root_level_when_file_level_higher_than_level(self, tmp_path: Path) -> None:
        configure_logging(level="DEBUG", log_to_file=True, log_dir=tmp_path, file_level="ERROR")
        assert logging.getLogger().level == logging.DEBUG

    def test_file_level_ignored_when_log_to_file_false(self, tmp_path: Path) -> None:
        configure_logging(level="WARNING", log_to_file=False, log_dir=tmp_path, file_level="DEBUG")
        assert logging.getLogger().level == logging.WARNING

    def test_file_level_default_is_debug(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_to_file=True, log_dir=tmp_path)
        file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers[0].level == logging.DEBUG

    def test_rotation_called_when_rotate_true(self, tmp_path: Path) -> None:
        with patch("main._rotate_logs") as mock_rotate:
            configure_logging(level="INFO", log_to_file=True, log_dir=tmp_path, rotate=True, keep_last=5)
        mock_rotate.assert_called_once_with(tmp_path, 5)

    def test_rotation_not_called_when_rotate_false(self, tmp_path: Path) -> None:
        with patch("main._rotate_logs") as mock_rotate:
            configure_logging(level="INFO", log_to_file=True, log_dir=tmp_path, rotate=False)
        mock_rotate.assert_not_called()

    def test_rotation_not_called_when_log_to_file_false(self, tmp_path: Path) -> None:
        with patch("main._rotate_logs") as mock_rotate:
            configure_logging(level="INFO", log_to_file=False, log_dir=tmp_path, rotate=True)
        mock_rotate.assert_not_called()

    def test_rotation_default_keep_last_is_10(self, tmp_path: Path) -> None:
        with patch("main._rotate_logs") as mock_rotate:
            configure_logging(level="INFO", log_to_file=True, log_dir=tmp_path, rotate=True)
        mock_rotate.assert_called_once_with(tmp_path, 10)

    def test_configure_logging_silences_litellm_loggers(self, tmp_path: Path) -> None:
        configure_logging(level="DEBUG", log_to_file=False, log_dir=tmp_path)
        assert logging.getLogger("LiteLLM").level == logging.WARNING
        assert logging.getLogger("LiteLLM Router").level == logging.WARNING


# ---------------------------------------------------------------------------
# _rotate_logs tests
# ---------------------------------------------------------------------------


class TestRotateLogs:
    def test_no_files_deleted_when_count_lte_keep_last(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"2026-01-0{i + 1}T10:00:00.log").write_text("")
        _rotate_logs(tmp_path, keep_last=5)
        assert len(list(tmp_path.glob("*.log"))) == 3

    def test_deletes_oldest_by_mtime(self, tmp_path: Path) -> None:
        import time
        files = []
        for i in range(5):
            f = tmp_path / f"f{i}.log"
            f.write_text("")
            time.sleep(0.01)
            files.append(f)
        _rotate_logs(tmp_path, keep_last=3)
        assert set(tmp_path.glob("*.log")) == {files[2], files[3], files[4]}

    def test_non_log_files_untouched(self, tmp_path: Path) -> None:
        (tmp_path / "2026-01-01T10:00:00.log").write_text("")
        (tmp_path / "2026-01-02T10:00:00.log").write_text("")
        (tmp_path / "notes.txt").write_text("")
        _rotate_logs(tmp_path, keep_last=1)
        assert (tmp_path / "notes.txt").exists()

    def test_exactly_keep_last_deletes_nothing(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"f{i}.log").write_text("")
        _rotate_logs(tmp_path, keep_last=3)
        assert len(list(tmp_path.glob("*.log"))) == 3

    def test_keep_last_zero_deletes_all(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"f{i}.log").write_text("")
        _rotate_logs(tmp_path, keep_last=0)
        assert len(list(tmp_path.glob("*.log"))) == 0


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


# ---------------------------------------------------------------------------
# main() — async entry point
# ---------------------------------------------------------------------------


class TestMain:
    async def test_config_error_writes_to_stderr_and_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["main.py"])
        with (
            patch("main.load_config", side_effect=ConfigError("bad config")),
            patch("sys.stderr") as mock_stderr,
        ):
            with pytest.raises(SystemExit) as exc_info:
                await main()
        assert exc_info.value.code == 1
        mock_stderr.write.assert_called_once_with("Failed to load config: bad config\n")

    async def test_pipeline_value_error_writes_to_stderr_and_exits(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["main.py"])
        mock_cfg = MagicMock()
        mock_cfg.app.log.level = "INFO"
        mock_cfg.app.log.to_file = False
        mock_cfg.app.paths.log_dir = tmp_path
        with (
            patch("main.load_config", return_value=mock_cfg),
            patch("main.configure_logging"),
            patch("main.Pipeline") as mock_pipeline_cls,
            patch("sys.stderr") as mock_stderr,
        ):
            mock_pipeline_cls.return_value.run = AsyncMock(
                side_effect=ValueError("nonexistent feed")
            )
            with pytest.raises(SystemExit) as exc_info:
                await main()
        assert exc_info.value.code == 1
        mock_stderr.write.assert_called_once_with("Error: nonexistent feed\n")

    async def test_happy_path_runs_pipeline(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["main.py"])
        mock_cfg = MagicMock()
        mock_cfg.app.log.level = "INFO"
        mock_cfg.app.log.to_file = False
        mock_cfg.app.paths.log_dir = tmp_path
        with (
            patch("main.load_config", return_value=mock_cfg),
            patch("main.configure_logging"),
            patch("main.Pipeline") as mock_pipeline_cls,
        ):
            mock_pipeline_cls.return_value.run = AsyncMock(return_value=[])
            await main()
        mock_pipeline_cls.return_value.run.assert_awaited_once()

    async def test_configure_logging_receives_rotation_fields(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["main.py"])
        mock_cfg = MagicMock()
        mock_cfg.app.log.level = "INFO"
        mock_cfg.app.log.to_file = True
        mock_cfg.app.log.rotate = True
        mock_cfg.app.log.keep_last = 7
        mock_cfg.app.log.file_level = "DEBUG"
        mock_cfg.app.paths.log_dir = tmp_path
        with (
            patch("main.load_config", return_value=mock_cfg),
            patch("main.configure_logging") as mock_logging,
            patch("main.Pipeline") as mock_pipeline_cls,
        ):
            mock_pipeline_cls.return_value.run = AsyncMock(return_value=[])
            await main()
        mock_logging.assert_called_once_with(
            level="INFO",
            log_to_file=True,
            log_dir=tmp_path,
            file_level="DEBUG",
            rotate=True,
            keep_last=7,
        )
