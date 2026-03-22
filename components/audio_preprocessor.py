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

    For each ``(guid, input_path)`` pair, runs::

        ffmpeg -i <input> -ac 1 -c:a aac -b:a 32k -map_metadata -1 -y <output>

    The output path is ``cache_dir / "{guid}.mono.m4a"``.

    This class has no dependency on the config module.  The caller (Pipeline)
    is responsible for supplying the pairs and the cache path.

    Args:
        cache_dir: Directory where ``{guid}.mono.m4a`` files are written.
            Created automatically if it does not exist.

    """

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir

    async def preprocess_all(
        self,
        pairs: list[tuple[str, Path]],
        on_progress: ProgressCallback | None = None,
    ) -> list[tuple[str, Path]]:
        """Convert each ``(guid, input_path)`` pair to a mono AAC file.

        Args:
            pairs: ``(guid, local_path)`` pairs to process.  Order is preserved.
            on_progress: Optional async callback invoked as
                ``await on_progress(guid, percent)`` where *percent* is in
                ``[0.0, 1.0]``.  ``0.0`` signals start; ``1.0`` signals
                completion.

        Returns:
            ``(guid, output_path)`` for every episode converted successfully,
            in input order.  Failed episodes are omitted.

        """
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        results: list[tuple[str, Path]] = []

        for guid, input_path in pairs:
            output_path = self._cache_dir / f"{guid}.mono.m4a"
            try:
                ffmpeg_cb = None
                if on_progress:
                    async def _wrap(pct: float, _guid: str = guid) -> None:
                        await on_progress(_guid, pct)  # type: ignore[misc]
                    ffmpeg_cb = _wrap

                await Ffmpeg().run(
                    [
                        "-i", str(input_path),
                        "-ac", "1",
                        "-c:a", "aac",
                        "-b:a", "32k",
                        "-map_metadata", "-1",
                        "-y",
                        str(output_path),
                    ],
                    on_progress=ffmpeg_cb,
                )
                results.append((guid, output_path))
                logger.debug(f"Preprocessed '{guid}' \u2192 {output_path}")
            except FfmpegError as exc:
                logger.error(f"Skipping audio preprocessing for '{guid}': {exc}")

        return results
