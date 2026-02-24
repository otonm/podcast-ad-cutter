import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from models.episode import Episode
from pipeline.exceptions import DownloadError

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug[:80]


async def download_episode(
    episode: Episode,
    *,
    output_dir: Path,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Stream-download the episode audio and return the local file path."""
    slug = _slugify(episode.title)
    suffix = Path(urlparse(str(episode.audio_url)).path).suffix or ".mp3"
    episode_dir = output_dir / slug
    episode_dir.mkdir(parents=True, exist_ok=True)
    dest = episode_dir / f"original{suffix}"

    if dest.exists() and dest.stat().st_size > 0:
        logger.info("Already downloaded: %s", dest)
        return dest

    logger.info("Downloading %s → %s", episode.audio_url, dest)
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)

    hasher = hashlib.sha256()
    try:
        async with client.stream("GET", str(episode.audio_url)) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
                    hasher.update(chunk)
    except httpx.HTTPError as exc:
        logger.error("Download failed for %s: %s", episode.guid, exc)
        raise DownloadError(f"Download failed: {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    file_size = dest.stat().st_size
    logger.info("Download complete: %s (%d bytes)", dest.name, file_size)
    return dest
