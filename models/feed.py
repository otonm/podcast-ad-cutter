"""Domain transfer objects for the feed pipeline.

These are plain dataclasses — no config module dependency.  Pipeline is the
sole owner of Config and is responsible for extracting the fields each
component needs and passing them here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Episode:
    """A single podcast episode extracted from an RSS feed."""

    guid: str
    url: str
    title: str = ""
    # Absent or unparseable pubDate falls back to current local datetime (same as ParsedFeed).
    pub_date: datetime = field(default_factory=lambda: datetime.now().astimezone())
    description: str | None = None
    explicit: bool | None = None
    # Raw string (e.g. "01:23:45"); will be typed to a duration type in a future task.
    duration: str | None = None
    image_url: str | None = None  # episode-level artwork (<itunes:image href="..."/>)


@dataclass
class FeedParseInput:
    """Input contract for FeedParser.parse_all() — plain data, no config types."""

    config_title: str  # configured feed title: used as fallback and identifier
    feed_url: str  # original feed URL, threaded into ParsedFeed for downstream stages
    episodes_to_keep: int
    xml_text: str  # raw RSS XML (named xml_text to avoid shadowing the stdlib xml module)


@dataclass
class PublisherInput:
    """Input contract for FeedPublisher — assembled by Pipeline from DB data and ParsedFeed metadata.

    Pipeline is responsible for combining the episode list (from DB) with the
    channel-level metadata (from ParsedFeed) and passing the result here.
    FeedPublisher receives only this object and performs no database access.
    """

    base_url: str  # server base URL; slug and self-link are derived by FeedPublisher
    title: str
    episodes: list[Episode]
    description: str | None = None
    link: str | None = None  # original podcast website URL (kept verbatim)
    language: str | None = None
    copyright: str | None = None
    author: str | None = None
    image_url: str | None = None
    categories: list[str] = field(default_factory=list)
    explicit: bool | None = None
    pub_date: datetime = field(default_factory=lambda: datetime.now().astimezone())
    last_build_date: datetime = field(default_factory=lambda: datetime.now().astimezone())


@dataclass
class ParsedFeed:
    """Result of parsing one RSS feed."""

    config_title: str  # matches FeedParseInput.config_title
    feed_url: str  # original feed URL (preserved so downstream stages don't need config)
    title: str  # parsed from RSS <channel><title>; may differ from config_title
    episodes: list[Episode] = field(default_factory=list)
    description: str | None = None
    link: str | None = None
    language: str | None = None
    copyright: str | None = None
    author: str | None = None
    image_url: str | None = None
    categories: list[str] = field(default_factory=list)
    explicit: bool | None = None
    # Feed-level dates always resolve to a concrete datetime (absent → current local time).
    # This differs from Episode.pub_date which stays None when the date is unknown.
    pub_date: datetime = field(default_factory=lambda: datetime.now().astimezone())
    last_build_date: datetime = field(default_factory=lambda: datetime.now().astimezone())
