"""Tests for FeedPublisher RSS generation."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from components.feed_publisher import FeedPublisher, episode_filename, episode_url
from models.feed import Episode, PublisherInput

if TYPE_CHECKING:
    from pathlib import Path

_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_ATOM = "http://www.w3.org/2005/Atom"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pub_date() -> datetime:
    return datetime(2026, 3, 21, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def episodes(pub_date: datetime) -> list[Episode]:
    return [
        Episode(
            guid="guid-1",
            url="https://origin.com/ep1.mp3",
            title="Episode One",
            pub_date=pub_date,
            description="First episode",
            explicit=False,
            duration="01:00:00",
        ),
        Episode(
            guid="guid-2",
            url="https://origin.com/ep2.mp3",
            title="Episode Two",
            pub_date=pub_date,
        ),
    ]


@pytest.fixture
def feed_input(episodes: list[Episode]) -> PublisherInput:
    return PublisherInput(
        base_url="https://podcasts.example.com",
        title="My Podcast",
        episodes=episodes,
        description="A great podcast",
        link="https://mypodcast.com",
        language="en",
        copyright="2026 My Podcast",
        author="Jane Doe",
        image_url="https://mypodcast.com/cover.jpg",
        categories=["Technology", "Science"],
        explicit=False,
    )


# ---------------------------------------------------------------------------
# Tests: file creation
# ---------------------------------------------------------------------------


async def test_publish_creates_rss_file(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    assert path.exists()


async def test_publish_returns_correct_path(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    assert path == tmp_path / "my-podcast.rss"


async def test_publish_creates_output_dir_if_absent(tmp_path: Path, feed_input: PublisherInput) -> None:
    nested = tmp_path / "feeds"
    publisher = FeedPublisher(nested)
    path = await publisher.publish(feed_input)
    assert path.exists()


async def test_published_feed_is_valid_xml(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    ET.parse(str(path))  # must not raise


async def test_publish_overwrites_existing_feed(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    await publisher.publish(feed_input)
    await publisher.publish(feed_input)  # must not raise
    path = tmp_path / "my-podcast.rss"
    assert path.exists()


# ---------------------------------------------------------------------------
# Tests: channel metadata
# ---------------------------------------------------------------------------


async def test_published_feed_has_rss_root(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    root = ET.parse(str(path)).getroot()
    assert root.tag == "rss"
    assert root.get("version") == "2.0"


async def test_published_feed_channel_title(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext("title") == "My Podcast"


async def test_published_feed_has_atom_self_link(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    atom_link = channel.find(f"{{{_ATOM}}}link")
    assert atom_link is not None
    assert atom_link.get("rel") == "self"
    assert atom_link.get("href") == "https://podcasts.example.com/my-podcast.rss"


async def test_published_feed_channel_description(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext("description") == "A great podcast"


async def test_published_feed_channel_language(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext("language") == "en"


async def test_published_feed_itunes_author(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{_ITUNES}}}author") == "Jane Doe"


async def test_published_feed_itunes_image(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    img = channel.find(f"{{{_ITUNES}}}image")
    assert img is not None
    assert img.get("href") == "https://mypodcast.com/cover.jpg"


async def test_published_feed_itunes_categories(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    cats = [el.get("label") for el in channel.findall(f"{{{_ITUNES}}}category")]
    assert "Technology" in cats
    assert "Science" in cats


# ---------------------------------------------------------------------------
# Tests: episode items
# ---------------------------------------------------------------------------


async def test_published_feed_episode_count(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert len(channel.findall("item")) == 2


async def test_published_feed_episode_title(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert items[0].findtext("title") == "Episode One"


async def test_published_feed_episode_guid(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert items[0].findtext("guid") == "guid-1"


async def test_published_feed_episode_enclosure_url(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    items = channel.findall("item")
    enclosure = items[0].find("enclosure")
    assert enclosure is not None
    assert enclosure.get("url") == "https://origin.com/ep1.mp3"


async def test_published_feed_episode_description(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert items[0].findtext("description") == "First episode"


async def test_published_feed_episode_duration(tmp_path: Path, feed_input: PublisherInput) -> None:
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert items[0].findtext(f"{{{_ITUNES}}}duration") == "01:00:00"


async def test_published_feed_episode_optional_fields_absent_when_none(
    tmp_path: Path, episodes: list[Episode]
) -> None:
    """An episode with no description/explicit/duration must omit those elements."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Sparse Feed",
        episodes=[episodes[1]],  # episode Two has no description/explicit/duration
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.find("description") is None
    assert item.find(f"{{{_ITUNES}}}duration") is None
    assert item.find(f"{{{_ITUNES}}}explicit") is None


