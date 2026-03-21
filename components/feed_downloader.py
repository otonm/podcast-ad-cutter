"""Feed downloader — fetches RSS/Atom XML for each podcast feed."""

from __future__ import annotations

import http
import logging
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from config.config_loader import Config, FeedConfig

logger = logging.getLogger(__name__)


class FeedDownloader:
    """Downloads RSS/Atom feed XML over HTTP, one feed at a time.

    A single :class:`aiohttp.ClientSession` is opened for the full batch of
    feeds and closed once all have been attempted.  Failed feeds are logged
    and skipped; the caller receives only the feeds that succeeded.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    async def download_all(
        self,
        feeds: list[FeedConfig],
    ) -> list[tuple[FeedConfig, str]]:
        """Download XML for each feed in order.

        Args:
            feeds: Feeds to fetch.  Order is preserved in the return value.

        Returns:
            List of ``(feed_config, xml_text)`` for every feed that was
            fetched successfully.  Feeds that fail are omitted.

        """
        results: list[tuple[FeedConfig, str]] = []
        async with aiohttp.ClientSession() as session:
            for feed in feeds:
                xml = await self._fetch_one(session, feed)
                if xml is not None:
                    results.append((feed, xml))
        return results

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        feed: FeedConfig,
    ) -> str | None:
        """Fetch the RSS XML for a single feed.

        Args:
            session: Shared aiohttp session.
            feed: Feed configuration containing the URL to fetch.

        Returns:
            Raw XML text on success, or ``None`` if the request failed.

        """
        logger.debug(f"Fetching feed '{feed.title}' from {feed.url}")
        try:
            async with session.get(feed.url) as response:
                logger.debug(
                    f"Feed '{feed.title}' response: HTTP {response.status}, "
                    f"content-type={response.headers.get('Content-Type', 'unknown')}"
                )
                if response.status != http.HTTPStatus.OK:
                    logger.warning(
                        f"Feed '{feed.title}' returned HTTP {response.status}, skipping"
                    )
                    return None
                xml = await response.text()
                logger.debug(f"Feed '{feed.title}' fetched: {len(xml)} characters")
                return xml
        except aiohttp.ClientError:
            logger.error(f"Failed to fetch feed '{feed.title}'")
            return None
