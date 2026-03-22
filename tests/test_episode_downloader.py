"""Tests for EpisodeDownloader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from components.episode_downloader import EpisodeDownloader

GUID = "episode-abc123"
URL = "https://example.com/episode.mp3"
AUDIO_DATA = b"fake audio bytes" * 512  # 8 KB of fake audio


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Return a cache directory path that does NOT yet exist."""
    return tmp_path / "cache"


@pytest.fixture
def downloader(cache_dir: Path) -> EpisodeDownloader:
    """EpisodeDownloader with zero retry delay for fast tests."""
    return EpisodeDownloader(cache_dir=cache_dir, max_retries=2, retry_delay=0.0)


async def test_download_all_creates_cache_dir(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """download_all creates cache_dir even when the episode list is empty."""
    assert not cache_dir.exists()  # noqa: ASYNC240
    await downloader.download_all([])
    assert cache_dir.is_dir()  # noqa: ASYNC240
