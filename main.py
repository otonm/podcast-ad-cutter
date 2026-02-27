import argparse
import asyncio
import logging
import sys
import warnings
from pathlib import Path

import yaml

from config.config_loader import load_config
from pipeline.exceptions import ConfigError
from pipeline.llm_client import validate_api_keys
from pipeline.runner import run_pipeline

# pydub uses invalid escape sequences in regex strings (a pydub bug); suppress the noise
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pydub")

logger = logging.getLogger(__name__)


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

    litellm_logger = logging.getLogger("LiteLLM")
    
    if level == "INFO":
        # Suppress httpx request and litellm logging at INFO level 
        logging.getLogger("httpx").setLevel(logging.WARNING)
        litellm_logger.setLevel(logging.WARNING)
    
    litellm_logger.propagate = True


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
    except (ConfigError, yaml.YAMLError) as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        sys.exit(1)

    log_level = "DEBUG" if args.verbose else cfg.logging.level
    setup_logging(log_level, cfg.logging.log_file)

    try:
        validate_api_keys(cfg)
    except ConfigError as exc:
        logger.error(f"API key validation failed: {exc}")
        sys.exit(1)

    if args.feed:
        matching = [f for f in cfg.feeds if f.name == args.feed]
        if not matching:
            logger.error(f"Feed not found: {args.feed}")
            sys.exit(1)
        cfg = cfg.model_copy(update={"feeds": matching})

    if args.output:
        new_paths = cfg.paths.model_copy(update={"output_dir": args.output})
        cfg = cfg.model_copy(update={"paths": new_paths})

    if args.min_confidence:
        new_ad = cfg.ad_detection.model_copy(update={"min_confidence": args.min_confidence})
        cfg = cfg.model_copy(update={"ad_detection": new_ad})

    try:
        await run_pipeline(cfg, dry_run=args.dry_run)
    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
