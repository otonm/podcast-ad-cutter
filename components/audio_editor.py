"""AudioEditor — cuts ad segments from audio and encodes the output file."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from components.feed_publisher import FeedPublisher
from utils.ffmpeg import Ffmpeg

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from datetime import datetime
    from pathlib import Path

    from models.ad_detection import CutRange

logger = logging.getLogger(__name__)

_CODEC_MAP: dict[str, str] = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "ogg": "libvorbis",
    "opus": "libopus",
    "flac": "flac",
}


class AudioEditor:
    """Cuts ad segments from audio files using ffmpeg atrim+concat.

    Returns ``None`` when there are no cut ranges or all audio is ads.
    Returns the output ``Path`` when ads were cut.

    Args:
        output_dir: Base output directory.
        file_type: Output audio format (default ``"mp3"``).
        bitrate: Output bitrate (default ``"128k"``).

    """

    def __init__(
        self,
        output_dir: Path,
        file_type: str = "mp3",
        bitrate: str = "128k",
    ) -> None:
        self._output_dir = output_dir
        self._file_type = file_type
        self._bitrate = bitrate

    async def edit(
        self,
        guid: str,
        input_path: Path,
        cut_ranges: list[CutRange],
        feed_slug: str,
        pub_date: datetime,
        title: str,
        total_duration_s: float = 3600.0,
    ) -> Path | None:
        """Cut ad time ranges from audio and produce the output file.

        Args:
            guid: Episode GUID (used for log messages).
            input_path: Raw downloaded audio file.
            cut_ranges: Time ranges to cut (may be empty).
            feed_slug: Slugified feed title for output subdirectory.
            pub_date: Episode publication date.
            title: Episode title.
            total_duration_s: Total audio duration in seconds for ffmpeg progress.

        Returns:
            Output file path when ads were cut; ``None`` when no cut ranges exist
            (caller should keep the original episode URL unchanged).

        """
        logger.debug(f"Episode '{guid}': {len(cut_ranges)} cut range(s) to apply")
        if not cut_ranges:
            return None

        merged = self._merge_overlapping(cut_ranges)
        keep_segments = self._build_keep_segments(merged, total_duration_s)

        if not keep_segments:
            logger.warning(
                f"Episode '{guid}': all audio classified as ads — skipping edit to preserve audio"
            )
            return None

        filename = FeedPublisher.episode_filename(pub_date, title, self._file_type)
        dest = self._output_dir / feed_slug / filename

        if dest.exists():
            logger.debug(f"Episode '{guid}': output file already exists at {dest}, skipping edit")
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)
        codec = _CODEC_MAP.get(self._file_type, "libmp3lame")

        filter_complex, out_label = self._build_filter_complex(keep_segments)
        kept_duration = sum(
            (end - start) if end is not None else total_duration_s - start
            for start, end in keep_segments
        )

        args = [
            "-i", str(input_path),
            "-filter_complex", filter_complex,
            "-map", out_label,
            "-vn",
            "-c:a", codec,
            "-b:a", self._bitrate,
            "-map_metadata", "-1",
            "-y",
            str(dest),
        ]

        await Ffmpeg().run(args, on_progress=self._on_progress(guid), duration=kept_duration)
        logger.info(f"Episode '{guid}': edited audio saved to {dest}")
        return dest

    @staticmethod
    def _merge_overlapping(segments: list[CutRange]) -> list[CutRange]:
        """Sort and merge any overlapping cut ranges."""
        sorted_segs = sorted(segments, key=lambda s: s.start_ms)
        merged: list[CutRange] = [sorted_segs[0]]
        for seg in sorted_segs[1:]:
            prev = merged[-1]
            if seg.start_ms <= prev.end_ms:
                merged[-1] = replace(prev, end_ms=max(prev.end_ms, seg.end_ms))
            else:
                merged.append(seg)
        return merged

    @staticmethod
    def _build_keep_segments(
        qualifying: list[CutRange],
        total_duration_s: float,
    ) -> list[tuple[float, float | None]]:
        """Build list of (start_s, end_s|None) time ranges to keep."""
        keep: list[tuple[float, float | None]] = []

        first_start = qualifying[0].start_ms / 1000.0
        if first_start > 0.0:
            keep.append((0.0, first_start))

        for i, seg in enumerate(qualifying):
            if i + 1 < len(qualifying):
                next_start = qualifying[i + 1].start_ms / 1000.0
                seg_end = seg.end_ms / 1000.0
                if next_start > seg_end:
                    keep.append((seg_end, next_start))
            else:
                seg_end = seg.end_ms / 1000.0
                if seg_end < total_duration_s:
                    keep.append((seg_end, None))

        return keep

    @staticmethod
    def _build_filter_complex(
        keep_segments: list[tuple[float, float | None]],
    ) -> tuple[str, str]:
        """Build ffmpeg filter_complex string for atrim+concat."""
        parts = []
        labels = []
        for i, (start_s, end_s) in enumerate(keep_segments):
            label = f"a{i}"
            trim = f"atrim={start_s}:{end_s}" if end_s is not None else f"atrim={start_s}"
            parts.append(f"[0:a]{trim},asetpts=PTS-STARTPTS[{label}]")
            labels.append(f"[{label}]")
        n = len(keep_segments)
        concat_input = "".join(labels)
        parts.append(f"{concat_input}concat=n={n}:v=0:a=1[out]")
        return ";".join(parts), "[out]"

    @staticmethod
    def _on_progress(guid: str) -> Callable[[float], Coroutine[None, None, None]]:
        """Return a progress callback that logs completion at 100%."""
        async def _cb(pct: float) -> None:
            if pct == 1.0:
                logger.debug(f"Episode '{guid}': audio editing complete")

        return _cb
