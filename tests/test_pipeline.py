"""Tests for Pipeline — feed orchestration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.pipeline import Pipeline
from config.config_loader import FeedConfig
from models.feed import Episode, FeedParseInput, ParsedFeed, PublisherInput

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_feed(title: str, *, enabled: bool = True) -> FeedConfig:
    return FeedConfig(
        title=title,
        url=f"https://example.com/{title}.rss",
        enabled=enabled,
        episodes_to_keep=10,
    )


def make_config(feeds: list[FeedConfig]) -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = feeds
    return cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_passes_only_enabled_feeds() -> None:
    """Disabled feeds must not be forwarded to the downloader."""
    enabled = make_feed("enabled")
    disabled = make_feed("disabled", enabled=False)
    config = make_config([enabled, disabled])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("enabled", "<xml/>")])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([("enabled", enabled.url)])


async def test_run_preserves_config_order() -> None:
    """Enabled feeds must be forwarded in the order they appear in config."""
    feed_a, feed_b, feed_c = make_feed("a"), make_feed("b"), make_feed("c")
    config = make_config([feed_a, feed_b, feed_c])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.FeedParser"),
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with(
        [("a", feed_a.url), ("b", feed_b.url), ("c", feed_c.url)]
    )


async def test_run_returns_parser_result() -> None:
    """Pipeline.run() returns what FeedParser.parse_all() returns."""
    feed = make_feed("test")
    parsed = [ParsedFeed(config_title="test", feed_url=feed.url, title="test")]
    config = make_config([feed])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.FeedPublisher") as mock_publisher_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("test", "<rss/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.get_episodes_for_feed = AsyncMock(return_value=[])
        mock_store_cls.return_value = mock_store
        mock_publisher_cls.return_value = AsyncMock()
        pipeline = Pipeline(config)
        result = await pipeline.run()

    assert result == parsed


async def test_run_calls_feed_parser() -> None:
    """Pipeline.run() passes FeedParseInput objects to FeedParser.parse_all."""
    feed = make_feed("test")
    config = make_config([feed])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("test", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=[])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        await pipeline.run()

    expected_input = FeedParseInput(
        config_title="test",
        feed_url=feed.url,
        episodes_to_keep=feed.episodes_to_keep,
        xml_text="<xml/>",
    )
    mock_fp.parse_all.assert_called_once_with([expected_input])


async def test_run_with_no_enabled_feeds() -> None:
    """When all feeds are disabled the downloader is called with an empty list."""
    disabled = make_feed("disabled", enabled=False)
    config = make_config([disabled])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config)
        result = await pipeline.run()

    mock_dl.download_all.assert_called_once_with([])
    assert result == []


async def test_run_with_feed_name_forces_disabled_feed() -> None:
    """--feed must process a disabled feed, ignoring enabled=False."""
    disabled = make_feed("target", enabled=False)
    other = make_feed("other", enabled=True)
    config = make_config([disabled, other])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("target", "<xml/>")])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([("target", disabled.url)])


async def test_run_with_feed_name_excludes_other_feeds() -> None:
    """--feed must pass only the named feed, even when others are enabled."""
    target = make_feed("target", enabled=True)
    other = make_feed("other", enabled=True)
    config = make_config([target, other])

    with (
        patch("components.pipeline.FeedDownloader") as mock_downloader_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("target", "<xml/>")])
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([("target", target.url)])


async def test_run_with_unknown_feed_name_raises() -> None:
    """--feed with a title that matches no feed must raise ValueError."""
    feed = make_feed("existing")
    config = make_config([feed])

    with patch("components.pipeline.FeedDownloader"):
        pipeline = Pipeline(config, feed_name="nonexistent")
        with pytest.raises(ValueError, match="nonexistent"):
            await pipeline.run()


async def test_run_calls_feed_publisher() -> None:
    """Pipeline.run() must call FeedPublisher.publish() once per parsed feed."""
    feed = make_feed("My Podcast")
    config = make_config([feed])
    config.app.base_url = "https://podcasts.example.com"
    config.app.paths.output_dir = Path("/output")
    ep = Episode(guid="g1", url="http://x.com/ep.mp3", title="Ep 1")
    parsed = [ParsedFeed(config_title="My Podcast", feed_url="http://x.com/feed", title="My Podcast", episodes=[ep])]

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.FeedPublisher") as mock_publisher_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("My Podcast", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.get_episodes_for_feed = AsyncMock(return_value=[])
        mock_store_cls.return_value = mock_store
        mock_publisher = AsyncMock()
        mock_publisher_cls.return_value = mock_publisher
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_publisher.publish.assert_awaited_once()
    publisher_input: PublisherInput = mock_publisher.publish.call_args[0][0]
    assert publisher_input.base_url == "https://podcasts.example.com"
    assert publisher_input.title == "My Podcast"


async def test_run_passes_new_channel_fields_to_publisher() -> None:
    """The 10 new channel-level fields on ParsedFeed must reach PublisherInput unchanged."""
    feed = make_feed("My Podcast")
    config = make_config([feed])
    config.app.base_url = "https://podcasts.example.com"
    config.app.paths.output_dir = Path("/output")

    # Build a ParsedFeed with all 10 new channel fields populated.
    parsed = [
        ParsedFeed(
            config_title="My Podcast",
            feed_url="http://x.com/feed",
            title="My Podcast",
            itunes_type="episodic",
            itunes_subtitle="A short subtitle",
            itunes_summary="A long summary",
            owner_name="Test Owner",
            owner_email="owner@test.com",
            image_title="My Podcast Image",
            image_link="https://example.com",
            content_encoded="<p>Encoded content</p>",
            itunes_new_feed_url="https://new.example.com/feed",
            itunes_complete=True,
        )
    ]

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.FeedPublisher") as mock_publisher_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("My Podcast", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store.get_episodes_for_feed = AsyncMock(return_value=[])
        mock_store_cls.return_value = mock_store
        mock_publisher = AsyncMock()
        mock_publisher_cls.return_value = mock_publisher
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_publisher.publish.assert_awaited_once()
    pi: PublisherInput = mock_publisher.publish.call_args[0][0]
    assert pi.itunes_type == "episodic"
    assert pi.itunes_subtitle == "A short subtitle"
    assert pi.itunes_summary == "A long summary"
    assert pi.owner_name == "Test Owner"
    assert pi.owner_email == "owner@test.com"
    assert pi.image_title == "My Podcast Image"
    assert pi.image_link == "https://example.com"
    assert pi.content_encoded == "<p>Encoded content</p>"
    assert pi.itunes_new_feed_url == "https://new.example.com/feed"
    assert pi.itunes_complete is True


async def test_run_saves_parsed_episodes() -> None:
    """Pipeline must call save_episodes once per successfully parsed feed."""
    feed = make_feed("Feed A")
    config = make_config([feed])
    ep = Episode(guid="g1", url="http://x.com/ep.mp3", title="Ep 1")
    parsed = [ParsedFeed(config_title="Feed A", feed_url="http://x.com/feed", title="Feed A", episodes=[ep])]

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.EpisodeDownloader") as mock_ep_dl_cls,
        patch("components.pipeline.FeedPublisher") as mock_pub_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("Feed A", "<xml/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store = AsyncMock()
        mock_store_cls.return_value = mock_store
        mock_ep_dl_cls.return_value.download_all = AsyncMock(return_value=[])
        mock_pub_cls.return_value.publish = AsyncMock(return_value=MagicMock())
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_store.save_episodes.assert_awaited_once_with("Feed A", [ep])


async def test_pipeline_calls_episode_downloader() -> None:
    """Pipeline calls EpisodeDownloader.download_all once per feed after publishing."""
    feed_cfg = FeedConfig(title="My Podcast", url="http://x.com/feed", enabled=True, episodes_to_keep=5)
    config = MagicMock()
    config.app.feeds = [feed_cfg]
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.base_url = "http://localhost"

    ep = Episode(
        guid="ep-001",
        url="https://example.com/ep.mp3",
        title="Ep 1",
        pub_date=datetime(2026, 3, 22, tzinfo=UTC),
    )
    parsed = ParsedFeed(
        config_title="My Podcast",
        feed_url="http://x.com/feed",
        title="My Podcast",
        episodes=[ep],
    )

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.FeedPublisher") as mock_pub_cls,
        patch("components.pipeline.EpisodeDownloader") as mock_ep_dl_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
    ):
        mock_dl_cls.return_value.download_all = AsyncMock(return_value=[("My Podcast", "<rss/>")])
        mock_fp_cls.return_value.parse_all.return_value = [parsed]
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store_cls.return_value.save_episodes = AsyncMock()
        mock_store_cls.return_value.get_episodes_for_feed = AsyncMock(return_value=[ep])
        mock_pub_cls.return_value.publish = AsyncMock(return_value=Path("/output/my-podcast.rss"))
        mock_ep_dl = mock_ep_dl_cls.return_value
        mock_ep_dl.download_all = AsyncMock(return_value=[])

        pipeline = Pipeline(config)
        await pipeline.run()

    # EpisodeDownloader.download_all must be called once with (guid, url) pairs and a progress callback
    mock_ep_dl.download_all.assert_awaited_once()
    call_args = mock_ep_dl.download_all.call_args
    assert call_args[0][0] == [("ep-001", "https://example.com/ep.mp3")]
    assert call_args[1]["on_progress"] is not None


# ---------------------------------------------------------------------------
# _on_download_progress branch coverage
# ---------------------------------------------------------------------------


async def test_on_download_progress_starting(caplog: pytest.LogCaptureFixture) -> None:
    """Progress callback at 0.0 logs a 'Downloading' message."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
    ):
        pipeline = Pipeline(config)

    with caplog.at_level(logging.DEBUG, logger="components.pipeline"):
        await pipeline._on_download_progress("ep-001", 0.0)

    assert "Downloading episode 'ep-001'" in caplog.text


async def test_on_download_progress_complete(caplog: pytest.LogCaptureFixture) -> None:
    """Progress callback at 1.0 logs a 'downloaded' message."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
    ):
        pipeline = Pipeline(config)

    with caplog.at_level(logging.DEBUG, logger="components.pipeline"):
        await pipeline._on_download_progress("ep-001", 1.0)

    assert "Episode 'ep-001' downloaded." in caplog.text


async def test_on_download_progress_intermediate() -> None:
    """Progress callback at an intermediate value writes percentage in-place to stderr."""
    config = MagicMock()
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
    ):
        pipeline = Pipeline(config)

    with patch("sys.stderr") as mock_stderr:
        await pipeline._on_download_progress("ep-001", 0.5)

    mock_stderr.write.assert_called_once_with("\r  Episode 'ep-001': 50%")
    mock_stderr.flush.assert_called_once()
