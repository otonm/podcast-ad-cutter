import asyncio
import json
import logging
from dataclasses import dataclass

from config.config_loader import AppConfig
from models.ad_segment import AdSegment, TopicContext
from models.transcript import Transcript
from pipeline.llm_client import complete

logger = logging.getLogger(__name__)

_LLM_SEMAPHORE = asyncio.Semaphore(3)

AD_DETECTION_PROMPT = """Identify advertisements in this podcast transcript segment.
An ad is any span where the host promotes a product, service, or sponsor.
Exclude brand mentions that are naturally part of the episode content.
Return only a JSON array — no markdown, no preamble.
Schema: [{"start_sec": float, "end_sec": float, "confidence": float,
          "reason": str, "sponsor": str | null}]
Return [] if no ads are found."""


@dataclass
class TranscriptChunk:
    episode_guid: str
    start_sec: float
    end_sec: float
    text: str


async def detect_ads(
    topic_context: TopicContext,
    transcript: Transcript,
    cfg: AppConfig,
) -> tuple[list[AdSegment], float]:
    """Detect ad segments in the transcript using LLM with chunking.

    Returns (segments, total_cost_usd).
    """
    chunks = _create_chunks(
        transcript, cfg.ad_detection.chunk_duration_sec, cfg.ad_detection.chunk_overlap_sec
    )
    if not chunks:
        return [], 0.0

    logger.info("Detecting ads in %d chunks", len(chunks))

    results: list[list[AdSegment]] = [[] for _ in chunks]
    costs: list[float] = [0.0] * len(chunks)

    async with asyncio.TaskGroup() as tg:
        for i, chunk in enumerate(chunks):
            tg.create_task(_detect_chunk(chunk, topic_context, cfg, results, costs, i))

    all_segments = [s for batch in results for s in batch]
    return all_segments, sum(costs)


async def _detect_chunk(
    chunk: TranscriptChunk,
    topic_context: TopicContext,
    cfg: AppConfig,
    results: list[list[AdSegment]],
    costs: list[float],
    index: int,
) -> None:
    context_str = f"Domain: {topic_context.domain}, Topic: {topic_context.topic}, Hosts: {', '.join(topic_context.hosts)}"

    messages = [
        {"role": "system", "content": AD_DETECTION_PROMPT},
        {
            "role": "user",
            "content": f"Episode context: {context_str}\n\nTranscript (timestamps in seconds):\n<transcript>{chunk.text}</transcript>",
        },
    ]

    async with _LLM_SEMAPHORE:
        try:
            response, cost = await complete(messages, cfg.interpretation)
            costs[index] = cost
            segments = _parse_ad_segments(response, chunk.episode_guid)
            results[index] = segments
        except Exception as exc:
            logger.warning("Chunk %d failed: %s", index, exc)
            results[index] = []


def _parse_ad_segments(response: str, episode_guid: str) -> list[AdSegment]:
    """Parse LLM response into AdSegment list."""
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON response: %s", response[:200])
        return []

    segments = []
    for item in data:
        try:
            segments.append(
                AdSegment(
                    episode_guid=episode_guid,
                    start_ms=int(item.get("start_sec", 0) * 1000),
                    end_ms=int(item.get("end_sec", 0) * 1000),
                    confidence=float(item.get("confidence", 0)),
                    reason=item.get("reason", ""),
                    sponsor_name=item.get("sponsor"),
                )
            )
        except (KeyError, ValueError) as exc:
            logger.warning("Failed to parse ad segment: %s", exc)
            continue

    return segments


def _create_chunks(
    transcript: Transcript, chunk_duration_sec: int, overlap_sec: int
) -> list[TranscriptChunk]:
    """Split transcript into chunks with overlap."""
    if not transcript.segments:
        return []

    chunks = []
    current_pos = 0

    while current_pos < len(transcript.segments):
        chunk_segments = []
        chunk_start_ms = transcript.segments[current_pos].start_ms
        chunk_end_ms = chunk_start_ms + (chunk_duration_sec * 1000)

        for seg in transcript.segments[current_pos:]:
            if seg.start_ms >= chunk_end_ms:
                break
            chunk_segments.append(seg)

        if not chunk_segments:
            break

        text = " ".join(seg.text for seg in chunk_segments)
        chunks.append(
            TranscriptChunk(
                episode_guid=transcript.episode_guid,
                start_sec=chunk_start_ms / 1000,
                end_sec=chunk_segments[-1].end_ms / 1000,
                text=text,
            )
        )

        # Advance current_pos to the first segment at or after (chunk_end_ms - overlap_ms).
        # Always advance by at least 1 to guarantee termination.
        next_pos = current_pos + len(chunk_segments)  # default: skip whole chunk
        if overlap_sec > 0:
            overlap_start_ms = chunk_end_ms - (overlap_sec * 1000)
            for j, seg in enumerate(transcript.segments[current_pos + 1:], start=current_pos + 1):
                if seg.start_ms >= overlap_start_ms:
                    next_pos = j
                    break
        current_pos = next_pos

    return chunks


def merge_segments(segments: list[AdSegment], merge_gap_sec: int) -> list[AdSegment]:
    """Merge adjacent ad segments within merge_gap_sec of each other."""
    if not segments:
        return []

    sorted_segments = sorted(segments, key=lambda s: s.start_ms)
    merged = [sorted_segments[0]]

    for seg in sorted_segments[1:]:
        last = merged[-1]
        gap_ms = seg.start_ms - last.end_ms
        if gap_ms <= merge_gap_sec * 1000:
            new_end = max(last.end_ms, seg.end_ms)
            merged[-1] = AdSegment(
                episode_guid=last.episode_guid,
                start_ms=last.start_ms,
                end_ms=new_end,
                confidence=max(last.confidence, seg.confidence),
                reason=last.reason,
                sponsor_name=last.sponsor_name or seg.sponsor_name,
            )
        else:
            merged.append(seg)

    return merged
