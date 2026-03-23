"""AudioPreprocessor — converts downloaded audio to mono AAC via ffmpeg."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path  # noqa: TC003

from utils.exceptions import FfmpegError
from utils.ffmpeg import Ffmpeg

logger = logging.getLogger(__name__)

type ProgressCallback = Callable[[str, float], Awaitable[None]]


class AudioPreprocessor:
    """Converts downloaded episode audio to mono 32 kbps AAC for further processing.

    For each ``(guid, input_path, duration)`` triple, runs::

        ffmpeg -i <input> -ac 1 -c:a aac -b:a 32k -map_metadata -1 -y <output>

    The output path is ``cache_dir / "{guid}.mono.m4a"``.

    This class has no dependency on the config module.  The caller (Pipeline)
    is responsible for supplying the triples and the cache path.

    Args:
        cache_dir: Directory where ``{guid}.mono.m4a`` files are written.
            Created automatically if it does not exist.

    """

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir

    async def preprocess(
        self,
        guid: str,
        input_path: Path,
        duration: float,
        on_progress: ProgressCallback | None = None,
    ) -> Path:
        """Convert one episode audio file to mono 32 kbps AAC.

        Args:
            guid: Episode GUID (used for the output filename and log messages).
            input_path: Path to the source audio file.
            duration: Audio duration in seconds (used for ffmpeg progress tracking).
            on_progress: Optional async callback invoked as
                ``await on_progress(guid, percent)`` where *percent* is in
                ``[0.0, 1.0]``.

        Returns:
            Path to the output ``{guid}.mono.m4a`` file.

        Raises:
            FfmpegError: If ffmpeg exits with a non-zero code.

        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._cache_dir / f"{guid}.mono.m4a"

        async def _wrap(pct: float) -> None:
            if on_progress:
                await on_progress(guid, pct)

        try:
            await Ffmpeg().run(
                [
                    "-i", str(input_path),
                    "-vn",
                    "-ac", "1",
                    "-c:a", "aac",
                    "-b:a", "32k",
                    "-map_metadata", "-1",
                    "-y",
                    str(output_path),
                ],
                on_progress=_wrap,
                duration=duration,
            )
        except FfmpegError as exc:
            logger.error(f"Skipping audio preprocessing for '{guid}': {exc.message}")
            if exc.stderr.strip():
                logger.debug(f"ffmpeg stderr for '{guid}':\n{exc.stderr.strip()}")
            raise

        logger.debug(f"Preprocessed '{guid}' → {output_path}")
        return output_path
