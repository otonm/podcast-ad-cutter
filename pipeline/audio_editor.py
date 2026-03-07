import asyncio
import logging
from pathlib import Path

import aiosqlite

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
    if not ad_segments:
        logger.info("No ad segments to cut, source file unchanged")
        return audio_path

    result = await _cut_ads_async(audio_path, ad_segments, cfg, output_path)
    await _mark_segments_cut(ad_segments, db)
    return result


async def _get_duration_seconds(audio_path: Path) -> float:
    """Return duration of audio file in seconds using ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(audio_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise AudioEditError(f"ffprobe failed on {audio_path.name}")
    return float(stdout.strip())


def _build_filtergraph(keep_spans: list[tuple[float, float | None]]) -> str:
    """Build an ffmpeg filter_complex string that trims and concatenates keep spans."""
    parts: list[str] = []
    labels: list[str] = []
    for i, (start, end) in enumerate(keep_spans):
        label = f"s{i}"
        if end is None:
            parts.append(f"[0]atrim=start={start},asetpts=PTS-STARTPTS[{label}]")
        else:
            parts.append(f"[0]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{label}]")
        labels.append(f"[{label}]")
    n = len(keep_spans)
    concat = "".join(labels) + f"concat=n={n}:v=0:a=1[out]"
    return ";".join(parts) + ";" + concat


async def _cut_ads_async(
    audio_path: Path,
    ad_segments: list[AdSegment],
    cfg: AppConfig,
    output_path: Path,
) -> Path:
    """Cut ad segments using ffmpeg filtergraph — no PCM decode into memory."""
    ad_segments_sorted = sorted(ad_segments, key=lambda s: s.start_ms)

    total_duration_sec = await _get_duration_seconds(audio_path)
    original_duration_ms = int(total_duration_sec * 1000)

    last_end_ms = 0
    keep_spans: list[tuple[float, float | None]] = []
    for seg in ad_segments_sorted:
        if seg.start_ms > last_end_ms:
            keep_spans.append((last_end_ms / 1000.0, seg.start_ms / 1000.0))
        last_end_ms = max(last_end_ms, seg.end_ms)
    if last_end_ms < original_duration_ms:
        keep_spans.append((last_end_ms / 1000.0, None))

    if not keep_spans:
        msg = "All audio would be cut; no keep segments remain"
        raise AudioEditError(msg)

    filtergraph = _build_filtergraph(keep_spans)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-filter_complex", filtergraph,
        "-map", "[out]",
        "-b:a", cfg.audio.cbr_bitrate,
        str(output_path),
    ]
    logger.debug(f"ffmpeg cmd: {' '.join(cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise AudioEditError(f"ffmpeg failed: {err}")

    removed_ms = sum(seg.end_ms - seg.start_ms for seg in ad_segments_sorted)
    removed_sec = removed_ms / 1000
    pct = (removed_ms / original_duration_ms) * 100 if original_duration_ms > 0 else 0
    logger.info(f"Export complete: {output_path.name} — removed {removed_sec:.1f}s ({pct:.0f}% of episode)")

    return output_path


async def _mark_segments_cut(segments: list[AdSegment], db: aiosqlite.Connection) -> None:
    """Mark segments as cut in database."""
    repo = AdSegmentRepository(db)
    if segments:
        await repo.mark_cut(segments[0].episode_guid)
