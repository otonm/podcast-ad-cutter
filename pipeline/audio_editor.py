import asyncio
import logging
from pathlib import Path

from config_loader import AppConfig
from db.repositories import AdSegmentRepository
from models.ad_segment import AdSegment
from pydub import AudioSegment
from pipeline.exceptions import AudioEditError

logger = logging.getLogger(__name__)


async def cut_ads(
    audio_path: Path,
    ad_segments: list[AdSegment],
    cfg: AppConfig,
    db,
) -> Path:
    """Cut ad segments from audio and export clean file."""
    return await asyncio.to_thread(_cut_ads_sync, audio_path, ad_segments, cfg, db)


def _cut_ads_sync(
    audio_path: Path,
    ad_segments: list[AdSegment],
    cfg: AppConfig,
    db,
) -> Path:
    """Synchronous audio cutting function (runs in thread)."""
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
        logger.warning("No audio segments to keep after cutting ads")
        return audio_path

    try:
        clean_audio = sum(keep_segments[1:], keep_segments[0])
    except Exception as exc:
        raise AudioEditError(f"Failed to concatenate audio: {exc}") from exc

    output_path = audio_path.parent / f"clean{audio_path.suffix}"

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
        "Export complete: %s — removed %.1fs (%.0f%% of episode)",
        output_path.name,
        removed_sec,
        pct,
    )

    asyncio.create_task(_mark_segments_cut(ad_segments_sorted, db))

    return output_path


async def _mark_segments_cut(segments: list[AdSegment], db) -> None:
    """Mark segments as cut in database."""
    repo = AdSegmentRepository(db)
    if segments:
        await repo.mark_cut(segments[0].episode_guid)