async def test_published_feed_episode_itunes_image(
    tmp_path: Path, pub_date: datetime
) -> None:
    """An episode with image_url must include <itunes:image href='...'/>."""
    ep = Episode(
        guid="guid-img",
        url="https://origin.com/ep.mp3",
        title="Episode With Art",
        pub_date=pub_date,
        image_url="https://example.com/ep-cover.jpg",
    )
    feed = PublisherInput(base_url="https://x.com", title="Art Feed", episodes=[ep])
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    img = item.find(f"{{{_ITUNES}}}image")
    assert img is not None
    assert img.get("href") == "https://example.com/ep-cover.jpg"


async def test_published_feed_episode_itunes_image_absent_when_none(
    tmp_path: Path, pub_date: datetime
) -> None:
    """An episode without image_url must omit <itunes:image/>."""
    ep = Episode(
        guid="guid-no-img",
        url="https://origin.com/ep.mp3",
        title="Episode Without Art",
        pub_date=pub_date,
    )
    feed = PublisherInput(base_url="https://x.com", title="No Art Feed", episodes=[ep])
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.find(f"{{{_ITUNES}}}image") is None


async def test_published_feed_empty_episodes_is_valid(tmp_path: Path) -> None:
    """A feed with zero episodes must still produce valid RSS."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Empty Feed",
        episodes=[],
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findall("item") == []


# ---------------------------------------------------------------------------
# Tests: update_episode_url
# ---------------------------------------------------------------------------


async def test_update_episode_url_changes_enclosure(
    tmp_path: Path, feed_input: PublisherInput
) -> None:
    publisher = FeedPublisher(tmp_path)
    await publisher.publish(feed_input)
    await publisher.update_episode_url("My Podcast", "guid-1", "https://local/processed.mp3")

    channel = ET.parse(str(tmp_path / "my-podcast.rss")).getroot().find("channel")
    assert channel is not None
    item = next(i for i in channel.findall("item") if i.findtext("guid") == "guid-1")
    assert item.find("enclosure") is not None
    assert item.find("enclosure").get("url") == "https://local/processed.mp3"  # type: ignore[union-attr]


async def test_update_episode_url_updates_mime_type(
    tmp_path: Path, feed_input: PublisherInput
) -> None:
    """MIME type in the enclosure must be derived from the new URL's extension."""
    publisher = FeedPublisher(tmp_path)
    await publisher.publish(feed_input)
    await publisher.update_episode_url("My Podcast", "guid-1", "https://local/processed.m4a")

    channel = ET.parse(str(tmp_path / "my-podcast.rss")).getroot().find("channel")
    assert channel is not None
    item = next(i for i in channel.findall("item") if i.findtext("guid") == "guid-1")
    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert enclosure.get("type") == "audio/x-m4a"


async def test_update_episode_url_leaves_other_items_unchanged(
    tmp_path: Path, feed_input: PublisherInput
) -> None:
    publisher = FeedPublisher(tmp_path)
    await publisher.publish(feed_input)
    await publisher.update_episode_url("My Podcast", "guid-1", "https://local/processed.mp3")

    channel = ET.parse(str(tmp_path / "my-podcast.rss")).getroot().find("channel")
    assert channel is not None
    item2 = next(i for i in channel.findall("item") if i.findtext("guid") == "guid-2")
    assert item2.find("enclosure") is not None
    assert item2.find("enclosure").get("url") == "https://origin.com/ep2.mp3"  # type: ignore[union-attr]


async def test_update_episode_url_returns_feed_path(
    tmp_path: Path, feed_input: PublisherInput
) -> None:
    publisher = FeedPublisher(tmp_path)
    await publisher.publish(feed_input)
    path = await publisher.update_episode_url("My Podcast", "guid-1", "https://local/processed.mp3")
    assert path == tmp_path / "my-podcast.rss"


async def test_update_episode_url_raises_if_feed_missing(tmp_path: Path) -> None:
    publisher = FeedPublisher(tmp_path)
    with pytest.raises(FileNotFoundError):
        await publisher.update_episode_url("No Such Feed", "guid-1", "https://local/x.mp3")


async def test_update_episode_url_raises_if_guid_not_found(
    tmp_path: Path, feed_input: PublisherInput
) -> None:
    publisher = FeedPublisher(tmp_path)
    await publisher.publish(feed_input)
    with pytest.raises(KeyError, match="guid-unknown"):
        await publisher.update_episode_url("My Podcast", "guid-unknown", "https://local/x.mp3")


