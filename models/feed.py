"""Domain transfer objects for the feed pipeline.

These are plain dataclasses — no config module dependency.  Pipeline is the
sole owner of Config and is responsible for extracting the fields each
component needs and passing them here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class Episode:
    """A single podcast episode extracted from an RSS feed."""

    guid: str
    url: str
    title: str = ""
    pub_date: datetime | None = None


@dataclass
class FeedParseInput:
    """Input contract for FeedParser.parse_all() — plain data, no config types."""

    config_title: str  # configured feed title: used as fallback and identifier
    feed_url: str  # original feed URL, threaded into ParsedFeed for downstream stages
    episodes_to_keep: int
    xml_text: str  # raw RSS XML (named xml_text to avoid shadowing the stdlib xml module)


@dataclass
class ParsedFeed:
    """Result of parsing one RSS feed."""

    config_title: str  # matches FeedParseInput.config_title
    feed_url: str  # original feed URL (preserved so downstream stages don't need config)
    title: str  # parsed from RSS <channel><title>; may differ from config_title
    episodes: list[Episode] = field(default_factory=list)
