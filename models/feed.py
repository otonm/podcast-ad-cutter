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
    # Extended episode metadata
    episode_type: str | None = None  # itunes:episodeType — "full", "trailer", or "bonus"
    itunes_author: str | None = None
    itunes_subtitle: str | None = None
    itunes_summary: str | None = None
    content_encoded: str | None = None  # rich HTML from <content:encoded>
    link: str | None = None  # episode permalink URL
    author: str | None = None  # standard RSS author (email format, e.g. "email@host (Name)")
    itunes_title: str | None = None  # iTunes-specific title; may differ from title
    episode_number: int | None = None  # itunes:episode
    season_number: int | None = None  # itunes:season
    itunes_block: bool = False  # itunes:block — hides episode from Apple Podcasts
    length: int = 0  # enclosure file size in bytes; updated after audio processing


@dataclass
class AudioMetadata:
    """Audio metadata extracted from a downloaded episode file via ffprobe."""

    guid: str
    duration: float  # exact seconds (sub-second precision from ffprobe)
    codec: str       # e.g. "aac", "mp3"
    channels: int    # 1 = mono, 2 = stereo
    bitrate: int     # bits per second


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
    # Extended channel metadata
    itunes_type: str | None = None  # podcast type: "episodic" or "serial"
    itunes_subtitle: str | None = None
    itunes_summary: str | None = None
    owner_name: str | None = None  # from itunes:owner/itunes:name
    owner_email: str | None = None  # from itunes:owner/itunes:email
    image_title: str | None = None  # from standard RSS <image><title>
    image_link: str | None = None  # from standard RSS <image><link>
    content_encoded: str | None = None  # from <content:encoded>
    itunes_new_feed_url: str | None = None
    itunes_complete: bool = False  # signals feed has no future episodes
    podcast_guid: str | None = None  # Podcast 2.0 unique show identifier


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
    # Extended channel metadata
    itunes_type: str | None = None  # podcast type: "episodic" or "serial"
    itunes_subtitle: str | None = None
    itunes_summary: str | None = None
    owner_name: str | None = None  # from itunes:owner/itunes:name
    owner_email: str | None = None  # from itunes:owner/itunes:email
    image_title: str | None = None  # from standard RSS <image><title>
    image_link: str | None = None  # from standard RSS <image><link>
    content_encoded: str | None = None  # from <content:encoded>
    itunes_new_feed_url: str | None = None
    itunes_complete: bool = False  # signals feed has no future episodes
