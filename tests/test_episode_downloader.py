"""Tests for EpisodeDownloader."""

from __future__ import annotations

import asyncio as _asyncio  # noqa: F401 — used in Task 5 tests
from typing import TYPE_CHECKING

import aiohttp as _aiohttp  # noqa: F401 — used in Task 4 tests
import pytest
from aioresponses import aioresponses

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


# ---------------------------------------------------------------------------
# Task 2: Successful download — streaming and extension mapping
# ---------------------------------------------------------------------------


async def test_successful_download_returns_path(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """A successful HTTP 200 download returns (guid, path) and writes the file."""
    with aioresponses() as m:
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        results = await downloader.download_all([(GUID, URL)])

    assert len(results) == 1
    guid, path = results[0]
    assert guid == GUID
    assert path == cache_dir / f"{GUID}.mp3"
    assert path.read_bytes() == AUDIO_DATA


async def test_successful_download_preserves_order(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Results are returned in the same order as the input list."""
    guid_a, url_a = "ep-001", "https://example.com/001.mp3"
    guid_b, url_b = "ep-002", "https://example.com/002.mp3"
    with aioresponses() as m:
        m.get(url_a, status=200, body=b"audio-a", headers={"Content-Type": "audio/mpeg"})
        m.get(url_b, status=200, body=b"audio-b", headers={"Content-Type": "audio/mpeg"})
        results = await downloader.download_all([(guid_a, url_a), (guid_b, url_b)])

    assert [g for g, _ in results] == [guid_a, guid_b]


@pytest.mark.parametrize(
    ("content_type", "expected_ext"),
    [
        ("audio/mpeg", "mp3"),
        ("audio/mp4", "m4a"),
        ("audio/x-m4a", "m4a"),
        ("audio/ogg", "ogg"),
        ("audio/opus", "opus"),
        ("audio/flac", "flac"),
        ("audio/wav", "wav"),
    ],
)
async def test_extension_from_content_type(
    downloader: EpisodeDownloader,
    cache_dir: Path,
    content_type: str,
    expected_ext: str,
) -> None:
    """Extension is derived from the Content-Type MIME type."""
    with aioresponses() as m:
        m.get(URL, status=200, body=b"audio", headers={"Content-Type": content_type})
        results = await downloader.download_all([(GUID, URL)])

    _, path = results[0]
    assert path.suffix == f".{expected_ext}"


async def test_unknown_content_type_falls_back_to_mp3(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Unknown MIME types fall back to .mp3 and log a warning."""
    with aioresponses() as m:
        m.get(URL, status=200, body=b"audio", headers={"Content-Type": "application/octet-stream"})
        results = await downloader.download_all([(GUID, URL)])

    _, path = results[0]
    assert path.suffix == ".mp3"


async def test_parameterised_content_type_stripped_correctly(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """aiohttp's response.content_type strips codec params before the MIME lookup.

    e.g. "audio/mp4; codecs=mp4a.40.2" -> content_type="audio/mp4" -> ext="m4a".
    This verifies the plan uses response.content_type (parsed) not the raw header.
    """
    with aioresponses() as m:
        m.get(
            URL,
            status=200,
            body=b"audio",
            headers={"Content-Type": "audio/mp4; codecs=mp4a.40.2"},
        )
        results = await downloader.download_all([(GUID, URL)])

    _, path = results[0]
    assert path.suffix == ".m4a"
