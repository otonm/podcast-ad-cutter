"""CLI entry point for the podcast ad cutter project."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from components.pipeline import Pipeline
from config.config_loader import Config, load_config
from utils.exceptions import ConfigError

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse and return CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Download podcast episodes, detect and remove ads, export clean audio."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--feed",
        type=str,
        help="Process only this feed by name",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Override output directory",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        help="Override minimum confidence threshold (0.0-1.0)",
    )
    parser.add_argument(
        "--force-ai-detection",
        action="store_true",
        help="Force using AI-based ad detection",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Enable debug-level logging",
    )
    parser.add_argument(
        "--log-to-file",
        action="store_true",
        help="Write logs to a timestamped file inside logs/",
    )
    return parser.parse_args()


def _rotate_logs(log_dir: Path, keep_last: int) -> None:
    """Delete the oldest .log files in log_dir, keeping the keep_last most recent.

    Sorted by modification time so the decision is independent of filename format.

    Args:
        log_dir: Directory containing .log files.
        keep_last: Number of most-recent .log files to retain.

    """
    log_files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    to_delete = log_files[:-keep_last] if keep_last > 0 else log_files
    for old_file in to_delete:
        old_file.unlink()


def configure_logging(
    *,
    level: str,
    log_to_file: bool,
    log_dir: Path = Path("logs"),
    file_level: str = "DEBUG",
    rotate: bool = False,
    keep_last: int = 10,
) -> None:
    """Configure the root logger with a stream handler and an optional file handler.

    Args:
        level: Logging level name — one of DEBUG, INFO, WARNING, ERROR, CRITICAL.
        log_to_file: When True, write logs to a timestamped file in log_dir.
        log_dir: Directory for log files (created if absent). Defaults to logs/.
        file_level: Log level for the file handler (default DEBUG). Only applies
            when log_to_file is True. Allows verbose file logs with a quieter console.
        rotate: When True and log_to_file is True, delete old .log files in log_dir
            after each run, keeping only the most recent keep_last files.
        keep_last: Number of .log files to retain when rotate is True. Default 10.

    """
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    root = logging.getLogger()

    # When writing to file with a potentially lower level, set root to the minimum
    # of the two so messages are not filtered before reaching either handler.
    if log_to_file:
        effective_root_level = min(getattr(logging, level), getattr(logging, file_level))
    else:
        effective_root_level = getattr(logging, level)
    root.setLevel(effective_root_level)

    # Some libraries are extremely chatty at DEBUG — keep them at WARNING
    # regardless of the application log level so they don't drown out our own messages.
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    # Remove any pre-existing handlers so this function is idempotent
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(getattr(logging, level))
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_to_file:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Use local timezone for the filename timestamp (ISO 8601)
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        file_handler = logging.FileHandler(log_dir / f"{timestamp}.log", encoding="utf-8")
        file_handler.setLevel(getattr(logging, file_level))
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

        if rotate:
            _rotate_logs(log_dir, keep_last)


async def main() -> None:
    """Run the podcast ad cutter application."""
    args = parse_args()

    try:
        cfg: Config = load_config(args.config)
    except ConfigError as exc:
        sys.stderr.write(f"Failed to load config: {exc}\n")
        sys.exit(1)

    effective_level = "DEBUG" if args.debug else cfg.app.log.level
    effective_log_to_file = args.log_to_file or cfg.app.log.to_file
    configure_logging(
        level=effective_level,
        log_to_file=effective_log_to_file,
        log_dir=cfg.app.paths.log_dir,
        file_level=cfg.app.log.file_level,
        rotate=cfg.app.log.rotate,
        keep_last=cfg.app.log.keep_last,
    )

    pipeline = Pipeline(cfg, feed_name=args.feed)
    try:
        logger.info("Starting...")
        await pipeline.run()
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        sys.exit(1)
