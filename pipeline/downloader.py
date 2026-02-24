import logging
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from models.episode import Episode
from pipeline.exceptions import DownloadError

logger = logging.getLogger(__name__)


async def download_episode(
    episode: Episode,
    *,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Stream-download the episode audio to a temp file and return the path."""
    suffix = Path(urlparse(str(episode.audio_url)).path).suffix or ".mp3"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    import os
    os.close(fd)
    dest = Path(tmp)

    logger.info(f"Downloading {episode.audio_url} → {dest}")
    should_close = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True)

    try:
        async with client.stream("GET", str(episode.audio_url)) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)
    except httpx.HTTPError as exc:
        dest.unlink(missing_ok=True)
        logger.error(f"Download failed for {episode.guid}: {exc}")
        raise DownloadError(f"Download failed: {exc}") from exc
    finally:
        if should_close:
            await client.aclose()

    file_size = dest.stat().st_size
    logger.info(f"Download complete: {dest.name} ({file_size} bytes)")
    return dest
