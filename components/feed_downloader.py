"""Feed downloader — fetches RSS/Atom XML for each podcast feed."""

from __future__ import annotations

import http
import logging

import aiohttp

logger = logging.getLogger(__name__)


class FeedDownloader:
    """Downloads RSS/Atom feed XML over HTTP, one feed at a time.

    A single :class:`aiohttp.ClientSession` is opened for the full batch of
    feeds and closed once all have been attempted.  Failed feeds are logged
    and skipped; the caller receives only the feeds that succeeded.

    This class has no dependency on the config module.  The caller (Pipeline)
    is responsible for extracting the ``(title, url)`` pairs it needs and
    passing them here.
    """

    async def download_all(
        self,
        feeds: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        """Download XML for each feed in order.

        Args:
            feeds: ``(title, url)`` pairs to fetch.  Order is preserved in the
                return value.

        Returns:
            List of ``(title, xml_text)`` for every feed that was fetched
            successfully.  Feeds that fail are omitted.

        """
        results: list[tuple[str, str]] = []
        async with aiohttp.ClientSession() as session:
            for title, url in feeds:
                xml = await self._fetch_one(session, title, url)
                if xml is not None:
                    results.append((title, xml))
        return results

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        title: str,
        url: str,
    ) -> str | None:
        """Fetch the RSS XML for a single feed.

        Args:
            session: Shared aiohttp session.
            title: Feed title used in log messages.
            url: URL to fetch.

        Returns:
            Raw XML text on success, or ``None`` if the request failed.

        """
        logger.debug(f"Fetching feed '{title}' from {url}")
        try:
            async with session.get(url) as response:
                logger.debug(
                    f"Feed '{title}' response: HTTP {response.status}, "
                    f"content-type={response.headers.get('Content-Type', 'unknown')}"
                )
                if response.status != http.HTTPStatus.OK:
                    logger.warning(f"Feed '{title}' returned HTTP {response.status}, skipping")
                    return None
                xml = await response.text()
                logger.debug(f"Feed '{title}' fetched: {len(xml)} characters")
                return xml
        except aiohttp.ClientError:
            logger.error(f"Failed to fetch feed '{title}'")
            return None
