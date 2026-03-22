# EpisodeDownloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `EpisodeDownloader` — a decoupled component that streams episode audio from enclosure URLs to `cache/{guid}.{ext}`, with retry/backoff, real-time async progress callbacks, and guaranteed partial-file cleanup on failure or cancellation.

**Architecture:** `EpisodeDownloader` mirrors `FeedDownloader` exactly: one public `download_all()`, one private `_download_one()`, a single `aiohttp.ClientSession` per batch, no config imports. The file extension is derived from `response.content_type` (aiohttp's parsed attribute, strips codec params). Retries use exponential backoff; a `try/finally` guard deletes partial files on any error including `CancelledError`. Pipeline constructs one instance and calls `download_all` per feed, inside the existing for-loop, after `FeedPublisher.publish()`.

**Tech Stack:** `aiohttp` (already present), `asyncio` stdlib, `pathlib`, `aioresponses` (dev dep, already present).

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `components/episode_downloader.py` | `EpisodeDownloader` class |
| Create | `tests/test_episode_downloader.py` | All tests for the new class |
| Modify | `components/pipeline.py` | Instantiate downloader; call after publish |
| Modify | `tests/test_pipeline.py` | Mock `EpisodeDownloader` in pipeline tests |

---

## Task 1: Skeleton — constructor + `cache_dir` creation

**Files:**
- Create: `components/episode_downloader.py`
- Create: `tests/test_episode_downloader.py`

- [ ] **Step 1: Write the failing test**

`tests/test_episode_downloader.py`:

```python
"""Tests for EpisodeDownloader."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    assert not cache_dir.exists()
    await downloader.download_all([])
    assert cache_dir.is_dir()
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/oton/Downloads/podcast-ad-cutter && uv run pytest tests/test_episode_downloader.py::test_download_all_creates_cache_dir -v
```

Expected: `ImportError` — `components.episode_downloader` not found.

- [ ] **Step 3: Write the skeleton implementation**

`components/episode_downloader.py`:

```python
"""Episode downloader — streams audio from enclosure URLs to the local cache."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeAlias

import aiohttp

logger = logging.getLogger(__name__)

ProgressCallback: TypeAlias = Callable[[str, float], Awaitable[None]]

# Maps the MIME type portion of Content-Type to a file extension.
# response.content_type (aiohttp's parsed attribute) strips codec parameters
# such as "audio/mp4; codecs=mp4a.40.2" → "audio/mp4" before this lookup.
_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/ogg": "ogg",
    "audio/opus": "opus",
    "audio/flac": "flac",
    "audio/wav": "wav",
}


class EpisodeDownloader:
    """Downloads episode audio from enclosure URLs to a local cache directory.

    Each call to :meth:`download_all` opens a single
    :class:`aiohttp.ClientSession` and downloads episodes serially.  Failed
    episodes are retried with exponential back-off; partial files are deleted
    on exhausted retries or on cancellation.

    This class has no dependency on the config module.  The caller (Pipeline)
    is responsible for supplying the ``(guid, url)`` pairs and the cache path.

    Args:
        cache_dir: Directory where ``{guid}.{ext}`` files are written.
            Created automatically at the top of :meth:`download_all`.
        max_retries: Number of retry attempts after the first failure.
            Default is 3.
        retry_delay: Base delay in seconds for exponential back-off.
            Delay before attempt ``N+1`` is ``retry_delay * (2 ** N)``.
            Default is 1.0.
        chunk_size: Bytes per streaming chunk.  Controls how often the
            progress callback is invoked.  Default is 1 MB.
        timeout: Total timeout in seconds passed to
            :class:`aiohttp.ClientTimeout`.  ``None`` disables the timeout.
            Default is 300 s.
    """

    def __init__(
        self,
        cache_dir: Path,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        chunk_size: int = 1024 * 1024,
        timeout: float | None = 300.0,
    ) -> None:
        self._cache_dir = cache_dir
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._chunk_size = chunk_size
        self._timeout = timeout

    async def download_all(
        self,
        episodes: list[tuple[str, str]],
        on_progress: ProgressCallback | None = None,
    ) -> list[tuple[str, Path]]:
        """Download audio for each episode in order.

        Args:
            episodes: ``(guid, url)`` pairs to download.  Order is preserved.
            on_progress: Optional async callback invoked as
                ``await on_progress(guid, percent)`` where *percent* is in
                ``[0.0, 1.0]``.  ``0.0`` signals "starting / indeterminate";
                ``1.0`` signals completion.  Intermediate values are emitted
                only when the server provides a ``Content-Length`` header.

        Returns:
            ``(guid, cache_path)`` for every episode downloaded successfully,
            in input order.  Failed episodes are omitted.
        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        client_timeout = (
            aiohttp.ClientTimeout(total=self._timeout)
            if self._timeout is not None
            else aiohttp.ClientTimeout()
        )
        results: list[tuple[str, Path]] = []
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            for guid, url in episodes:
                path = await self._fetch_with_retry(session, guid, url, on_progress)
                if path is not None:
                    results.append((guid, path))
        return results

    async def _fetch_with_retry(
        self,
        session: aiohttp.ClientSession,
        guid: str,
        url: str,
        on_progress: ProgressCallback | None,
    ) -> Path | None:
        """Attempt to download one episode, retrying on transient errors.

        Args:
            session: Shared aiohttp session.
            guid: Episode GUID (used for the output filename and log messages).
            url: Enclosure URL.
            on_progress: Progress callback (may be ``None``).

        Returns:
            Path to the cached file on success, or ``None`` after all retries
            are exhausted.
        """
        for attempt in range(self._max_retries + 1):
            try:
                return await self._download_one(session, guid, url, on_progress)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < self._max_retries:
                    delay = self._retry_delay * (2**attempt)
                    logger.warning(
                        f"Download failed for '{guid}' "
                        f"(attempt {attempt + 1}/{self._max_retries + 1}): {exc}. "
                        f"Retrying in {delay:.1f}s."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"All retries exhausted for '{guid}' after "
                        f"{self._max_retries + 1} attempt(s): {exc}"
                    )
                    return None
        return None  # pragma: no cover — loop always returns or returns None inside

    async def _download_one(
        self,
        session: aiohttp.ClientSession,
        guid: str,
        url: str,
        on_progress: ProgressCallback | None,
    ) -> Path:
        """Stream one episode to disk.

        Raises:
            aiohttp.ClientError: On HTTP errors (including non-200 status).
            asyncio.TimeoutError: When the session timeout expires.
        """
        logger.debug(f"Downloading '{guid}' from {url}")
        async with session.get(url) as response:
            if response.status != 200:  # noqa: PLR2004
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message=f"HTTP {response.status}",
                )

            ext = _CONTENT_TYPE_TO_EXT.get(response.content_type)
            if ext is None:
                logger.warning(
                    f"Unknown Content-Type '{response.content_type}' for '{guid}', "
                    f"falling back to mp3."
                )
                ext = "mp3"

            output_path = self._cache_dir / f"{guid}.{ext}"
            total = response.content_length  # None when Content-Length absent
            downloaded = 0
            success = False

            if on_progress:
                await on_progress(guid, 0.0)

            try:
                with output_path.open("wb") as f:
                    async for chunk in response.content.iter_chunked(self._chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_progress and total is not None and total > 0:
                            await on_progress(guid, downloaded / total)
                success = True
            finally:
                if not success and output_path.exists():
                    output_path.unlink()
                    logger.debug(f"Deleted partial file for '{guid}' after failure.")

        if on_progress:
            await on_progress(guid, 1.0)

        logger.info(f"Downloaded '{guid}' → {output_path}")
        return output_path
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_episode_downloader.py::test_download_all_creates_cache_dir -v
```

Expected: `PASSED`.

- [ ] **Step 5: Run ruff and mypy**

```bash
uv run ruff check components/episode_downloader.py && uv run mypy components/episode_downloader.py
```

Expected: no errors or warnings. Fix any that appear before continuing.

- [ ] **Step 6: Commit**

```bash
git add components/episode_downloader.py tests/test_episode_downloader.py
git commit -m "feat: add EpisodeDownloader skeleton with cache_dir creation"
```

---

## Task 2: Successful download — streaming and extension mapping

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_episode_downloader.py`:

```python
from aioresponses import aioresponses


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

    e.g. "audio/mp4; codecs=mp4a.40.2" → content_type="audio/mp4" → ext="m4a".
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_episode_downloader.py -k "successful or extension or unknown_content" -v
```

Expected: `ModuleNotFoundError` for `aioresponses`, or test failures. If `aioresponses` is missing: `uv add --dev aioresponses` (it should already be in dev deps — verify with `uv run python -c "import aioresponses"`).

- [ ] **Step 3: Run the tests against the existing implementation**

The implementation from Task 1 should already make these pass. Run:

```bash
uv run pytest tests/test_episode_downloader.py -v
```

Expected: all tests pass. The MIME mapping and streaming are already implemented in `_download_one`.

- [ ] **Step 4: Run full suite + coverage**

```bash
uv run pytest --cov=. --cov-report=term-missing
```

Expected: all tests pass; `components/episode_downloader.py` shows 100% coverage (or close — uncovered lines will guide next tasks).

- [ ] **Step 5: Commit**

```bash
git add tests/test_episode_downloader.py
git commit -m "test: successful download, ordering, and content-type extension mapping"
```

---

## Task 3: Progress callback

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_episode_downloader.py`:

```python
async def test_progress_callback_with_content_length(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """Progress callback receives 0.0 (start), intermediate values, and 1.0 (done)."""
    calls: list[tuple[str, float]] = []

    async def on_progress(guid: str, pct: float) -> None:
        calls.append((guid, pct))

    # Use chunk_size smaller than AUDIO_DATA to force multiple intermediate calls.
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

    guids, percents = zip(*calls)
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_episode_downloader.py -k "progress" -v
```

Expected: failures because progress logic is not yet verified. (The implementation was written in Task 1, so these may already pass — confirm.)

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest tests/test_episode_downloader.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_episode_downloader.py
git commit -m "test: progress callback with and without Content-Length"
```

---

## Task 4: Retry with exponential backoff

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_episode_downloader.py`:

```python
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
        # max_retries=2 → 3 total attempts, all fail
        m.get(URL, status=503)
        m.get(URL, status=503)
        m.get(URL, status=503)
        results = await downloader.download_all([(GUID, URL)])

    assert results == []
    # No partial file should remain in cache
    assert list(cache_dir.iterdir()) == []


async def test_client_error_triggers_retry(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """aiohttp.ClientError on the first attempt triggers a retry."""
    import aiohttp as _aiohttp

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
    import asyncio as _asyncio

    with aioresponses() as m:
        m.get(URL, exception=_asyncio.TimeoutError())
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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_episode_downloader.py -k "retries or exhausted or client_error or one_fails" -v
```

Expected: failures if retry logic has bugs; or passes if implementation from Task 1 is correct.

- [ ] **Step 3: Verify implementation is correct**

```bash
uv run pytest tests/test_episode_downloader.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_episode_downloader.py
git commit -m "test: retry backoff, exhausted retries, ClientError recovery"
```

---

## Task 5: Partial file cleanup — exhausted retries and CancelledError

This task verifies the `try/finally` guard that deletes partial files on failure or cancellation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_episode_downloader.py`:

```python
import asyncio as _asyncio


async def test_partial_file_deleted_on_exhausted_retries(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """No stale partial file remains after all retries are exhausted."""
    with aioresponses() as m:
        m.get(URL, status=503)
        m.get(URL, status=503)
        m.get(URL, status=503)
        await downloader.download_all([(GUID, URL)])

    assert list(cache_dir.iterdir()) == []


async def test_cancelled_error_deletes_partial_file_and_propagates(
    downloader: EpisodeDownloader,
    cache_dir: Path,
) -> None:
    """CancelledError propagates out of download_all and the partial file is deleted."""

    # Build a fake aiohttp response that writes one chunk then raises CancelledError.
    class _FakeContent:
        async def iter_chunked(self, size: int):  # noqa: ANN201
            yield b"partial data"
            raise _asyncio.CancelledError

    class _FakeResponse:
        status = 200
        content_type = "audio/mpeg"
        content_length = 10_000
        content = _FakeContent()
        request_info = None  # not needed; error is raised before ClientResponseError
        history = ()

        async def __aenter__(self) -> _FakeResponse:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    class _FakeSession:
        def get(self, url: str, **kwargs: object) -> _FakeResponse:  # noqa: ANN201
            return _FakeResponse()

    cache_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(_asyncio.CancelledError):
        await downloader._download_one(_FakeSession(), GUID, URL, None)  # type: ignore[arg-type]

    # No partial file should survive the cancellation
    partial = cache_dir / f"{GUID}.mp3"
    assert not partial.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_episode_downloader.py -k "partial or cancelled" -v
```

Expected: `test_partial_file_deleted_on_exhausted_retries` likely passes already. `test_cancelled_error_deletes_partial_file_and_propagates` may fail if the `try/finally` guard has a bug.

- [ ] **Step 3: Verify the `try/finally` guard in `_download_one`**

The guard in the implementation must look exactly like:

```python
success = False
try:
    with output_path.open("wb") as f:
        async for chunk in response.content.iter_chunked(self._chunk_size):
            f.write(chunk)
            ...
    success = True
finally:
    if not success and output_path.exists():
        output_path.unlink()
        logger.debug(f"Deleted partial file for '{guid}' after failure.")
```

The `finally` runs even on `CancelledError` because `CancelledError` is not caught. After the `finally`, the cancellation propagates normally.

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest tests/test_episode_downloader.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_episode_downloader.py
git commit -m "test: partial file cleanup on exhausted retries and CancelledError"
```

---

## Task 6: Full test coverage check

- [ ] **Step 1: Run coverage**

```bash
uv run pytest --cov=components/episode_downloader --cov-report=term-missing tests/test_episode_downloader.py
```

Expected: 100% coverage on `components/episode_downloader.py`. If any lines are missed, add a targeted test for that branch, then recheck.

- [ ] **Step 2: Run the complete project test suite**

```bash
uv run pytest --cov=. && uv run ruff check components/episode_downloader.py tests/test_episode_downloader.py
```

Expected: all tests pass, coverage maintained, no ruff errors.

- [ ] **Step 3: Commit (only if new tests were added)**

```bash
git add tests/test_episode_downloader.py
git commit -m "test: close coverage gaps in EpisodeDownloader"
```

---

## Task 7: Pipeline integration

Wire `EpisodeDownloader` into `Pipeline`. The downloader is constructed once in `__init__` and called once per feed inside the existing for-loop, after `FeedPublisher.publish()`.

**Files:**
- Modify: `components/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing pipeline test**

Append to `tests/test_pipeline.py`. All imports (`AsyncMock`, `MagicMock`, `patch`, `Episode`, `ParsedFeed`) are already present at the top of that file.

```python
async def test_pipeline_calls_episode_downloader() -> None:
    """Pipeline calls EpisodeDownloader.download_all once per feed after publishing."""
    from datetime import datetime, timezone

    from config.config_loader import FeedConfig
    from models.feed import Episode, ParsedFeed

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
        pub_date=datetime(2026, 3, 22, tzinfo=timezone.utc),
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
        patch("components.pipeline.Database"),
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
    ):
        mock_dl_cls.return_value.download_all = AsyncMock(return_value=[("My Podcast", "<rss/>")])
        mock_fp_cls.return_value.parse_all.return_value = [parsed]
        mock_store_cls.return_value.save_episodes = AsyncMock()
        mock_store_cls.return_value.get_episodes_for_feed = AsyncMock(return_value=[ep])
        mock_pub_cls.return_value.publish = AsyncMock(return_value=Path("/output/my-podcast.rss"))
        mock_ep_dl = mock_ep_dl_cls.return_value
        mock_ep_dl.download_all = AsyncMock(return_value=[])

        from components.pipeline import Pipeline
        pipeline = Pipeline(config)
        await pipeline.run()

    # EpisodeDownloader.download_all must be called once with (guid, url) pairs
    mock_ep_dl.download_all.assert_awaited_once()
    episodes_arg = mock_ep_dl.download_all.call_args[0][0]
    assert episodes_arg == [("ep-001", "https://example.com/ep.mp3")]
```

- [ ] **Step 2: Run the new test to verify it fails**

```bash
uv run pytest tests/test_pipeline.py -k "episode_downloader" -v
```

Expected: `ImportError` — `EpisodeDownloader` not yet imported in `pipeline.py`.

- [ ] **Step 3: Wire EpisodeDownloader into pipeline.py**

In `components/pipeline.py`:

**Add import** (alongside existing component imports):
```python
from components.episode_downloader import EpisodeDownloader
```

**In `Pipeline.__init__`**, after the existing component instantiation:
```python
self._episode_downloader = EpisodeDownloader(config.app.paths.cache_dir)
```

**In `Pipeline.run()`**, inside the `async with Database(...)` block, inside the for-loop, **after** the `await self._feed_publisher.publish(publisher_input)` line:
```python
episode_pairs = [(ep.guid, ep.url) for ep in episodes]
await self._episode_downloader.download_all(
    episode_pairs,
    on_progress=self._on_download_progress,
)
```

**Add the progress handler method** to `Pipeline`, after `_build_parse_inputs`:
```python
async def _on_download_progress(self, guid: str, percent: float) -> None:
    """Log episode download progress.

    Args:
        guid: Episode GUID being downloaded.
        percent: Progress in ``[0.0, 1.0]``.  ``0.0`` means starting;
            ``1.0`` means complete.
    """
    if percent == 0.0:
        logger.debug(f"Downloading episode '{guid}' …")
    elif percent == 1.0:
        logger.debug(f"Episode '{guid}' downloaded.")
    else:
        logger.debug(f"Episode '{guid}': {percent:.0%}")
```

- [ ] **Step 4: Run the new test to verify it passes**

```bash
uv run pytest tests/test_pipeline.py -k "episode_downloader" -v
```

Expected: `PASSED`.

- [ ] **Step 5: Ensure existing pipeline tests still pass**

```bash
uv run pytest tests/test_pipeline.py -v
```

Every test that constructs a `Pipeline` and calls `await pipeline.run()` must have `EpisodeDownloader` patched — otherwise `self._episode_downloader.download_all(...)` will be called on a plain `MagicMock` which is not awaitable and will raise `TypeError`.

The following tests need `patch("components.pipeline.EpisodeDownloader") as mock_ep_dl_cls` added to their `with (...)` block, plus `mock_ep_dl_cls.return_value.download_all = AsyncMock(return_value=[])` in the setup:

- `test_run_passes_only_enabled_feeds`
- `test_run_preserves_config_order`
- `test_run_returns_parser_result`
- `test_run_calls_feed_parser`
- `test_run_with_no_enabled_feeds` (raises before download_all but patch needed for `__init__`)
- `test_run_with_feed_name_forces_disabled_feed`
- `test_run_with_feed_name_excludes_other_feeds`
- `test_run_with_unknown_feed_name_raises` — this test has a flat single-item `with patch(...)` block (no parentheses), so it must be restructured. `download_all` is never reached (ValueError is raised before the feed loop), so no `AsyncMock` setup is needed — only the patch itself:
  ```python
  # Before:
  with patch("components.pipeline.FeedDownloader"):
      pipeline = Pipeline(config, feed_name="nonexistent")
      with pytest.raises(ValueError, match="nonexistent"):
          await pipeline.run()

  # After:
  with (
      patch("components.pipeline.FeedDownloader"),
      patch("components.pipeline.EpisodeDownloader"),
  ):
      pipeline = Pipeline(config, feed_name="nonexistent")
      with pytest.raises(ValueError, match="nonexistent"):
          await pipeline.run()
  ```
- `test_run_calls_feed_publisher`
- `test_run_passes_new_channel_fields_to_publisher`
- `test_run_saves_parsed_episodes`

Pattern to add in each test (alongside existing patches):
```python
patch("components.pipeline.EpisodeDownloader") as mock_ep_dl_cls,
```
And in the setup body:
```python
mock_ep_dl_cls.return_value.download_all = AsyncMock(return_value=[])
```

- [ ] **Step 6: Run the full test suite and coverage**

```bash
uv run pytest --cov=. && uv run ruff check components/pipeline.py
```

Expected: all tests pass, 100% coverage, no ruff errors.

- [ ] **Step 7: Commit**

```bash
git add components/pipeline.py tests/test_pipeline.py
git commit -m "feat: wire EpisodeDownloader into pipeline after FeedPublisher.publish"
```

---

## Verification

End-to-end sanity check (requires a real config with at least one enabled feed):

```bash
uv run python main.py --debug
```

Expected: logs show `"Downloading episode '...' …"` and `"Episode '...' downloaded."` for each episode; `cache/` directory contains `{guid}.{ext}` files after the run.

Run the full test suite one final time:

```bash
uv run pytest --cov=. && uv run ruff check . && uv run mypy components/episode_downloader.py components/pipeline.py
```

Expected: all tests pass, 100% coverage, no linting or type errors.
