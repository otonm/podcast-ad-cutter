"""CLI entry point for the podcast ad cutter project."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_config(_config_path: Path) -> dict[str, Any]:
    """Load configuration from YAML file."""
    # Stub function - to be implemented
    return {}


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
    return parser.parse_args()


async def main() -> None:
    """Run the podcast ad cutter application."""
    args = parse_args()

    try:
        load_config(args.config)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Failed to load config: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        sys.exit(1)
