"""Tests for the FeedParser component."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest  # noqa: F401 — used by later tests

from components.feed_parser import Episode, FeedParser, ParsedFeed
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


# ---------------------------------------------------------------------------
# _parse_episode — enclosure / url
# ---------------------------------------------------------------------------


def test_episode_without_enclosure_skipped() -> None:
    """An <item> with no <enclosure> element must return None."""
    parser = FeedParser()
    item = ET.fromstring("<item><guid>ep1</guid><title>Episode 1</title></item>")
    assert parser._parse_episode(item) is None


def test_episode_empty_enclosure_url_skipped() -> None:
    """An <item> with <enclosure url=""> must return None."""
    parser = FeedParser()
    item = ET.fromstring('<item><enclosure url="" type="audio/mpeg" length="0"/></item>')
    assert parser._parse_episode(item) is None


# ---------------------------------------------------------------------------
# _parse_episode — guid, pub_date
# ---------------------------------------------------------------------------


def test_guid_falls_back_to_url() -> None:
    """When <guid> is absent, the enclosure URL is used as guid."""
    parser = FeedParser()
    item = ET.fromstring(
        "<item>"
        "<title>Ep</title>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="100"/>'
        "</item>"
    )
    episode = parser._parse_episode(item)
    assert episode is not None
    assert episode.guid == "https://example.com/ep.mp3"


def test_pub_date_parsed_to_datetime() -> None:
    """A valid RFC 2822 <pubDate> is parsed into a timezone-aware datetime."""
    parser = FeedParser()
    item = ET.fromstring(
        "<item>"
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="100"/>'
        "<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>"
        "</item>"
    )
    episode = parser._parse_episode(item)
    assert episode is not None
    assert isinstance(episode.pub_date, datetime)
    assert episode.pub_date == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # noqa: UP017 — datetime.UTC is Python 3.13+


def test_pub_date_missing_is_none() -> None:
    """When <pubDate> is absent, pub_date is None."""
    parser = FeedParser()
    item = ET.fromstring(
        "<item>"
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="100"/>'
        "</item>"
    )
    episode = parser._parse_episode(item)
    assert episode is not None
    assert episode.pub_date is None


# ---------------------------------------------------------------------------
# _parse_one
# ---------------------------------------------------------------------------


def test_malformed_xml_skipped() -> None:
    """Malformed XML returns None without raising."""
    parser = FeedParser()
    assert parser._parse_one(FEED_CFG, "<<<not xml>>>") is None


def test_no_channel_element_skipped() -> None:
    """An XML document without a <channel> child returns None."""
    parser = FeedParser()
    assert parser._parse_one(FEED_CFG, "<rss version='2.0'><notchannel/></rss>") is None


def test_feed_title_falls_back_to_config_title() -> None:
    """When the channel has no <title>, feed_config.title is used."""
    parser = FeedParser()
    xml = (
        "<rss version='2.0'><channel>"
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
        "</channel></rss>"
    )
    result = parser._parse_one(FEED_CFG, xml)
    assert result is not None
    assert result.title == FEED_CFG.title


def test_episodes_limited_to_keep() -> None:
    """Episodes are sliced to feed_config.episodes_to_keep after parsing."""
    # FEED_CFG.episodes_to_keep = 3; add 3 more items to VALID_XML (6 total)
    extra = "".join(
        f"<item><guid>ep{i}</guid><title>Episode {i}</title>"
        f'<enclosure url="https://example.com/ep{i}.mp3" type="audio/mpeg" length="{i}"/>'
        f"<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>"
        f"</item>"
        for i in range(4, 7)
    )
    xml_6eps = VALID_XML.replace("</channel>", extra + "</channel>")
    parser = FeedParser()
    result = parser._parse_one(FEED_CFG, xml_6eps)
    assert result is not None
    assert len(result.episodes) == FEED_CFG.episodes_to_keep
    assert result.episodes[0].guid == "ep1"  # document order preserved


# ---------------------------------------------------------------------------
# parse_all
# ---------------------------------------------------------------------------


def test_parse_all_success() -> None:
    """Valid XML produces a ParsedFeed with all fields populated correctly."""
    parser = FeedParser()
    results = parser.parse_all([(FEED_CFG, VALID_XML)])
    assert len(results) == 1
    pf = results[0]
    assert pf.title == "Test Pod"
    assert pf.feed_config is FEED_CFG
    assert len(pf.episodes) == FEED_CFG.episodes_to_keep
    ep = pf.episodes[0]
    assert ep.guid == "ep1"
    assert ep.title == "Episode 1"
    assert ep.url == "https://example.com/ep1.mp3"
    assert ep.pub_date == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_parse_all_empty_input() -> None:
    """An empty downloads list returns an empty results list."""
    assert FeedParser().parse_all([]) == []


def test_parse_all_mixed_feeds() -> None:
    """One valid + one malformed feed: only the valid one appears in results."""
    bad_feed = FeedConfig(
        title="Bad Pod",
        url="https://bad.example.com/feed.rss",
        enabled=True,
        episodes_to_keep=5,
    )
    parser = FeedParser()
    results = parser.parse_all([(FEED_CFG, VALID_XML), (bad_feed, "<<<not xml>>>")])
    assert len(results) == 1
    assert results[0].feed_config is FEED_CFG
