import asyncio
import logging
from pathlib import Path

import aiosqlite
from pydub import AudioSegment

from config.config_loader import AppConfig
from db.repositories import AdSegmentRepository
from models.ad_segment import AdSegment
from pipeline.exceptions import AudioEditError

logger = logging.getLogger(__name__)


async def cut_ads(
    audio_path: Path,
    ad_segments: list[AdSegment],
    cfg: AppConfig,
    db: aiosqlite.Connection,
    *,
    output_path: Path,
) -> Path:
    """Cut ad segments from audio and export clean file."""
    result = await asyncio.to_thread(_cut_ads_sync, audio_path, ad_segments, cfg, output_path)
    await _mark_segments_cut(ad_segments, db)
    return result


def _cut_ads_sync(
    audio_path: Path,
    ad_segments: list[AdSegment],
    cfg: AppConfig,
    output_path: Path,
) -> Path:
    """Cut ad segments from audio and export the clean file (runs in a thread)."""
    try:
        audio = AudioSegment.from_file(audio_path)
    except Exception as exc:
        raise AudioEditError(f"Failed to load audio: {exc}") from exc

    original_duration_ms = len(audio)
    ad_segments_sorted = sorted(ad_segments, key=lambda s: s.start_ms)

    keep_segments = []
    last_end = 0

    for seg in ad_segments_sorted:
        if seg.start_ms > last_end:
            keep_segments.append(audio[last_end : seg.start_ms])
        last_end = max(last_end, seg.end_ms)

    if last_end < original_duration_ms:
        keep_segments.append(audio[last_end:])

    if not keep_segments:
        msg = "All audio would be cut; no keep segments remain"
        raise AudioEditError(msg)

    try:
        clean_audio = sum(keep_segments[1:], keep_segments[0])
    except Exception as exc:
        raise AudioEditError(f"Failed to concatenate audio: {exc}") from exc

    try:
        clean_audio.export(
            output_path,
            format=cfg.audio.output_format.value,
            bitrate=cfg.audio.cbr_bitrate,
        )
    except Exception as exc:
        raise AudioEditError(f"Failed to export audio: {exc}") from exc

    removed_ms = sum(seg.end_ms - seg.start_ms for seg in ad_segments_sorted)
    removed_sec = removed_ms / 1000
    pct = (removed_ms / original_duration_ms) * 100 if original_duration_ms > 0 else 0

    logger.info(
        f"Export complete: {output_path.name} — removed {removed_sec:.1f}s ({pct:.0f}% of episode)"
    )

    return output_path


async def _mark_segments_cut(segments: list[AdSegment], db: aiosqlite.Connection) -> None:
    """Mark segments as cut in database."""
    repo = AdSegmentRepository(db)
    if segments:
        await repo.mark_cut(segments[0].episode_guid)
