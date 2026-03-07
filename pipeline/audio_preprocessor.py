import asyncio
import logging
from pathlib import Path

from pipeline.exceptions import AudioEditError

logger = logging.getLogger(__name__)

_TARGET_SAMPLE_RATE = 16_000  # 16 kHz — Whisper's native sample rate
_TARGET_CHANNELS = 1          # mono — halves file size, no transcription quality loss
_TARGET_BITRATE = "32k"       # sufficient for speech; ~4 MB/hr


async def prepare_for_transcription(audio_path: Path) -> Path:
    """Convert audio to mono 16 kHz 32 kbps MP3 ready for upload.

    Uses ffmpeg as a subprocess to avoid loading any audio data into Python
    memory. A 40-minute podcast that would otherwise consume ~650 MB via pydub
    uses under 5 MB with this approach.

    The output file is placed in the same directory as the source.
    Caller is responsible for deleting it after use.
    """
    output_path = audio_path.parent / "transcription_input.mp3"
    logger.info(f"Pre-processing audio for transcription: {audio_path.name} → {output_path.name}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-vn",
        "-ar", str(_TARGET_SAMPLE_RATE),
        "-ac", str(_TARGET_CHANNELS),
        "-b:a", _TARGET_BITRATE,
        str(output_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise AudioEditError(f"ffmpeg preprocessing failed: {stderr.decode()}")

    size_mb = output_path.stat().st_size / 1_048_576
    logger.info(f"Pre-processing complete: {output_path.name} ({size_mb:.1f} MB)")

    return output_path
