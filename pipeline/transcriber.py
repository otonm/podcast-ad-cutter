import logging
from pathlib import Path

import aiosqlite

from config.config_loader import AppConfig
from db.repositories import TranscriptRepository
from db.repositories.llm_call_repo import LLMCallRepository
from models.episode import Episode
from models.llm_call import CallType, LLMCall
from models.transcript import Segment, Transcript
from pipeline import audio_preprocessor
from pipeline.exceptions import TranscriptionError
from pipeline.llm_client import transcribe as llm_transcribe

logger = logging.getLogger(__name__)


async def transcribe_episode(
    episode: Episode,
    audio_path: Path,
    cfg: AppConfig,
    db: aiosqlite.Connection,
) -> Transcript | None:
    """Transcribe the episode audio. Returns cached transcript if available."""
    repo = TranscriptRepository(db)

    cached = await repo.get_by_episode_guid(episode.guid)
    if cached:
        logger.info(f"Transcript cache hit for {episode.guid}")
        return cached

    logger.info(f"Starting transcription for {episode.guid}")

    transcription_path = await audio_preprocessor.prepare_for_transcription(audio_path)
    try:
        result, cost = await llm_transcribe(transcription_path, cfg.transcription)
    except Exception as exc:
        logger.error(f"Transcription failed for {episode.guid}: {exc}")
        raise TranscriptionError(f"Transcription failed: {exc}") from exc
    finally:
        transcription_path.unlink(missing_ok=True)

    raw_items = result.get("words") or result.get("segments") or []
    segments = []
    for item in raw_items:
        start_ms = int(item.get("start", 0) * 1000)
        end_ms = int(item.get("end", 0) * 1000)
        text = item.get("word") or item.get("text", "")
        if text:
            segments.append(Segment(start_ms=start_ms, end_ms=end_ms, text=text))

    full_text = " ".join(item.get("word") or item.get("text", "") for item in raw_items)
    transcript = Transcript(
        episode_guid=episode.guid,
        segments=tuple(segments),
        full_text=full_text,
        language=result.get("language", "en"),
        provider_model=cfg.transcription.provider_model,
    )

    await repo.save(transcript)
    llm_repo = LLMCallRepository(db)
    await llm_repo.save(
        LLMCall(
            episode_guid=episode.guid,
            call_type=CallType.TRANSCRIPTION,
            model=cfg.transcription.provider_model,
            cost_usd=cost,
        )
    )
    duration_ms = segments[-1].end_ms if segments else 0
    logger.info(f"Transcription saved: {len(segments)} segments, {duration_ms / 1000:.1f}s")

    return transcript
