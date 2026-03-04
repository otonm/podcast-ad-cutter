from datetime import UTC, datetime
from pathlib import Path

import pytest

from models.episode import Episode


def test_feed_slug_basic() -> None:
    from pipeline.runner import _feed_slug

    assert _feed_slug("My Favourite Podcast") == "my-favourite-podcast"


def test_feed_slug_unicode() -> None:
    from pipeline.runner import _feed_slug

    # python-slugify transliterates accented chars (é→e) and drops symbols like & and —
    assert _feed_slug("Café & Music — Vol. 3") == "cafe-music-vol-3"


def test_feed_slug_truncates() -> None:
    from pipeline.runner import _feed_slug

    long_title = "A" * 200
    result = _feed_slug(long_title)
    assert len(result) <= 80


def test_episode_filename_format() -> None:
    from pipeline.runner import _episode_filename

    ep = Episode(
        guid="test-guid",
        feed_title="My Podcast",
        title="Hello World! #42",
        audio_url="https://example.com/ep.mp3",  # type: ignore[arg-type]
        published=datetime(2025, 3, 15, tzinfo=UTC),
    )
    assert _episode_filename(ep, "mp3") == "15.03.2025-hello-world-42.mp3"


@pytest.mark.asyncio
async def test_process_feed_calls_publisher_when_base_url_set(tmp_path: Path) -> None:
    """process_feed invokes generate_feed_rss when publishing.base_url is configured."""
    from unittest.mock import AsyncMock, patch

    from config.config_loader import (
        AdDetectionConfig,
        AppConfig,
        AudioConfig,
        FeedConfig,
        InterpretationConfig,
        LoggingConfig,
        PathsConfig,
        PublishingConfig,
        RetryConfig,
        TranscriptionConfig,
    )
    from pipeline.runner import process_feed

    feed_cfg = FeedConfig(name="Test Podcast", url="https://feeds.example.com/test.rss")
    cfg = AppConfig(
        feeds=[feed_cfg],
        paths=PathsConfig(output_dir=tmp_path, database=tmp_path / "test.db"),
        transcription=TranscriptionConfig(provider="openai", model="whisper-1"),
        interpretation=InterpretationConfig(provider="openai", model="gpt-4o"),
        ad_detection=AdDetectionConfig(),
        audio=AudioConfig(),
        logging=LoggingConfig(),
        retry=RetryConfig(),
        publishing=PublishingConfig(base_url="https://example.com"),
    )

    mock_generate = AsyncMock()
    with (
        patch("pipeline.runner.fetch_episodes", return_value=[]),
        patch("pipeline.runner.generate_feed_rss", mock_generate),
    ):
        await process_feed(feed_cfg, cfg)

    mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_feed_skips_publisher_when_no_base_url(tmp_path: Path) -> None:
    """process_feed does NOT invoke generate_feed_rss when base_url is None."""
    from unittest.mock import AsyncMock, patch

    from config.config_loader import (
        AdDetectionConfig,
        AppConfig,
        AudioConfig,
        FeedConfig,
        InterpretationConfig,
        LoggingConfig,
        PathsConfig,
        PublishingConfig,
        RetryConfig,
        TranscriptionConfig,
    )
    from pipeline.runner import process_feed

    feed_cfg = FeedConfig(name="Test Podcast", url="https://feeds.example.com/test.rss")
    cfg = AppConfig(
        feeds=[feed_cfg],
        paths=PathsConfig(output_dir=tmp_path, database=tmp_path / "test.db"),
        transcription=TranscriptionConfig(provider="openai", model="whisper-1"),
        interpretation=InterpretationConfig(provider="openai", model="gpt-4o"),
        ad_detection=AdDetectionConfig(),
        audio=AudioConfig(),
        logging=LoggingConfig(),
        retry=RetryConfig(),
        publishing=PublishingConfig(base_url=None),
    )

    mock_generate = AsyncMock()
    with (
        patch("pipeline.runner.fetch_episodes", return_value=[]),
        patch("pipeline.runner.generate_feed_rss", mock_generate),
    ):
        await process_feed(feed_cfg, cfg)

    mock_generate.assert_not_awaited()
