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
_CONTENT = "http://purl.org/rss/1.0/modules/content/"


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
            episode_type="full",
            itunes_author="Test Author",
            itunes_subtitle="Episode subtitle",
            itunes_summary="Episode summary",
            content_encoded="<p>Episode HTML</p>",
            link="https://example.com/ep1",
            author="author@example.com (Test Author)",
            itunes_title="iTunes Title",
            episode_number=1,
            season_number=2,
            itunes_block=False,
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
        itunes_type="episodic",
        itunes_subtitle="A test subtitle",
        itunes_summary="A test summary",
        owner_name="Test Owner",
        owner_email="owner@example.com",
        image_title="My Podcast",
        image_link="https://mypodcast.com",
        content_encoded="<p>HTML content</p>",
        itunes_new_feed_url="https://new.example.com/feed.rss",
        itunes_complete=False,
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
    # feed_input has categories=["Technology", "Science"]; Technology is parent, Science is child.
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    parent_cat = channel.find(f"{{{_ITUNES}}}category")
    assert parent_cat is not None
    assert parent_cat.get("text") == "Technology"
    child = parent_cat.find(f"{{{_ITUNES}}}category")
    assert child is not None
    assert child.get("text") == "Science"


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


# ---------------------------------------------------------------------------
# Tests: itunes:category correctness (Task 3)
# ---------------------------------------------------------------------------


async def test_category_uses_text_attribute(tmp_path: Path) -> None:
    """Category elements must use text= attribute, not label=."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Cat Feed",
        episodes=[],
        categories=["Business"],
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    cat = channel.find(f"{{{_ITUNES}}}category")
    assert cat is not None
    assert cat.get("text") == "Business"
    assert cat.get("label") is None  # must NOT use label=


async def test_category_hierarchy_multiple(tmp_path: Path) -> None:
    """First category is parent; subsequent categories are nested children."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Cat Feed",
        episodes=[],
        categories=["Business", "Investing", "Entrepreneurship"],
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None

    # Only one top-level itunes:category element (the parent)
    top_cats = channel.findall(f"{{{_ITUNES}}}category")
    assert len(top_cats) == 1
    parent = top_cats[0]
    assert parent.get("text") == "Business"

    # Children are nested inside the parent
    children = parent.findall(f"{{{_ITUNES}}}category")
    assert len(children) == 2
    assert children[0].get("text") == "Investing"
    assert children[1].get("text") == "Entrepreneurship"


async def test_category_hierarchy_single(tmp_path: Path) -> None:
    """Single category has no nested children."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Cat Feed",
        episodes=[],
        categories=["Technology"],
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None

    top_cats = channel.findall(f"{{{_ITUNES}}}category")
    assert len(top_cats) == 1
    parent = top_cats[0]
    assert parent.get("text") == "Technology"
    assert parent.findall(f"{{{_ITUNES}}}category") == []


async def test_category_empty_list_writes_nothing(tmp_path: Path) -> None:
    """Empty categories list must produce no itunes:category elements."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Cat Feed",
        episodes=[],
        categories=[],
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findall(f"{{{_ITUNES}}}category") == []


# ---------------------------------------------------------------------------
# Tests: new channel fields in output XML (Task 3)
# ---------------------------------------------------------------------------


async def test_image_block_written_with_url_title_link(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<image> block must contain <url>, <title>, and <link> sub-elements."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    img = channel.find("image")
    assert img is not None
    assert img.findtext("url") == "https://mypodcast.com/cover.jpg"
    assert img.findtext("title") == "My Podcast"
    assert img.findtext("link") == "https://mypodcast.com"


async def test_image_block_url_only_when_title_link_absent(tmp_path: Path) -> None:
    """<image> block with only image_url set must only contain <url>, no <title> or <link>."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Pod",
        episodes=[],
        image_url="https://example.com/art.jpg",
        image_title=None,
        image_link=None,
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    img = channel.find("image")
    assert img is not None
    assert img.findtext("url") == "https://example.com/art.jpg"
    assert img.find("title") is None
    assert img.find("link") is None


async def test_itunes_type_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<itunes:type> must appear in channel when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{_ITUNES}}}type") == "episodic"


async def test_itunes_subtitle_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<itunes:subtitle> must appear in channel when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{_ITUNES}}}subtitle") == "A test subtitle"


async def test_itunes_summary_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<itunes:summary> must appear in channel when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{_ITUNES}}}summary") == "A test summary"


async def test_owner_block_written_with_name_and_email(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<itunes:owner> must contain both <itunes:name> and <itunes:email> when both are set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    owner = channel.find(f"{{{_ITUNES}}}owner")
    assert owner is not None
    assert owner.findtext(f"{{{_ITUNES}}}name") == "Test Owner"
    assert owner.findtext(f"{{{_ITUNES}}}email") == "owner@example.com"


async def test_owner_block_name_only(tmp_path: Path) -> None:
    """<itunes:owner> with only owner_name set must omit <itunes:email>."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Pod",
        episodes=[],
        owner_name="Only Name",
        owner_email=None,
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    owner = channel.find(f"{{{_ITUNES}}}owner")
    assert owner is not None
    assert owner.findtext(f"{{{_ITUNES}}}name") == "Only Name"
    assert owner.find(f"{{{_ITUNES}}}email") is None


async def test_owner_block_email_only(tmp_path: Path) -> None:
    """<itunes:owner> with only owner_email set must omit <itunes:name>."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Pod",
        episodes=[],
        owner_name=None,
        owner_email="only@example.com",
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    owner = channel.find(f"{{{_ITUNES}}}owner")
    assert owner is not None
    assert owner.find(f"{{{_ITUNES}}}name") is None
    assert owner.findtext(f"{{{_ITUNES}}}email") == "only@example.com"


async def test_owner_block_not_written_when_both_absent(tmp_path: Path) -> None:
    """<itunes:owner> must be absent when both owner_name and owner_email are None."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Pod",
        episodes=[],
        owner_name=None,
        owner_email=None,
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.find(f"{{{_ITUNES}}}owner") is None


async def test_content_encoded_channel_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<content:encoded> must appear in channel when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{_CONTENT}}}encoded") == "<p>HTML content</p>"


