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


def configure_logging(*, level: str, log_to_file: bool, log_dir: Path = Path("logs")) -> None:
    """Configure the root logger with a stream handler and an optional file handler.

    Args:
        level: Logging level name — one of DEBUG, INFO, WARNING, ERROR, CRITICAL.
        log_to_file: When True, write logs to a timestamped file in log_dir.
        log_dir: Directory for log files (created if absent). Defaults to logs/.

    """
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level))

    # Some libraries are extremely chatty at DEBUG — keep them at WARNING
    # regardless of the application log level so they don't drown out our own messages.
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    # Remove any pre-existing handlers so this function is idempotent
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    if log_to_file:
        log_dir.mkdir(parents=True, exist_ok=True)
        # Use local timezone for the filename timestamp
        timestamp = datetime.now().astimezone().strftime("%d.%m.%Y-%H.%M.%S")
        file_handler = logging.FileHandler(log_dir / f"{timestamp}.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


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
