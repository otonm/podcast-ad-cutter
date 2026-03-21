"""FeedParser — parses downloaded RSS/Atom XML into structured data."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

from pydantic import BaseModel

from config.config_loader import FeedConfig

logger = logging.getLogger(__name__)


class Episode(BaseModel):
    """Represents a single podcast episode extracted from an RSS feed."""

    guid: str
    title: str = ""  # default "" if <title> absent — must be explicit so Pydantic does not raise
    url: str
    pub_date: datetime | None


class ParsedFeed(BaseModel):
    """Represents a parsed RSS feed with its episodes."""

    feed_config: FeedConfig
    title: str
    episodes: list[Episode]


class FeedParser:
    """Stateless RSS/Atom XML parser. No constructor args."""

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
            except Exception:  # noqa: BLE001
                logger.debug(f"Could not parse pubDate {pub_date_str!r} — setting to None")

        return Episode(guid=guid, title=title, url=url, pub_date=pub_date)
