"""FeedParser — parses downloaded RSS/Atom XML into structured data."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from models.feed import Episode, FeedParseInput, ParsedFeed

logger = logging.getLogger(__name__)

_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def _parse_explicit(text: str | None) -> bool | None:
    """Normalise <itunes:explicit> text to bool or None.

    Returns True for "yes"/"true", False for "no"/"false"/"clean",
    and None for absent, blank, or any unrecognised value.
    """
    if not text or not text.strip():
        return None
    normalized = text.strip().lower()
    if normalized in ("yes", "true"):
        return True
    if normalized in ("no", "false", "clean"):
        return False
    return None


def _parse_date(text: str | None) -> datetime:
    """Parse an RFC 2822 date string, falling back to the current local datetime.

    Used for feed-level dates only. Episode dates use a separate path that
    returns None on failure (see _parse_episode).

    Args:
        text: Raw date string from XML, or None if the element was absent.

    Returns:
        Parsed datetime, or datetime.now().astimezone() if text is absent,
        empty, blank, or unparseable.

    """
    if not text or not text.strip():
        return datetime.now().astimezone()
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return datetime.now().astimezone()


class FeedParser:
    """Stateless RSS/Atom XML parser. No constructor args."""

    def parse_all(self, inputs: list[FeedParseInput]) -> list[ParsedFeed]:
        """Parse all downloaded XML blobs.

        Failed feeds are omitted from the result list (logged at WARNING level
        inside ``_parse_one``).

        Args:
            inputs: List of :class:`FeedParseInput` objects, each carrying the
                raw XML and the metadata needed to parse it.

        Returns:
            List of successfully parsed feeds in input order.

        """
        results: list[ParsedFeed] = []
        for feed_input in inputs:
            parsed = self._parse_one(feed_input)
            if parsed is not None:
                results.append(parsed)
        logger.info(
            f"Feed parsing complete: {len(results)}/{len(inputs)} feed(s) parsed successfully"
        )
        return results

    def _parse_one(self, feed_input: FeedParseInput) -> ParsedFeed | None:
        """Parse a single XML blob into a ParsedFeed.

        Returns ``None`` if the XML is malformed or has no ``<channel>`` element.
        """
        try:
            root = ET.fromstring(feed_input.xml_text)  # noqa: S314 — feed XML is from configured trusted sources
        except ET.ParseError:
            logger.warning(f"Failed to parse XML for feed '{feed_input.config_title}'")
            return None

        channel = root.find("channel")
        if channel is None:
            logger.warning(f"No <channel> element in feed '{feed_input.config_title}'")
            return None

        title = (channel.findtext("title") or feed_input.config_title).strip()

        # RSS feeds are newest-first by convention; stop as soon as we have N valid episodes.
        episodes: list[Episode] = []
        for item in channel.findall("item"):
            if len(episodes) >= feed_input.episodes_to_keep:
                break
            episode = self._parse_episode(item)
            if episode is None:
                logger.debug(
                    f"Skipping item without valid enclosure in feed '{feed_input.config_title}'"
                )
            else:
                episodes.append(episode)

        return ParsedFeed(
            config_title=feed_input.config_title,
            feed_url=feed_input.feed_url,
            title=title,
            episodes=episodes,
        )

    def _parse_episode(self, item: ET.Element) -> Episode | None:
        """Parse a single <item> element.

        Returns ``None`` if the item has no valid enclosure URL.
        """
        enclosure = item.find("enclosure")
        if enclosure is None:
            return None
        url = (enclosure.get("url") or "").strip()
        if not url:
            return None

        title = (item.findtext("title") or "").strip()

        guid_el = item.find("guid")
        guid_text = (guid_el.text or "").strip() if guid_el is not None else ""
        guid = guid_text or url  # fall back to URL when guid is absent or blank

        pub_date: datetime | None = None
        pub_date_str = item.findtext("pubDate")
        if pub_date_str:
            try:
                pub_date = parsedate_to_datetime(pub_date_str)
            except (TypeError, ValueError):
                # TypeError: parsedate_tz returned None (unrecognised format)
                # ValueError: date components out of range
                pub_date = datetime.now().astimezone()
                logger.debug(
                    f"Could not parse pubDate {pub_date_str!r} — falling back to current local datetime"
                )

        return Episode(guid=guid, title=title, url=url, pub_date=pub_date)
