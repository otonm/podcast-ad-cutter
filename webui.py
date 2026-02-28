"""Web frontend launcher for Podcast Ad Cutter.

Usage:
    uv run python webui.py [--host 127.0.0.1] [--port 8000] [--reload] [--config config.yaml]

Imports:
    - setup_logging from main.py to configure logging at startup
    - frontend.config_editor.set_config_path so all routes use the right config file
"""

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from config.config_loader import load_config
from frontend.config_editor import set_config_path
from main import setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Podcast Ad Cutter — web UI")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (development only)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Set log level to DEBUG")
    return parser.parse_args()


def main() -> None:
    """Parse arguments, configure logging, then start the uvicorn server."""
    args = _parse_args()

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        sys.stderr.write(f"Failed to load config: {exc}\n")
        raise SystemExit(1) from exc

    log_level = "DEBUG" if args.verbose else cfg.logging.level
    setup_logging(log_level, cfg.logging.log_file)

    # Point config_editor at the selected config file.
    set_config_path(args.config)

    logging.getLogger(__name__).info(
        f"Starting web UI on http://{args.host}:{args.port}  config={args.config}"
    )

    uvicorn.run(
        "frontend.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="warning",  # suppress uvicorn access logs; pipeline logs use root logger
    )


if __name__ == "__main__":
    main()
