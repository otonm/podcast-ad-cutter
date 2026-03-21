"""Tests for the FeedParser component."""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from components.feed_parser import FeedParser, _parse_explicit
from models.feed import Episode, FeedParseInput, ParsedFeed

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FEED_INPUT = FeedParseInput(
    config_title="Test Pod",
    feed_url="https://example.com/feed.rss",
    episodes_to_keep=3,
    xml_text="",  # overridden per test via dataclasses.replace()
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
    pf = ParsedFeed(
        config_title="Test Pod",
        feed_url="https://example.com/feed.rss",
        title="Test Pod",
        episodes=[ep],
    )
    assert pf.title == "Test Pod"
    assert len(pf.episodes) == 1
    assert pf.config_title == "Test Pod"
    assert pf.feed_url == "https://example.com/feed.rss"


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
    assert parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text="<<<not xml>>>")) is None


def test_no_channel_element_skipped() -> None:
    """An XML document without a <channel> child returns None."""
    parser = FeedParser()
    assert (
        parser._parse_one(
            dataclasses.replace(FEED_INPUT, xml_text="<rss version='2.0'><notchannel/></rss>")
        )
        is None
    )


def test_feed_title_falls_back_to_config_title() -> None:
    """When the channel has no <title>, config_title is used."""
    parser = FeedParser()
    xml = (
        "<rss version='2.0'><channel>"
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.title == FEED_INPUT.config_title


def test_episodes_limited_to_keep() -> None:
    """Episodes are sliced to episodes_to_keep after parsing."""
    # FEED_INPUT.episodes_to_keep = 3; add 3 more items to VALID_XML (6 total)
    extra = "".join(
        f"<item><guid>ep{i}</guid><title>Episode {i}</title>"
        f'<enclosure url="https://example.com/ep{i}.mp3" type="audio/mpeg" length="{i}"/>'
        f"<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>"
        f"</item>"
        for i in range(4, 7)
    )
    xml_6eps = VALID_XML.replace("</channel>", extra + "</channel>")
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml_6eps))
    assert result is not None
    assert len(result.episodes) == FEED_INPUT.episodes_to_keep
    assert result.episodes[0].guid == "ep1"  # document order preserved


# ---------------------------------------------------------------------------
# parse_all
# ---------------------------------------------------------------------------


def test_parse_all_success() -> None:
    """Valid XML produces a ParsedFeed with all fields populated correctly."""
    parser = FeedParser()
    results = parser.parse_all([dataclasses.replace(FEED_INPUT, xml_text=VALID_XML)])
    assert len(results) == 1
    pf = results[0]
    assert pf.title == "Test Pod"
    assert pf.config_title == FEED_INPUT.config_title
    assert pf.feed_url == FEED_INPUT.feed_url
    assert len(pf.episodes) == FEED_INPUT.episodes_to_keep
    ep = pf.episodes[0]
    assert ep.guid == "ep1"
    assert ep.title == "Episode 1"
    assert ep.url == "https://example.com/ep1.mp3"
    assert ep.pub_date == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_parse_all_empty_input() -> None:
    """An empty inputs list returns an empty results list."""
    assert FeedParser().parse_all([]) == []


def test_parse_all_mixed_feeds() -> None:
    """One valid + one malformed feed: only the valid one appears in results."""
    bad_input = FeedParseInput(
        config_title="Bad Pod",
        feed_url="https://bad.example.com/feed.rss",
        episodes_to_keep=5,
        xml_text="<<<not xml>>>",
    )
    parser = FeedParser()
    results = parser.parse_all(
        [dataclasses.replace(FEED_INPUT, xml_text=VALID_XML), bad_input]
    )
    assert len(results) == 1
    assert results[0].config_title == FEED_INPUT.config_title


# ---------------------------------------------------------------------------
# Model field defaults
# ---------------------------------------------------------------------------


def test_episode_new_fields_default_to_none() -> None:
    ep = Episode(guid="g1", url="https://example.com/ep.mp3")
    assert ep.description is None
    assert ep.explicit is None
    assert ep.duration is None


def test_parsed_feed_new_optional_fields_default_to_none() -> None:
    pf = ParsedFeed(config_title="Pod", feed_url="https://example.com/feed.rss", title="Pod")
    assert pf.description is None
    assert pf.link is None
    assert pf.language is None
    assert pf.copyright is None
    assert pf.author is None
    assert pf.image_url is None
    assert pf.categories == []
    assert pf.explicit is None


def test_parsed_feed_pub_dates_default_to_datetime() -> None:
    pf = ParsedFeed(config_title="Pod", feed_url="https://example.com/feed.rss", title="Pod")
    assert isinstance(pf.pub_date, datetime)
    assert isinstance(pf.last_build_date, datetime)


# ---------------------------------------------------------------------------
# _parse_explicit
# ---------------------------------------------------------------------------


def test_parse_explicit_yes() -> None:
    assert _parse_explicit("yes") is True


def test_parse_explicit_true() -> None:
    assert _parse_explicit("true") is True


def test_parse_explicit_no() -> None:
    assert _parse_explicit("no") is False


def test_parse_explicit_false() -> None:
    assert _parse_explicit("false") is False


def test_parse_explicit_clean() -> None:
    assert _parse_explicit("clean") is False


def test_parse_explicit_none() -> None:
    assert _parse_explicit(None) is None


def test_parse_explicit_blank() -> None:
    assert _parse_explicit("   ") is None


def test_parse_explicit_unknown_value() -> None:
    assert _parse_explicit("maybe") is None