async def test_itunes_new_feed_url_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<itunes:new-feed-url> must appear in channel when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{_ITUNES}}}new-feed-url") == "https://new.example.com/feed.rss"


async def test_itunes_complete_written_when_true(tmp_path: Path) -> None:
    """<itunes:complete>yes</itunes:complete> must appear when itunes_complete is True."""
    feed = PublisherInput(
        base_url="https://x.com",
        title="Pod",
        episodes=[],
        itunes_complete=True,
    )
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.findtext(f"{{{_ITUNES}}}complete") == "yes"


async def test_itunes_complete_not_written_when_false(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<itunes:complete> must be absent when itunes_complete is False."""
    # feed_input has itunes_complete=False
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.find(f"{{{_ITUNES}}}complete") is None


# ---------------------------------------------------------------------------
# Tests: new episode fields in output XML (Task 3)
# ---------------------------------------------------------------------------


async def test_episode_link_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <link> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext("link") == "https://example.com/ep1"


async def test_episode_author_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <author> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext("author") == "author@example.com (Test Author)"


async def test_episode_itunes_title_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <itunes:title> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}title") == "iTunes Title"


async def test_episode_type_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <itunes:episodeType> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}episodeType") == "full"


async def test_episode_itunes_author_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <itunes:author> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}author") == "Test Author"


async def test_episode_number_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <itunes:episode> must appear when episode_number is set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}episode") == "1"


async def test_episode_season_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <itunes:season> must appear when season_number is set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}season") == "2"


async def test_episode_itunes_subtitle_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <itunes:subtitle> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}subtitle") == "Episode subtitle"


async def test_episode_itunes_summary_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <itunes:summary> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}summary") == "Episode summary"


async def test_episode_itunes_block_written_when_true(tmp_path: Path, pub_date: datetime) -> None:
    """<itunes:block>yes</itunes:block> must appear when itunes_block is True."""
    ep = Episode(
        guid="guid-blocked",
        url="https://origin.com/ep.mp3",
        title="Blocked Episode",
        pub_date=pub_date,
        itunes_block=True,
    )
    feed = PublisherInput(base_url="https://x.com", title="Pod", episodes=[ep])
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}block") == "yes"


async def test_episode_itunes_block_not_written_when_false(tmp_path: Path, feed_input: PublisherInput) -> None:
    """<itunes:block> must be absent when itunes_block is False."""
    # episodes[0] in feed_input has itunes_block=False
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.find(f"{{{_ITUNES}}}block") is None


async def test_episode_content_encoded_written(tmp_path: Path, feed_input: PublisherInput) -> None:
    """Episode <content:encoded> must appear when set."""
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed_input)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_CONTENT}}}encoded") == "<p>Episode HTML</p>"


async def test_absent_new_episode_fields_not_written(tmp_path: Path, pub_date: datetime) -> None:
    """A minimal Episode with no new Task-3 fields must not emit any of those tags."""
    ep = Episode(
        guid="guid-minimal",
        url="https://origin.com/ep.mp3",
        title="Minimal Episode",
        pub_date=pub_date,
    )
    feed = PublisherInput(base_url="https://x.com", title="Pod", episodes=[ep])
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]

    # None of these should appear
    assert item.find("link") is None
    assert item.find("author") is None
    assert item.find(f"{{{_ITUNES}}}title") is None
    assert item.find(f"{{{_ITUNES}}}episodeType") is None
    assert item.find(f"{{{_ITUNES}}}author") is None
    assert item.find(f"{{{_ITUNES}}}episode") is None
    assert item.find(f"{{{_ITUNES}}}season") is None
    assert item.find(f"{{{_ITUNES}}}subtitle") is None
    assert item.find(f"{{{_ITUNES}}}summary") is None
    assert item.find(f"{{{_ITUNES}}}block") is None
    assert item.find(f"{{{_CONTENT}}}encoded") is None


async def test_episode_number_zero_is_written(tmp_path: Path, pub_date: datetime) -> None:
    """episode_number=0 must be written — zero is a valid episode number."""
    ep = Episode(
        guid="guid-zero",
        url="https://origin.com/ep.mp3",
        title="Episode Zero",
        pub_date=pub_date,
        episode_number=0,
    )
    feed = PublisherInput(base_url="https://x.com", title="Pod", episodes=[ep])
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    item = channel.findall("item")[0]
    assert item.findtext(f"{{{_ITUNES}}}episode") == "0"


async def test_image_block_absent_when_image_url_none(tmp_path: Path) -> None:
    """Standard RSS <image> block must not appear when image_url is None."""
    feed = PublisherInput(base_url="https://x.com", title="Pod", episodes=[])
    publisher = FeedPublisher(tmp_path)
    path = await publisher.publish(feed)
    channel = ET.parse(str(path)).getroot().find("channel")
    assert channel is not None
    assert channel.find("image") is None