async def test_update_episode_url_raises_key_error_when_no_channel(tmp_path: Path) -> None:
    """Defensive: RSS file with no <channel> element raises KeyError."""
    feed_path = tmp_path / "no-channel.rss"
    feed_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"></rss>',
        encoding="utf-8",
    )
    publisher = FeedPublisher(tmp_path)
    with pytest.raises(KeyError):
        await publisher.update_episode_url("No Channel", "any-guid", "https://x.com/ep.mp3")


async def test_update_episode_url_creates_enclosure_when_missing(tmp_path: Path) -> None:
    """Defensive: if a matching item has no <enclosure>, one is created with the new URL."""
    feed_path = tmp_path / "test.rss"
    feed_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<rss version="2.0"><channel>'
        "<title>Test</title>"
        '<item><title>Ep</title><guid isPermaLink="false">g1</guid></item>'
        "</channel></rss>",
        encoding="utf-8",
    )
    publisher = FeedPublisher(tmp_path)
    await publisher.update_episode_url("Test", "g1", "https://local/ep.mp3")

    channel = ET.parse(str(feed_path)).getroot().find("channel")
    assert channel is not None
    item = channel.find("item")
    assert item is not None
    enclosure = item.find("enclosure")
    assert enclosure is not None
    assert enclosure.get("url") == "https://local/ep.mp3"


# ---------------------------------------------------------------------------
# Tests: episode_filename / episode_url helpers
# ---------------------------------------------------------------------------


def test_episode_filename_basic() -> None:
    pub_date = datetime(2026, 3, 21, tzinfo=UTC)
    assert episode_filename(pub_date, "Hello World", "mp3") == "21.03.2026-hello-world.mp3"


def test_episode_filename_slugifies_title() -> None:
    pub_date = datetime(2026, 3, 21, tzinfo=UTC)
    assert episode_filename(pub_date, "The Café & Müller Show!", "mp3") == "21.03.2026-the-cafe-muller-show.mp3"


def test_episode_filename_uses_episode_date() -> None:
    pub_date = datetime(2024, 1, 5, tzinfo=UTC)
    name = episode_filename(pub_date, "Ep", "mp3")
    assert name.startswith("05.01.2024-")


def test_episode_url_includes_feed_slug() -> None:
    pub_date = datetime(2026, 3, 21, tzinfo=UTC)
    url = episode_url("https://podcasts.example.com", "my-feed", pub_date, "My Episode", "mp3")
    assert url == "https://podcasts.example.com/my-feed/21.03.2026-my-episode.mp3"


def test_episode_url_strips_trailing_slash_from_base() -> None:
    pub_date = datetime(2026, 3, 21, tzinfo=UTC)
    url = episode_url("https://podcasts.example.com/", "my-feed", pub_date, "Ep", "mp3")
    assert url == "https://podcasts.example.com/my-feed/21.03.2026-ep.mp3"


# ---------------------------------------------------------------------------
# Tests: PublisherInput new channel fields (Task 1)
# ---------------------------------------------------------------------------


def test_publisher_input_new_channel_fields_default_correctly() -> None:
    """All 10 new PublisherInput channel fields must default to None / False."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Pod",
        episodes=[],
    )
    assert feed.itunes_type is None
    assert feed.itunes_subtitle is None
    assert feed.itunes_summary is None
    assert feed.owner_name is None
    assert feed.owner_email is None
    assert feed.image_title is None
    assert feed.image_link is None
    assert feed.content_encoded is None
    assert feed.itunes_new_feed_url is None
    assert feed.itunes_complete is False


def test_publisher_input_new_channel_fields_accept_values() -> None:
    """New PublisherInput channel fields can be set to non-default values."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Pod",
        episodes=[],
        itunes_type="episodic",
        itunes_subtitle="Short teaser",
        itunes_summary="Long summary.",
        owner_name="Jane Owner",
        owner_email="jane@example.com",
        image_title="Pod Cover",
        image_link="https://example.com",
        content_encoded="<p>HTML</p>",
        itunes_new_feed_url="https://new.example.com/feed.rss",
        itunes_complete=True,
    )
    assert feed.itunes_type == "episodic"
    assert feed.itunes_subtitle == "Short teaser"
    assert feed.itunes_summary == "Long summary."
    assert feed.owner_name == "Jane Owner"
    assert feed.owner_email == "jane@example.com"
    assert feed.image_title == "Pod Cover"
    assert feed.image_link == "https://example.com"
    assert feed.content_encoded == "<p>HTML</p>"
    assert feed.itunes_new_feed_url == "https://new.example.com/feed.rss"
    assert feed.itunes_complete is True
