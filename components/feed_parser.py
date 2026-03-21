"""FeedParser — parses downloaded RSS/Atom XML into structured data."""

from __future__ import annotations

import logging
from datetime import datetime

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
