"""AudioProber — extracts codec/duration/channels/bitrate via ffprobe."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from models.feed import AudioMetadata
from utils.exceptions import AudioProbeError

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class AudioProber:
    """Probes downloaded episode files with ffprobe to extract audio metadata.

    Each call to :meth:`probe_all` runs ``ffprobe`` once per file via
    ``asyncio.create_subprocess_exec``.  Failed probes are logged and skipped;
    the episode is omitted from the returned list.

    This class has no dependency on the config module or the database.  The
    caller (Pipeline) is responsible for filtering already-probed episodes and
    persisting the results.

    Args:
        timeout: Seconds before an individual ffprobe call is cancelled.
            Default is 30.0.

    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def probe(self, guid: str, path: Path) -> AudioMetadata:
        """Probe one audio file and return its metadata.

        Args:
            guid: Episode GUID — used in error messages and the result.
            path: Path to the local audio file.

        Returns:
            :class:`~models.feed.AudioMetadata` with codec, duration,
            channels and bitrate.

        Raises:
            AudioProbeError: On non-zero ffprobe exit, timeout, missing audio
                stream, or unparseable JSON output.

        """
        metadata = await self._probe_one(guid, path)
        logger.debug(
            f"Probed '{guid}': codec={metadata.codec}, "
            f"duration={metadata.duration:.2f}s, "
            f"channels={metadata.channels}, "
            f"bitrate={metadata.bitrate}bps"
        )
        return metadata

    async def _probe_one(self, guid: str, path: Path) -> AudioMetadata:
        """Run ffprobe on one file and return parsed metadata.

        Args:
            guid: Episode GUID — used only in error messages and the result.
            path: Path to the local audio file.

        Raises:
            AudioProbeError: On non-zero ffprobe exit, timeout, missing audio
                stream, or unparseable JSON output.

        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            raise AudioProbeError(f"ffprobe timed out probing '{guid}'") from exc

        if proc.returncode != 0:
            raise AudioProbeError(
                f"ffprobe exited with code {proc.returncode} for '{guid}'"
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise AudioProbeError(f"ffprobe produced invalid JSON for '{guid}'") from exc

        streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        if not streams:
            raise AudioProbeError(f"No audio stream found in ffprobe output for '{guid}'")

        stream = streams[0]
        fmt = data.get("format", {})
        format_bitrate = fmt.get("bit_rate", "0") or "0"
        bitrate = (
            int(format_bitrate) if format_bitrate != "0"
            else int(stream.get("bit_rate", "0"))
        )

        try:
            return AudioMetadata(
                guid=guid,
                duration=float(stream["duration"]),
                codec=str(stream["codec_name"]),
                channels=int(stream["channels"]),
                bitrate=bitrate,
            )
        except (KeyError, ValueError) as exc:
            raise AudioProbeError(
                f"Missing or invalid field in ffprobe output for '{guid}'"
            ) from exc
