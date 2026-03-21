"""Tests for the FeedParser component."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: F401 — used by _parse_episode tests (Tasks 2-4)
from datetime import datetime, timezone  # noqa: F401 — used by pub_date assertions (Tasks 3-5)

import pytest  # noqa: F401 — used by later tests

from components.feed_parser import Episode, FeedParser, ParsedFeed  # noqa: F401 — FeedParser used in Tasks 2-5
from config.config_loader import FeedConfig

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FEED_CFG = FeedConfig(
    title="Test Pod",
    url="https://example.com/feed.rss",
    enabled=True,
    episodes_to_keep=3,
)

# Minimal valid RSS 2.0 with 3 episodes
VALID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Pod</title>
    <item>
      <guid>ep1</guid>
      <title>Episode 1</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg" length="1000"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <guid>ep2</guid>
      <title>Episode 2</title>
      <enclosure url="https://example.com/ep2.mp3" type="audio/mpeg" length="2000"/>
      <pubDate>Tue, 02 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <guid>ep3</guid>
      <title>Episode 3</title>
      <enclosure url="https://example.com/ep3.mp3" type="audio/mpeg" length="3000"/>
      <pubDate>Wed, 03 Jan 2024 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


def test_episode_model_instantiation() -> None:
    ep = Episode(
        guid="ep1",
        title="Episode 1",
        url="https://example.com/ep1.mp3",
        pub_date=None,
    )
    assert ep.guid == "ep1"
    assert ep.title == "Episode 1"
    assert ep.url == "https://example.com/ep1.mp3"
    assert ep.pub_date is None


def test_parsed_feed_model_instantiation() -> None:
    ep = Episode(guid="ep1", url="https://example.com/ep1.mp3", pub_date=None)
    pf = ParsedFeed(feed_config=FEED_CFG, title="Test Pod", episodes=[ep])
    assert pf.title == "Test Pod"
    assert len(pf.episodes) == 1
    assert pf.feed_config is FEED_CFG
