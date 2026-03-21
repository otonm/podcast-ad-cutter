"""Tests for the FeedParser component."""

from __future__ import annotations

import dataclasses
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from components.feed_parser import FeedParser, _parse_date, _parse_explicit
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

# Valid RSS 2.0 with iTunes namespace and one representative value per new field
VALID_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Test Pod</title>
    <description>A great podcast about testing.</description>
    <link>https://testpod.example.com</link>
    <language>en</language>
    <copyright>&#169; 2024 Test Pod</copyright>
    <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    <lastBuildDate>Tue, 02 Jan 2024 12:00:00 +0000</lastBuildDate>
    <image>
      <url>https://example.com/cover.jpg</url>
      <title>Test Pod</title>
      <link>https://testpod.example.com</link>
    </image>
    <itunes:author>Test Author</itunes:author>
    <itunes:image href="https://example.com/itunes-cover.jpg"/>
    <itunes:explicit>yes</itunes:explicit>
    <itunes:category text="Technology">
      <itunes:category text="Tech News"/>
    </itunes:category>
    <item>
      <guid>ep1</guid>
      <title>Episode 1</title>
      <description>Episode 1 description.</description>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg" length="1000"/>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <itunes:duration>01:23:45</itunes:duration>
      <itunes:explicit>no</itunes:explicit>
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
    )
    assert ep.guid == "ep1"
    assert ep.title == "Episode 1"
    assert ep.url == "https://example.com/ep1.mp3"
    assert isinstance(ep.pub_date, datetime)


def test_parsed_feed_model_instantiation() -> None:
    ep = Episode(guid="ep1", url="https://example.com/ep1.mp3")
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


def test_pub_date_missing_falls_back_to_now() -> None:
    """When <pubDate> is absent, pub_date is a datetime close to now."""
    parser = FeedParser()
    item = ET.fromstring(
        "<item>"
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="100"/>'
        "</item>"
    )
    episode = parser._parse_episode(item)
    assert episode is not None
    assert isinstance(episode.pub_date, datetime)


# ---------------------------------------------------------------------------
# _parse_one
# ---------------------------------------------------------------------------


