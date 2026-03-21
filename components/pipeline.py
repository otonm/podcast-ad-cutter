"""Pipeline — top-level orchestrator for the podcast ad-cutting workflow."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from components.feed_downloader import FeedDownloader
from components.feed_parser import FeedParser, ParsedFeed

if TYPE_CHECKING:
    from config.config_loader import Config

logger = logging.getLogger(__name__)


class Pipeline:
    """Coordinates each stage of the podcast ad-cutting workflow.

    Currently the pipeline performs a single stage: downloading the RSS/Atom
    XML for the selected feeds, in the order they appear in the config.
    Further stages (transcription, ad detection, audio cutting) will be
    added here as new components.

    Args:
        config: Validated application config.
        feed_name: When set, process only the feed whose title matches this
            string exactly, regardless of its ``enabled`` flag.  When
            ``None`` (default), only feeds marked ``enabled: true`` are
            processed.

    """

    def __init__(self, config: Config, feed_name: str | None = None) -> None:
        self._config = config
        self._feed_name = feed_name
        self._feed_downloader = FeedDownloader(config)
        self._feed_parser = FeedParser()

    async def run(self) -> list[ParsedFeed]:
        """Execute the pipeline for the selected feeds.

        Returns:
            List of parsed feeds for every feed that was downloaded and
            parsed successfully, in config order.

        Raises:
            ValueError: If ``feed_name`` was supplied but no feed with that
                exact title exists in the config.

        """
        all_feeds = self._config.app.feeds

        if self._feed_name is not None:
            # Force a specific feed through regardless of its enabled flag.
            selected = [f for f in all_feeds if f.title == self._feed_name]
            if not selected:
                available = [f.title for f in all_feeds]
                msg = (
                    f"No feed titled {self._feed_name!r}. "
                    f"Available titles: {available}"
                )
                raise ValueError(msg)
            logger.info(f"Pipeline starting: forcing feed '{self._feed_name}' (enabled override)")
        else:
            selected = [f for f in all_feeds if f.enabled]
            logger.info(
                f"Pipeline starting: {len(selected)} enabled feed(s) of "
                f"{len(all_feeds)} total"
            )

        results = await self._feed_downloader.download_all(selected)
        logger.info(f"Feed download complete: {len(results)} feed(s) retrieved")
        return self._feed_parser.parse_all(results)
