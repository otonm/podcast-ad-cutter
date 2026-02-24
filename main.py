import argparse
import asyncio
import logging
import sys
import warnings
from pathlib import Path

# pydub uses invalid escape sequences in regex strings (a pydub bug); suppress the noise
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pydub")

from config_loader import load_config
from pipeline.runner import run_pipeline


def setup_logging(level: str, log_file: str | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(Path(log_file)))
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
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
        "--use-cache",
        action="store_true",
        help="Skip transcription if already cached",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect ads but skip audio cutting",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Set log level to DEBUG",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        sys.exit(1)

    log_level = "DEBUG" if args.verbose else cfg.logging.level
    setup_logging(log_level, cfg.logging.log_file)

    if args.feed:
        cfg.feeds = [f for f in cfg.feeds if f.name == args.feed]
        if not cfg.feeds:
            logging.error("Feed not found: %s", args.feed)
            sys.exit(1)

    if args.output:
        cfg.paths.output_dir = args.output

    if args.min_confidence:
        cfg.ad_detection.min_confidence = args.min_confidence

    try:
        await run_pipeline(cfg, dry_run=args.dry_run)
    except Exception as exc:
        logging.error("Pipeline failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
