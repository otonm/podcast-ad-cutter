"""Tests for EpisodeDownloader."""

from __future__ import annotations

import asyncio as _asyncio
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

import aiohttp as _aiohttp
import pytest
from aioresponses import aioresponses

if TYPE_CHECKING:
    from pathlib import Path

from yarl import URL as _URL

from components.episode_downloader import _DEFAULT_HEADERS, EpisodeDownloader

# A redirect target whose query string contains percent-encoded characters that
# yarl would normalize away if the URL were parsed without encoded=True.
# CloudFront signed URLs have ci=...%3D%3D (base64 padding) and Signature=...~...
# which must reach the server byte-for-byte as issued, or the signature fails.
_REDIRECT_TARGET = (
    "https://stitcher.example.com/audio.mp3"
    "?ci=dGVzdA%3D%3D&Signature=abc~def__&Expires=9999999999"
    "&Key-Pair-Id=KTEST"
)

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


def test_default_headers_contain_user_agent(cache_dir: Path) -> None:
    """Default headers include User-Agent: curl/7.88.1 when no headers arg is supplied."""
    d = EpisodeDownloader(cache_dir=cache_dir)
    assert d._headers == {"User-Agent": "curl/7.88.1"}


def test_custom_headers_replace_default(cache_dir: Path) -> None:
    """A custom headers dict is stored as-is, replacing the default."""
    custom = {"User-Agent": "MyApp/1.0", "X-Custom": "value"}
    d = EpisodeDownloader(cache_dir=cache_dir, headers=custom)
    assert d._headers == custom


