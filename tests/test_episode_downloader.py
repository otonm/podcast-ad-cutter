"""Tests for EpisodeDownloader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiohttp as _aiohttp
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


# ---------------------------------------------------------------------------
# Task 3: Progress callback
# ---------------------------------------------------------------------------


async def test_progress_callback_with_content_length(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Progress callback receives 0.0 (start), intermediate values, and 1.0 (done)."""
    calls: list[tuple[str, float]] = []

    async def on_progress(guid: str, pct: float) -> None:
        calls.append((guid, pct))

    # Use chunk_size smaller than data to force multiple intermediate calls.
    small_chunk_downloader = EpisodeDownloader(
        cache_dir=cache_dir,
        max_retries=0,
        retry_delay=0.0,
        chunk_size=64,  # force many chunks
    )
    data = b"x" * 256  # 256 bytes — produces 4 chunks of 64
    with aioresponses() as m:
        m.get(
            URL,
            status=200,
            body=data,
            headers={"Content-Type": "audio/mpeg", "Content-Length": str(len(data))},
        )
        await small_chunk_downloader.download_all([(GUID, URL)], on_progress=on_progress)

    guids, percents = zip(*calls, strict=True)
    assert set(guids) == {GUID}
    assert percents[0] == 0.0   # start sentinel
    assert percents[-1] == 1.0  # completion
    # Intermediate values strictly between 0 and 1
    intermediate = [p for p in percents if 0.0 < p < 1.0]
    assert len(intermediate) > 0
    assert all(0.0 < p < 1.0 for p in intermediate)


async def test_progress_callback_without_content_length(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Without Content-Length only 0.0 (start) and 1.0 (end) are emitted."""
    calls: list[tuple[str, float]] = []

    async def on_progress(guid: str, pct: float) -> None:
        calls.append((guid, pct))

    with aioresponses() as m:
        # Omit Content-Length header entirely
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        await downloader.download_all([(GUID, URL)], on_progress=on_progress)

    percents = [p for _, p in calls]
    assert percents == [0.0, 1.0]


async def test_no_progress_callback_does_not_raise(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Passing on_progress=None works without error."""
    with aioresponses() as m:
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        results = await downloader.download_all([(GUID, URL)], on_progress=None)

    assert len(results) == 1


# ---------------------------------------------------------------------------
# Task 4: Retry with exponential backoff
# ---------------------------------------------------------------------------


async def test_retries_on_http_error_then_succeeds(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Non-200 responses are retried; succeeds on the final attempt."""
    with aioresponses() as m:
        m.get(URL, status=503)  # attempt 0: fail
        m.get(URL, status=503)  # attempt 1: fail
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})  # attempt 2: ok
        results = await downloader.download_all([(GUID, URL)])

    assert len(results) == 1


async def test_all_retries_exhausted_episode_omitted(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """After max_retries+1 failures the episode is omitted and no file remains."""
    with aioresponses() as m:
        # max_retries=2 -> 3 total attempts, all fail
        m.get(URL, status=503)
        m.get(URL, status=503)
        m.get(URL, status=503)
        results = await downloader.download_all([(GUID, URL)])

    assert results == []
    # No partial file should remain in cache
    assert list(cache_dir.iterdir()) == []  # noqa: ASYNC240


async def test_client_error_triggers_retry(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """aiohttp.ClientError on the first attempt triggers a retry."""
    with aioresponses() as m:
        m.get(URL, exception=_aiohttp.ClientConnectionError("connection refused"))
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        results = await downloader.download_all([(GUID, URL)])

    assert len(results) == 1


async def test_timeout_error_triggers_retry(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """asyncio.TimeoutError on the first attempt triggers a retry."""
    with aioresponses() as m:
        m.get(URL, exception=TimeoutError())
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        results = await downloader.download_all([(GUID, URL)])

    assert len(results) == 1


async def test_multiple_episodes_one_fails(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Failed episode is skipped; successful ones are returned in input order."""
    guid_a, url_a = "ep-ok", "https://example.com/ok.mp3"
    guid_b, url_b = "ep-fail", "https://example.com/fail.mp3"

    with aioresponses() as m:
        m.get(url_a, status=200, body=b"audio-a", headers={"Content-Type": "audio/mpeg"})
        # ep-fail exhausts all retries
        m.get(url_b, status=500)
        m.get(url_b, status=500)
        m.get(url_b, status=500)
        results = await downloader.download_all(
            [(guid_a, url_a), (guid_b, url_b)]
        )

    assert [g for g, _ in results] == [guid_a]
