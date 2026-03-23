"""EpisodeCopier — copies original downloaded episode files to the output directory."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import TYPE_CHECKING

from components.feed_publisher import FeedPublisher

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

logger = logging.getLogger(__name__)


class EpisodeCopier:
    """Copies raw downloaded episode audio files to the structured output directory.

    The destination path follows the pattern::

        output_dir/{feed_slug}/{DD.MM.YYYY}-{slugified-title}.{ext}

    If the destination already exists the copy is skipped (idempotent).

    Args:
        output_dir: Base output directory; feed subdirectories are created automatically.
        base_url: Server base URL used to construct the public episode URL.

    """

    def __init__(self, output_dir: Path, base_url: str) -> None:
        self._output_dir = output_dir
        self._base_url = base_url

    async def copy(
        self,
        guid: str,
        src: Path,
        feed_slug: str,
        pub_date: datetime,
        title: str,
    ) -> tuple[str, Path, str]:
        """Copy one episode file to its destination in the output directory.

        If the destination already exists the copy is skipped (idempotent).

        Args:
            guid: Episode GUID (used for log messages).
            src: Source audio file path.
            feed_slug: Slugified feed title (used as subdirectory name).
            pub_date: Episode publication date (used in the filename).
            title: Episode title (slugified and used in the filename).

        Returns:
            ``(guid, dest_path, new_url)`` triple.

        """
        ext = src.suffix.lstrip(".")
        filename = FeedPublisher.episode_filename(pub_date, title, ext)
        dest = self._output_dir / feed_slug / filename
        new_url = FeedPublisher.episode_url(self._base_url, feed_slug, pub_date, title, ext)

        if dest.exists():
            logger.debug(f"Episode '{guid}': output file already exists at {dest}, skipping copy")
            return (guid, dest, new_url)

        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, src, dest)
        logger.info(f"Episode '{guid}': copied to {dest}")
        return (guid, dest, new_url)
