import asyncio
import logging
from pathlib import Path

from pydub import AudioSegment

logger = logging.getLogger(__name__)

_TARGET_CHANNELS = 1       # mono — halves file size, no transcription quality loss
_TARGET_FRAME_RATE = 16000 # 16 kHz — Whisper's native sample rate
_TARGET_SAMPLE_WIDTH = 2   # 16-bit PCM before MP3 encode
_TARGET_BITRATE = "32k"    # sufficient for speech; ~4 MB/hr
_TARGET_FORMAT = "mp3"


async def prepare_for_transcription(audio_path: Path) -> Path:
    """Convert audio to mono 16 kHz 32 kbps MP3 ready for upload.

    The output file is placed in the same directory as the source.
    Caller is responsible for deleting it after use.
    """
    return await asyncio.to_thread(_convert_sync, audio_path)


def _convert_sync(audio_path: Path) -> Path:
    output_path = audio_path.parent / "transcription_input.mp3"
    logger.info(f"Pre-processing audio for transcription: {audio_path.name} → {output_path.name}")
    audio = AudioSegment.from_file(audio_path)
    audio = (
        audio.set_channels(_TARGET_CHANNELS)
             .set_frame_rate(_TARGET_FRAME_RATE)
             .set_sample_width(_TARGET_SAMPLE_WIDTH)
    )
    audio.export(output_path, format=_TARGET_FORMAT, bitrate=_TARGET_BITRATE)
    size_mb = output_path.stat().st_size / 1_048_576
    logger.info(f"Pre-processing complete: {output_path.name} ({size_mb:.1f} MB)")
    return output_path