async def test_user_agent_passed_to_client_session(cache_dir: Path) -> None:
    """ClientSession is constructed with the default User-Agent header."""
    captured: dict = {}

    original_init = _aiohttp.ClientSession.__init__

    def capturing_init(self, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        original_init(self, *args, **kwargs)  # type: ignore[misc]

    with (
        patch.object(_aiohttp.ClientSession, "__init__", capturing_init),
        aioresponses() as m,
    ):
        m.get(URL, status=200, body=b"x", headers={"Content-Type": "audio/mpeg"})
        await EpisodeDownloader(cache_dir=cache_dir).download(GUID, URL)

    assert captured.get("headers") == _DEFAULT_HEADERS


async def test_custom_headers_passed_to_client_session(cache_dir: Path) -> None:
    """ClientSession receives custom headers when provided to EpisodeDownloader."""
    captured: dict = {}
    custom = {"User-Agent": "CustomBot/2.0"}

    original_init = _aiohttp.ClientSession.__init__

    def capturing_init(self, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        original_init(self, *args, **kwargs)  # type: ignore[misc]

    with (
        patch.object(_aiohttp.ClientSession, "__init__", capturing_init),
        aioresponses() as m,
    ):
        m.get(URL, status=200, body=b"x", headers={"Content-Type": "audio/mpeg"})
        await EpisodeDownloader(cache_dir=cache_dir, headers=custom).download(GUID, URL)

    assert captured.get("headers") == custom


async def test_download_creates_cache_dir(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """download creates cache_dir if it does not exist."""
    assert not cache_dir.exists()  # noqa: ASYNC240
    with aioresponses() as m:
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        await downloader.download(GUID, URL)
    assert cache_dir.is_dir()  # noqa: ASYNC240


async def test_download_returns_path(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """A successful HTTP 200 download returns the cache path and writes the file."""
    with aioresponses() as m:
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        path = await downloader.download(GUID, URL)

    assert path == cache_dir / f"{GUID}.mp3"
    assert path.read_bytes() == AUDIO_DATA


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
        path = await downloader.download(GUID, URL)

    assert path.suffix == f".{expected_ext}"


async def test_unknown_content_type_falls_back_to_mp3(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Unknown MIME types fall back to .mp3 and log a warning."""
    with aioresponses() as m:
        m.get(URL, status=200, body=b"audio", headers={"Content-Type": "application/octet-stream"})
        path = await downloader.download(GUID, URL)

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
        path = await downloader.download(GUID, URL)

    assert path.suffix == ".m4a"


async def test_progress_callback_with_content_length(
    cache_dir: Path,
) -> None:
    """Progress callback receives 0.0 (start), intermediate values, and 1.0 (done)."""
    calls: list[tuple[str, float]] = []

    async def on_progress(guid: str, pct: float) -> None:
        calls.append((guid, pct))

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
        await small_chunk_downloader.download(GUID, URL, on_progress=on_progress)

    guids, percents = zip(*calls, strict=True)
    assert set(guids) == {GUID}
    assert percents[0] == 0.0
    assert percents[-1] == 1.0
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
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        await downloader.download(GUID, URL, on_progress=on_progress)

    percents = [p for _, p in calls]
    assert percents == [0.0, 1.0]


async def test_no_progress_callback_does_not_raise(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Passing on_progress=None works without error."""
    with aioresponses() as m:
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        path = await downloader.download(GUID, URL, on_progress=None)

    assert path.exists()


async def test_retries_on_http_error_then_succeeds(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """5xx responses are retried; succeeds on the final attempt."""
    with aioresponses() as m:
        m.get(URL, status=503)  # attempt 0: fail
        m.get(URL, status=503)  # attempt 1: fail
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})  # attempt 2: ok
        path = await downloader.download(GUID, URL)

    assert path.read_bytes() == AUDIO_DATA


async def test_all_retries_exhausted_raises(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """After max_retries+1 failures download() raises and no partial file remains."""
    with aioresponses() as m:
        m.get(URL, status=503)
        m.get(URL, status=503)
        m.get(URL, status=503)
        with pytest.raises(_aiohttp.ClientError):
            await downloader.download(GUID, URL)

    assert not (cache_dir / f"{GUID}.mp3").exists()


async def test_client_error_triggers_retry(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """aiohttp.ClientError on the first attempt triggers a retry."""
    with aioresponses() as m:
        m.get(URL, exception=_aiohttp.ClientConnectionError("connection refused"))
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        path = await downloader.download(GUID, URL)

    assert path.exists()


async def test_timeout_error_triggers_retry(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """asyncio.TimeoutError on the first attempt triggers a retry."""
    with aioresponses() as m:
        m.get(URL, exception=TimeoutError())
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        path = await downloader.download(GUID, URL)

    assert path.exists()


@pytest.mark.parametrize("status", [400, 403, 404, 410, 422])
async def test_4xx_does_not_retry(
    downloader: EpisodeDownloader,
    cache_dir: Path,
    status: int,
) -> None:
    """4xx responses are permanent — no retries, raises immediately after one attempt."""
    with aioresponses() as m:
        m.get(URL, status=status)
        # Only one mock registered; a retry would cause aioresponses to raise
        # ConnectionError (no more registered responses), exposing any retry.
        with pytest.raises(_aiohttp.ClientResponseError) as exc_info:
            await downloader.download(GUID, URL)

    assert exc_info.value.status == status


async def test_5xx_retries(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """5xx responses are retried; succeeds on the next attempt."""
    with aioresponses() as m:
        m.get(URL, status=503)
        m.get(URL, status=200, body=AUDIO_DATA, headers={"Content-Type": "audio/mpeg"})
        path = await downloader.download(GUID, URL)

    assert path.read_bytes() == AUDIO_DATA


async def test_client_connection_error_exhausts_retries_raises(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """ClientConnectionError (non-HTTP) raises after all retries are exhausted."""
    with aioresponses() as m:
        m.get(URL, exception=_aiohttp.ClientConnectionError("refused"))
        m.get(URL, exception=_aiohttp.ClientConnectionError("refused"))
        m.get(URL, exception=_aiohttp.ClientConnectionError("refused"))
        with pytest.raises(_aiohttp.ClientError):
            await downloader.download(GUID, URL)


async def test_cancelled_error_deletes_partial_file_and_propagates(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """CancelledError propagates out of _download_episode and the partial file is deleted."""

    class _FakeContent:
        async def iter_chunked(self, _size: int):
            yield b"partial data"
            raise _asyncio.CancelledError

    class _FakeResponse:
        status = 200
        content_type = "audio/mpeg"
        content_length = 10_000
        content = _FakeContent()
        request_info = None
        history = ()

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    class _FakeSession:
        def get(self, _url: str, **_kwargs: object) -> _FakeResponse:
            return _FakeResponse()

    cache_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

    with pytest.raises(_asyncio.CancelledError):
        await downloader._download_episode(_FakeSession(), GUID, URL, None)  # type: ignore[arg-type]

    partial = cache_dir / f"{GUID}.mp3"
    assert not partial.exists()


# ── Redirect handling ──────────────────────────────────────────────────────────

async def test_single_redirect_is_followed(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """A 302 redirect is followed and the final response is downloaded."""
    with aioresponses() as m:
        m.get(URL, status=302, headers={"Location": _REDIRECT_TARGET})
        m.get(_URL(_REDIRECT_TARGET, encoded=True), status=200, body=AUDIO_DATA,
              headers={"Content-Type": "audio/mpeg"})
        path = await downloader.download(GUID, URL)

    assert path.read_bytes() == AUDIO_DATA


async def test_redirect_preserves_percent_encoding(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Percent-encoded characters in redirect Location URLs reach the server unchanged.

    yarl normalises URLs by decoding safe characters (e.g. %3D → =).  A
    CloudFront signed URL contains %3D (base64 padding) and ~ in its Signature
    parameter; decoding them invalidates the signature and causes a 403.
    Using URL(location, encoded=True) prevents normalisation.
    """
    with aioresponses() as m:
        m.get(URL, status=302, headers={"Location": _REDIRECT_TARGET})
        # Register with encoded=True so only the exact percent-encoded form matches.
        # If aiohttp decodes %3D to = the lookup will miss and raise ConnectionError.
        m.get(_URL(_REDIRECT_TARGET, encoded=True), status=200, body=AUDIO_DATA,
              headers={"Content-Type": "audio/mpeg"})
        path = await downloader.download(GUID, URL)

    assert path.exists()


async def test_too_many_redirects_raises(
    cache_dir: Path,
) -> None:
    """More than _MAX_REDIRECTS consecutive redirects raises ClientError."""
    d = EpisodeDownloader(cache_dir=cache_dir, max_retries=0)
    redirect_url = "https://r.example.com/hop"

    with aioresponses() as m:
        for _ in range(12):  # well over _MAX_REDIRECTS (10)
            m.get(redirect_url, status=302, headers={"Location": redirect_url})
        with pytest.raises(_aiohttp.ClientError):
            await d.download(GUID, redirect_url)


async def test_redirect_missing_location_raises(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """A 302 response without a Location header raises ClientResponseError."""
    with aioresponses() as m:
        m.get(URL, status=302)  # no Location header
        with pytest.raises(_aiohttp.ClientResponseError):
            await downloader.download(GUID, URL)
