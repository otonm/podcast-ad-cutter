"""Episode downloader — streams audio from enclosure URLs to the local cache."""

from __future__ import annotations

import asyncio
import http
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path  # noqa: TC003

import aiohttp
from yarl import URL

logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 10
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

type ProgressCallback = Callable[[str, float], Awaitable[None]]

# acast's CDN (stitcher2.acast.com / CloudFront) rejects requests that carry
# aiohttp's default User-Agent.  curl/7.x.x is accepted by all known podcast CDNs.
_DEFAULT_HEADERS: dict[str, str] = {"User-Agent": "curl/7.88.1"}

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
        headers: HTTP headers sent with every request.  Defaults to
            :data:`_DEFAULT_HEADERS` (``User-Agent: curl/7.88.1``), which is
            accepted by acast and other podcast CDNs that block generic
            library User-Agents.

    """

    def __init__(
        self,
        cache_dir: Path,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        chunk_size: int = 1024 * 1024,
        timeout: float | None = 300.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._chunk_size = chunk_size
        self._timeout = timeout
        self._headers: dict[str, str] = headers if headers is not None else _DEFAULT_HEADERS

    async def download(
        self,
        guid: str,
        url: str,
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        """Download audio for one episode to the local cache.

        Args:
            guid: Episode GUID (used for the output filename and log messages).
            url: Enclosure URL to download from.
            on_progress: Optional async callback invoked as
                ``await on_progress(guid, percent)`` where *percent* is in
                ``[0.0, 1.0]``.  ``0.0`` signals "starting / indeterminate";
                ``1.0`` signals completion.  Intermediate values are emitted
                only when the server provides a ``Content-Length`` header.

        Returns:
            Path to the cached file.

        Raises:
            aiohttp.ClientError: After all retries are exhausted.
            TimeoutError: After all retries are exhausted.

        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        client_timeout = aiohttp.ClientTimeout(total=self._timeout)
        async with aiohttp.ClientSession(timeout=client_timeout, headers=self._headers) as session:
            return await self._fetch_with_retry(session, guid, url, on_progress)

    async def _fetch_with_retry(
        self,
        session: aiohttp.ClientSession,
        guid: str,
        url: str,
        on_progress: ProgressCallback | None,
    ) -> Path:
        """Attempt to download one episode, retrying on transient errors.

        Args:
            session: Shared aiohttp session.
            guid: Episode GUID (used for the output filename and log messages).
            url: Enclosure URL.
            on_progress: Progress callback (may be ``None``).

        Returns:
            Path to the cached file on success.

        Raises:
            aiohttp.ClientError: After all retries are exhausted.
            TimeoutError: After all retries are exhausted.

        """
        for attempt in range(self._max_retries + 1):
            try:
                return await self._download_episode(session, guid, url, on_progress)
            except aiohttp.ClientResponseError as exc:
                # 4xx errors are permanent for a given URL — never retry.
                # 5xx errors are server-side transient — retry with back-off.
                if exc.status < http.HTTPStatus.INTERNAL_SERVER_ERROR or attempt >= self._max_retries:
                    raise
                delay = self._retry_delay * (2**attempt)
                logger.warning(
                    f"Download failed for '{guid}' "
                    f"(attempt {attempt + 1}/{self._max_retries + 1}): {exc}. "
                    f"Retrying in {delay:.1f}s."
                )
                await asyncio.sleep(delay)
            except (TimeoutError, aiohttp.ClientError) as exc:
                if attempt < self._max_retries:
                    delay = self._retry_delay * (2**attempt)
                    logger.warning(
                        f"Download failed for '{guid}' "
                        f"(attempt {attempt + 1}/{self._max_retries + 1}): {exc}. "
                        f"Retrying in {delay:.1f}s."
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
        msg = "unreachable"  # pragma: no cover
        raise RuntimeError(msg)  # pragma: no cover

    async def _download_episode(
        self,
        session: aiohttp.ClientSession,
        guid: str,
        url: str,
        on_progress: ProgressCallback | None,
    ) -> Path:
        """Stream one episode to disk, following redirects with encoding preserved.

        aiohttp's default redirect handling parses each Location header through
        yarl.URL(), which normalises percent-encoded characters (e.g. %3D → =).
        CloudFront signed URLs (used by acast) embed base64 in query parameters
        and include the exact raw URL in the signed policy; any normalisation
        invalidates the signature and produces a 403.  We therefore disable
        aiohttp's auto-redirect and follow each hop manually using
        URL(location, encoded=True) to preserve the original byte sequence.

        Raises:
            aiohttp.ClientError: On HTTP errors (including non-200 status) or
                when the redirect limit is exceeded.
            asyncio.TimeoutError: When the session timeout expires.

        """
        logger.debug(f"Downloading '{guid}' from {url}")

        current_url: str | URL = URL(url, encoded=True)

        for _ in range(_MAX_REDIRECTS + 1):
            async with session.get(current_url, allow_redirects=False) as response:

                # ── Follow redirect, preserving exact percent-encoding ──────────
                if response.status in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise aiohttp.ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=f"HTTP {response.status} with no Location header",
                        )
                    current_url = URL(location, encoded=True)
                    continue

                # ── Non-redirect: expect 200 ───────────────────────────────────
                if response.status != http.HTTPStatus.OK:
                    raise aiohttp.ClientResponseError(
                        response.request_info,
                        response.history,
                        status=response.status,
                        message=f"HTTP {response.status}",
                    )

                return await self._stream_response(guid, response, on_progress)

        raise aiohttp.ClientConnectionError(
            f"Exceeded {_MAX_REDIRECTS} redirects for '{guid}'"
        )

    async def _stream_response(
        self,
        guid: str,
        response: aiohttp.ClientResponse,
        on_progress: ProgressCallback | None,
    ) -> Path:
        """Write the response body to ``{cache_dir}/{guid}.{ext}`` and return the path."""
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
            if not success:
                output_path.unlink(missing_ok=True)
                logger.debug(f"Deleted partial file for '{guid}' after failure.")

        if on_progress:
            await on_progress(guid, 1.0)

        logger.info(f"Downloaded '{guid}' → {output_path}")
        return output_path
