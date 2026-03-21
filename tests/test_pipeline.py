"""Tests for Pipeline — feed orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.pipeline import Pipeline
from config.config_loader import FeedConfig
from models.feed import Episode, FeedParseInput, ParsedFeed

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


def _patch_db() -> tuple[MagicMock, MagicMock]:
    """Return (mock_db_cls_patch, mock_db) for use with patch()."""
    mock_db = MagicMock()
    mock_db.conn = AsyncMock()
    mock_db_cls = MagicMock()
    mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_db_cls, mock_db


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
    parsed = [MagicMock()]  # simulated list[ParsedFeed]
    config = make_config([feed])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.Database") as mock_db_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[("test", "<rss/>")])
        mock_fp = mock_fp_cls.return_value
        mock_fp.parse_all = MagicMock(return_value=parsed)
        mock_db = MagicMock()
        mock_db.conn = AsyncMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
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
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_store.save_episodes.assert_awaited_once_with("Feed A", [ep])