def test_items_without_enclosure_skipped_inside_parse_one() -> None:
    """An <item> without an enclosure is silently dropped; valid items still returned."""
    parser = FeedParser()
    xml = (
        "<rss version='2.0'><channel><title>Pod</title>"
        # no-enclosure item — must be skipped
        "<item><guid>bad</guid><title>No enclosure</title></item>"
        # valid item — must be returned
        "<item><guid>good</guid><title>Good Ep</title>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert len(result.episodes) == 1
    assert result.episodes[0].guid == "good"


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


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


def test_parse_date_valid_rfc2822() -> None:
    result = _parse_date("Mon, 01 Jan 2024 12:00:00 +0000")
    assert result == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_parse_date_none_returns_datetime() -> None:
    result = _parse_date(None)
    assert isinstance(result, datetime)


def test_parse_date_empty_string_returns_datetime() -> None:
    # xml.etree.ElementTree.findtext() returns "" for <pubDate></pubDate>
    result = _parse_date("")
    assert isinstance(result, datetime)


def test_parse_date_invalid_string_returns_datetime() -> None:
    result = _parse_date("not a date at all")
    assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# _parse_one — channel metadata
# ---------------------------------------------------------------------------


def test_feed_description_from_description_element() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.description == "A great podcast about testing."


def test_feed_description_falls_back_to_itunes_summary() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        "<itunes:summary>iTunes summary fallback.</itunes:summary>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.description == "iTunes summary fallback."


def test_feed_link() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.link == "https://testpod.example.com"


def test_feed_language() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.language == "en"


def test_feed_copyright() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.copyright == "\u00a9 2024 Test Pod"


def test_feed_author_from_itunes_author() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.author == "Test Author"


def test_feed_author_falls_back_to_managing_editor() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        "<managingEditor>editor@example.com</managingEditor>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.author == "editor@example.com"


def test_feed_image_url_from_image_element() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.image_url == "https://example.com/cover.jpg"


def test_feed_image_url_falls_back_to_itunes_image_when_no_image_element() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        '<itunes:image href="https://example.com/itunes.jpg"/>'
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.image_url == "https://example.com/itunes.jpg"


def test_feed_image_url_falls_back_to_itunes_image_when_url_child_missing() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        "<image><title>No url child here</title></image>"
        '<itunes:image href="https://example.com/itunes.jpg"/>'
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.image_url == "https://example.com/itunes.jpg"


def test_feed_categories_use_text_attribute() -> None:
    """<itunes:category text="..."> is the attribute per the Apple spec."""
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        '<itunes:category text="Technology"/>'
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert "Technology" in result.categories


def test_feed_categories_top_level() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert "Technology" in result.categories


def test_feed_categories_includes_subcategories() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert "Tech News" in result.categories


def test_feed_categories_exclude_episode_level_tags() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title>"
        '<itunes:category text="FeedCat"/>'
        "<item>"
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        '<itunes:category text="EpisodeCat"/>'
        "</item>"
        "</channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert "FeedCat" in result.categories
    assert "EpisodeCat" not in result.categories


def test_feed_explicit_true() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.explicit is True


def test_feed_explicit_false() -> None:
    parser = FeedParser()
    xml = (
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<channel><title>Pod</title><itunes:explicit>no</itunes:explicit></channel></rss>"
    )
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.explicit is False


def test_feed_explicit_none_when_absent() -> None:
    parser = FeedParser()
    xml = "<rss version='2.0'><channel><title>Pod</title></channel></rss>"
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert result.explicit is None


def test_feed_pub_date_parsed() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.pub_date == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_feed_pub_date_is_datetime_when_absent() -> None:
    parser = FeedParser()
    xml = "<rss version='2.0'><channel><title>Pod</title></channel></rss>"
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert isinstance(result.pub_date, datetime)


def test_feed_last_build_date_parsed() -> None:
    parser = FeedParser()
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=VALID_XML))
    assert result is not None
    assert result.last_build_date == datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017


def test_feed_last_build_date_is_datetime_when_absent() -> None:
    parser = FeedParser()
    xml = "<rss version='2.0'><channel><title>Pod</title></channel></rss>"
    result = parser._parse_one(dataclasses.replace(FEED_INPUT, xml_text=xml))
    assert result is not None
    assert isinstance(result.last_build_date, datetime)


# ---------------------------------------------------------------------------
# _parse_episode — new metadata fields
# ---------------------------------------------------------------------------


def test_episode_description_from_description_element() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<description>Episode description.</description>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.description == "Episode description."


def test_episode_description_falls_back_to_itunes_summary() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:summary>iTunes episode summary.</itunes:summary>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.description == "iTunes episode summary."


def test_episode_duration_parsed() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:duration>01:23:45</itunes:duration>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.duration == "01:23:45"


def test_episode_duration_none_when_absent() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.duration is None


def test_episode_duration_none_when_whitespace_only() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:duration>   </itunes:duration>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.duration is None


def test_episode_explicit_true() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:explicit>yes</itunes:explicit>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.explicit is True


def test_episode_explicit_false() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "<itunes:explicit>no</itunes:explicit>"
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.explicit is False


def test_episode_explicit_none_when_absent() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.explicit is None


def test_episode_image_url_from_itunes_image() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        '<item xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        "<guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        '<itunes:image href="https://example.com/ep-cover.jpg"/>'
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.image_url == "https://example.com/ep-cover.jpg"


def test_episode_image_url_none_when_absent() -> None:
    parser = FeedParser()
    item = ET.fromstring(
        "<item><guid>ep1</guid>"
        '<enclosure url="https://example.com/ep.mp3" type="audio/mpeg" length="0"/>'
        "</item>"
    )
    ep = parser._parse_episode(item)
    assert ep is not None
    assert ep.image_url is None
