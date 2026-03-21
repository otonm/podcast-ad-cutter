"""Tests for Pipeline — feed orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.pipeline import Pipeline
from config.config_loader import FeedConfig

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

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[(enabled, "<xml/>")])
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([enabled])


async def test_run_preserves_config_order() -> None:
    """Enabled feeds must be forwarded in the order they appear in config."""
    feed_a, feed_b, feed_c = make_feed("a"), make_feed("b"), make_feed("c")
    config = make_config([feed_a, feed_b, feed_c])

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[])
        pipeline = Pipeline(config)
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([feed_a, feed_b, feed_c])


async def test_run_returns_downloader_result() -> None:
    """Pipeline.run() must return exactly what download_all returns."""
    feed = make_feed("test")
    expected = [(feed, "<rss/>")]
    config = make_config([feed])

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=expected)
        pipeline = Pipeline(config)
        result = await pipeline.run()

    assert result == expected


async def test_run_with_no_enabled_feeds() -> None:
    """When all feeds are disabled the downloader is called with an empty list."""
    disabled = make_feed("disabled", enabled=False)
    config = make_config([disabled])

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[])
        pipeline = Pipeline(config)
        result = await pipeline.run()

    mock_dl.download_all.assert_called_once_with([])
    assert result == []


async def test_run_with_feed_name_forces_disabled_feed() -> None:
    """--feed must process a disabled feed, ignoring enabled=False."""
    disabled = make_feed("target", enabled=False)
    other = make_feed("other", enabled=True)
    config = make_config([disabled, other])

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[(disabled, "<xml/>")])
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([disabled])


async def test_run_with_feed_name_excludes_other_feeds() -> None:
    """--feed must pass only the named feed, even when others are enabled."""
    target = make_feed("target", enabled=True)
    other = make_feed("other", enabled=True)
    config = make_config([target, other])

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[(target, "<xml/>")])
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([target])


async def test_run_with_unknown_feed_name_raises() -> None:
    """--feed with a title that matches no feed must raise ValueError."""
    feed = make_feed("existing")
    config = make_config([feed])

    with patch("components.pipeline.FeedDownloader"):
        pipeline = Pipeline(config, feed_name="nonexistent")
        with pytest.raises(ValueError, match="nonexistent"):
            await pipeline.run()
