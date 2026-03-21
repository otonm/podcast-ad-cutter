"""Pipeline — top-level orchestrator for the podcast ad-cutting workflow."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from components.feed_downloader import FeedDownloader

if TYPE_CHECKING:
    from config.config_loader import Config, FeedConfig

logger = logging.getLogger(__name__)


class Pipeline:
    """Coordinates each stage of the podcast ad-cutting pipeline.

    Currently the pipeline performs a single stage: downloading the RSS/Atom
    XML for every enabled feed, in the order they appear in the config.
    Further stages (transcription, ad detection, audio cutting) will be
    added here as new components.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._feed_downloader = FeedDownloader(config)

    async def run(self) -> list[tuple[FeedConfig, str]]:
        """Execute the pipeline for all enabled feeds.

        Returns:
            List of ``(feed_config, xml_text)`` for every feed that was
            downloaded successfully, in config order.

        """
        enabled_feeds = [f for f in self._config.app.feeds if f.enabled]
        logger.info(
            f"Pipeline starting: {len(enabled_feeds)} enabled feed(s) of "
            f"{len(self._config.app.feeds)} total"
        )
        results = await self._feed_downloader.download_all(enabled_feeds)
        logger.info(f"Feed download complete: {len(results)} feed(s) retrieved")
        return results
