"""Integration tests for FeedParser against real-world RSS feeds.

Uses:
  - tests/static/example.rss  — a snapshot of 'The Daily' by The New York Times.
  - tests/static/example2.rss — a snapshot of 'Prof G Markets' by Scott Galloway.

All expected values are derived directly from those files, so these tests act as
regression guards: any parser change that silently drops or mangles a real-world
field will be caught here.

Each feed is parsed once at module level (FeedParser is synchronous) and shared
across all tests to keep the suite fast.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from components.feed_parser import FeedParser
from models.feed import FeedParseInput, ParsedFeed

# ---------------------------------------------------------------------------
# Shared parse result — computed once for the whole module
# ---------------------------------------------------------------------------

_EXAMPLE_XML = (Path(__file__).parent / "static" / "example.rss").read_text(encoding="utf-8")

# Use episodes_to_keep=37 to get the full feed (37 items in the snapshot).
_FULL_INPUT = FeedParseInput(
    config_title="The Daily",
    feed_url="https://feeds.simplecast.com/Sl5CSM3S",
    episodes_to_keep=37,
    xml_text=_EXAMPLE_XML,
)

_FEED: ParsedFeed = FeedParser().parse_all([_FULL_INPUT])[0]

# ---------------------------------------------------------------------------
# example2.rss — Prof G Markets (Scott Galloway / Vox Media)
# ---------------------------------------------------------------------------

_EXAMPLE2_XML = (Path(__file__).parent / "static" / "example2.rss").read_text(encoding="utf-8")

_FULL_INPUT2 = FeedParseInput(
    config_title="Prof G Markets",
    feed_url="https://feeds.megaphone.fm/profgmarkets",
    episodes_to_keep=100,
    xml_text=_EXAMPLE2_XML,
)

_FEED2: ParsedFeed = FeedParser().parse_all([_FULL_INPUT2])[0]

# ---------------------------------------------------------------------------
# Exact values extracted from example.rss for use as expected values.
# ---------------------------------------------------------------------------

_CHANNEL_IMAGE_URL = (
    "https://image.simplecastcdn.com/images/"
    "7f2f4c05-9c2f-4deb-82b7-b538062bc22d/"
    "73549bf1-94b3-40ff-8aeb-b4054848ec1b/"
    "3000x3000/the-daily-album-art-original.jpg?aid=rss_feed"
)
_EP0_URL = (
    "https://dts.podtrac.com/redirect.mp3/pdst.fm/e/pfx.vpixl.com/6qj4J/"
    "pscrb.fm/rss/p/nyt.simplecastaudio.com/"
    "03d8b493-87fc-4bd1-931f-8a8e9b945d8a/episodes/"
    "e3cf68c5-4b12-40c2-bf1b-6119924be851/audio/128/default.mp3"
    "?aid=rss_feed"
    "&awCollectionId=03d8b493-87fc-4bd1-931f-8a8e9b945d8a"
    "&awEpisodeId=e3cf68c5-4b12-40c2-bf1b-6119924be851"
    "&feed=Sl5CSM3S"
)
_EP0_IMAGE_URL = (
    "https://image.simplecastcdn.com/images/"
    "082bdd7f-2cfd-41ac-b245-e50a79e0e871/"
    "b7946306-0349-432c-9ddf-38c11603890d/"
    "3000x3000/the_interviewapple_spotify_260320.jpg?aid=rss_feed"
)


# ---------------------------------------------------------------------------
# Sanity: the file must parse successfully
# ---------------------------------------------------------------------------


def test_real_feed_parses_successfully() -> None:
    assert _FEED is not None


# ---------------------------------------------------------------------------
# Channel metadata
# ---------------------------------------------------------------------------


def test_real_feed_title() -> None:
    assert _FEED.title == "The Daily"


def test_real_feed_config_title_preserved() -> None:
    assert _FEED.config_title == "The Daily"


def test_real_feed_url_preserved() -> None:
    assert _FEED.feed_url == "https://feeds.simplecast.com/Sl5CSM3S"


def test_real_feed_link() -> None:
    assert _FEED.link == "https://www.nytimes.com/the-daily"


def test_real_feed_language() -> None:
    assert _FEED.language == "en"


def test_real_feed_copyright_starts_with() -> None:
    assert _FEED.copyright is not None
    assert _FEED.copyright.startswith("© 2020-2021 THE NEW YORK TIMES COMPANY")


def test_real_feed_description_starts_with() -> None:
    assert _FEED.description is not None
    assert _FEED.description.startswith("This is what the news should sound like.")


def test_real_feed_author() -> None:
    assert _FEED.author == "The New York Times"


def test_real_feed_explicit_false() -> None:
    # Feed uses <itunes:explicit>false</itunes:explicit>
    assert _FEED.explicit is False


def test_real_feed_image_url() -> None:
    assert _FEED.image_url == _CHANNEL_IMAGE_URL


def test_real_feed_pub_date() -> None:
    assert _FEED.pub_date == datetime(2026, 3, 21, 10, 0, 0, tzinfo=UTC)


def test_real_feed_last_build_date() -> None:
    assert _FEED.last_build_date == datetime(2026, 3, 21, 10, 0, 12, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Categories — real feed uses text= attribute per Apple Podcasts spec
# ---------------------------------------------------------------------------


def test_real_feed_category_news() -> None:
    assert "News" in _FEED.categories


def test_real_feed_category_daily_news_subcategory() -> None:
    assert "Daily News" in _FEED.categories


def test_real_feed_no_extra_categories() -> None:
    # Only one top-level category and one sub-category in this feed.
    assert sorted(_FEED.categories) == ["Daily News", "News"]


# ---------------------------------------------------------------------------
# Episode count and limit behaviour
# ---------------------------------------------------------------------------


def test_real_feed_total_episode_count() -> None:
    assert len(_FEED.episodes) == 37


def test_real_feed_episodes_limit_is_respected() -> None:
    limited_input = FeedParseInput(
        config_title="The Daily",
        feed_url="https://feeds.simplecast.com/Sl5CSM3S",
        episodes_to_keep=5,
        xml_text=_EXAMPLE_XML,
    )
    result = FeedParser().parse_all([limited_input])[0]
    assert len(result.episodes) == 5


def test_real_feed_episodes_limit_exceeding_total_returns_all() -> None:
    large_input = FeedParseInput(
        config_title="The Daily",
        feed_url="https://feeds.simplecast.com/Sl5CSM3S",
        episodes_to_keep=1000,
        xml_text=_EXAMPLE_XML,
    )
    result = FeedParser().parse_all([large_input])[0]
    assert len(result.episodes) == 37


def test_real_feed_episodes_document_order_preserved() -> None:
    # Newest episode is first in the RSS document and must be first in the list.
    assert _FEED.episodes[0].guid == "4d7bea1b-9b61-496c-8b74-e9d8571eb637"
    assert _FEED.episodes[-1].guid == "1e52075f-9370-4a76-8123-d36336d800d1"


# ---------------------------------------------------------------------------
# First episode — all fields
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def first_episode() -> object:
    return _FEED.episodes[0]


def test_real_feed_first_episode_guid(first_episode: object) -> None:
    assert first_episode.guid == "4d7bea1b-9b61-496c-8b74-e9d8571eb637"  # type: ignore[attr-defined]


def test_real_feed_first_episode_title(first_episode: object) -> None:
    # &apos; entities decode to ASCII ' (U+0027); typographic quotes in the title
    # are encoded as U+2018 / U+2019 in the source XML.
    assert first_episode.title == (  # type: ignore[attr-defined]
        "'The Interview': \u2018Baby Reindeer\u2019 Exploded Richard Gadd's Life. "
        "It Also Set Him Free."
    )


def test_real_feed_first_episode_url(first_episode: object) -> None:
    assert first_episode.url == _EP0_URL  # type: ignore[attr-defined]


def test_real_feed_first_episode_pub_date(first_episode: object) -> None:
    assert first_episode.pub_date == datetime(2026, 3, 21, 10, 0, 0, tzinfo=UTC)  # type: ignore[attr-defined]


def test_real_feed_first_episode_duration(first_episode: object) -> None:
    assert first_episode.duration == "00:45:16"  # type: ignore[attr-defined]


def test_real_feed_first_episode_explicit_false(first_episode: object) -> None:
    assert first_episode.explicit is False  # type: ignore[attr-defined]


def test_real_feed_first_episode_image_url(first_episode: object) -> None:
    assert first_episode.image_url == _EP0_IMAGE_URL  # type: ignore[attr-defined]


def test_real_feed_first_episode_description_is_cdata_html(first_episode: object) -> None:
    # ElementTree strips the CDATA wrapper and returns the raw content.
    desc = first_episode.description  # type: ignore[attr-defined]
    assert desc is not None
    assert desc.startswith("<p>The writer and actor found unexpected success")


# ---------------------------------------------------------------------------
# Last episode — spot-check to validate date parsing across years
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def last_episode() -> object:
    return _FEED.episodes[-1]


def test_real_feed_last_episode_guid(last_episode: object) -> None:
    assert last_episode.guid == "1e52075f-9370-4a76-8123-d36336d800d1"  # type: ignore[attr-defined]


def test_real_feed_last_episode_title(last_episode: object) -> None:
    assert last_episode.title == "The Sunday Read: \u2018Who Is the Bad Art Friend?\u2019"  # type: ignore[attr-defined]


def test_real_feed_last_episode_pub_date(last_episode: object) -> None:
    # Oldest episode is from Oct 2021 — confirms multi-year date parsing.
    assert last_episode.pub_date == datetime(2021, 10, 24, 10, 0, 0, tzinfo=UTC)  # type: ignore[attr-defined]


def test_real_feed_last_episode_duration(last_episode: object) -> None:
    assert last_episode.duration == "01:08:25"  # type: ignore[attr-defined]


def test_real_feed_last_episode_has_image_url(last_episode: object) -> None:
    # All 37 episodes in this snapshot carry an episode-level artwork URL.
    assert last_episode.image_url is not None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# All episodes — invariants that must hold across the whole snapshot
# ---------------------------------------------------------------------------


def test_real_feed_all_episodes_have_guid() -> None:
    assert all(ep.guid for ep in _FEED.episodes)


def test_real_feed_all_episodes_have_url() -> None:
    assert all(ep.url.startswith("https://") for ep in _FEED.episodes)


def test_real_feed_all_episodes_have_pub_date() -> None:
    assert all(isinstance(ep.pub_date, datetime) for ep in _FEED.episodes)


def test_real_feed_all_episodes_have_image_url() -> None:
    # Every item in this feed carries <itunes:image href="...">.
    assert all(ep.image_url is not None for ep in _FEED.episodes)


def test_real_feed_all_episodes_have_duration() -> None:
    assert all(ep.duration is not None for ep in _FEED.episodes)


def test_real_feed_all_episodes_have_explicit_set() -> None:
    # Every episode in this snapshot carries <itunes:explicit> — none are None.
    # (Mix of True and False values; use test_real_feed_first_episode_explicit_false
    # and the channel test for specific value checks.)
    assert all(ep.explicit is not None for ep in _FEED.episodes)


# ---------------------------------------------------------------------------
# example.rss — new extended channel fields
# ---------------------------------------------------------------------------


def test_real_feed_itunes_type() -> None:
    assert _FEED.itunes_type == "episodic"


def test_real_feed_owner_name() -> None:
    assert _FEED.owner_name == "The New York Times"


def test_real_feed_owner_email() -> None:
    assert _FEED.owner_email == "thedaily@nytimes.com"


def test_real_feed_itunes_new_feed_url() -> None:
    assert _FEED.itunes_new_feed_url == "https://feeds.simplecast.com/Sl5CSM3S"


def test_real_feed_itunes_summary_starts_with() -> None:
    assert _FEED.itunes_summary is not None
    assert _FEED.itunes_summary.startswith("This is what the news should sound like.")


def test_real_feed_image_title() -> None:
    assert _FEED.image_title == "The Daily"


def test_real_feed_image_link() -> None:
    assert _FEED.image_link == "https://www.nytimes.com/the-daily"


# ---------------------------------------------------------------------------
# example.rss — new extended episode fields (episode 0)
# ---------------------------------------------------------------------------


def test_real_feed_first_episode_type(first_episode: object) -> None:
    assert first_episode.episode_type == "full"  # type: ignore[attr-defined]


def test_real_feed_first_episode_itunes_author(first_episode: object) -> None:
    assert first_episode.itunes_author == "The New York Times"  # type: ignore[attr-defined]


def test_real_feed_first_episode_itunes_summary_set(first_episode: object) -> None:
    assert first_episode.itunes_summary is not None  # type: ignore[attr-defined]
    assert first_episode.itunes_summary.startswith(  # type: ignore[attr-defined]
        "The writer and actor found unexpected success"
    )


def test_real_feed_first_episode_content_encoded_set(first_episode: object) -> None:
    assert first_episode.content_encoded is not None  # type: ignore[attr-defined]
    assert first_episode.content_encoded.startswith("<p>The writer")  # type: ignore[attr-defined]


def test_real_feed_first_episode_link(first_episode: object) -> None:
    assert first_episode.link == "https://www.nytimes.com/the-daily"  # type: ignore[attr-defined]


def test_real_feed_first_episode_author(first_episode: object) -> None:
    assert first_episode.author == "thedaily@nytimes.com (The New York Times)"  # type: ignore[attr-defined]


# ===========================================================================
# example2.rss — Prof G Markets (Scott Galloway / Vox Media)
# ===========================================================================


# ---------------------------------------------------------------------------
# Sanity
# ---------------------------------------------------------------------------


def test_profg_feed_parses_successfully() -> None:
    assert _FEED2 is not None


# ---------------------------------------------------------------------------
# Channel metadata — basic fields
# ---------------------------------------------------------------------------


def test_profg_feed_title() -> None:
    assert _FEED2.title == "Prof G Markets"


def test_profg_feed_link() -> None:
    assert _FEED2.link == "https://podcasts.voxmedia.com/show/prof-g-markets"


def test_profg_feed_language() -> None:
    assert _FEED2.language == "en"


# ---------------------------------------------------------------------------
# Channel metadata — extended fields
# ---------------------------------------------------------------------------


def test_profg_feed_itunes_type() -> None:
    assert _FEED2.itunes_type == "episodic"


def test_profg_feed_owner_name() -> None:
    # Encoded as "Prof G Media &amp; Vox Media" in XML; ElementTree decodes to "&".
    assert _FEED2.owner_name == "Prof G Media & Vox Media"


def test_profg_feed_owner_email() -> None:
    assert _FEED2.owner_email == "podcasting@voxmedia.com"


def test_profg_feed_itunes_summary_starts_with() -> None:
    assert _FEED2.itunes_summary is not None
    assert _FEED2.itunes_summary.startswith("Prof G Markets breaks down the news")


def test_profg_feed_image_title() -> None:
    assert _FEED2.image_title == "Prof G Markets"


def test_profg_feed_image_link() -> None:
    assert _FEED2.image_link == "https://podcasts.voxmedia.com/show/prof-g-markets"


def test_profg_feed_content_encoded_set() -> None:
    # The channel carries a <content:encoded> block with HTML.
    assert _FEED2.content_encoded is not None
    assert "<p>" in _FEED2.content_encoded


# ---------------------------------------------------------------------------
# Episode 0 — extended fields
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profg_first_episode() -> object:
    return _FEED2.episodes[0]


def test_profg_first_episode_type(profg_first_episode: object) -> None:
    assert profg_first_episode.episode_type == "full"  # type: ignore[attr-defined]


def test_profg_first_episode_itunes_author(profg_first_episode: object) -> None:
    assert profg_first_episode.itunes_author == "Vox Media Podcast Network"  # type: ignore[attr-defined]


def test_profg_first_episode_itunes_summary_set(profg_first_episode: object) -> None:
    assert profg_first_episode.itunes_summary is not None  # type: ignore[attr-defined]
    assert profg_first_episode.itunes_summary.startswith(  # type: ignore[attr-defined]
        "Ed Elson and Scott Galloway are joined by Ed Yardeni"
    )


def test_profg_first_episode_content_encoded_set(profg_first_episode: object) -> None:
    assert profg_first_episode.content_encoded is not None  # type: ignore[attr-defined]
    assert "<p>" in profg_first_episode.content_encoded  # type: ignore[attr-defined]
