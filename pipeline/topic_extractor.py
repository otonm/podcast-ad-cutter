import json
import logging

from config.config_loader import AppConfig
from db.repositories.llm_call_repo import LLMCallRepository
from models.ad_segment import TopicContext
from models.llm_call import CallType, LLMCall
from models.transcript import Transcript
from pipeline.llm_client import complete

logger = logging.getLogger(__name__)

_MAX_PARSE_RETRIES: int = 3


async def _get_topic_context(episode_guid: str, db) -> TopicContext | None:
    """Return a cached TopicContext for this episode, or None if not yet extracted."""
    cursor = await db.execute(
        "SELECT domain, topic, hosts, notes FROM topic_contexts WHERE episode_guid = ?",
        (episode_guid,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return TopicContext(
        domain=row[0],
        topic=row[1],
        hosts=tuple(json.loads(row[2]) if row[2] else []),
        notes=row[3] or "",
    )


async def extract_topic(
    transcript: Transcript,
    cfg: AppConfig,
    db,
) -> TopicContext | None:
    """Extract topic context from the transcript using LLM."""
    cached = await _get_topic_context(transcript.episode_guid, db)
    if cached:
        logger.info(f"Topic context cache hit for {transcript.episode_guid}")
        return cached

    words = transcript.full_text.split()[: cfg.interpretation.topic_excerpt_words]
    excerpt = " ".join(words)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": cfg.prompts.topic_extraction},
        {"role": "user", "content": f"<transcript>{excerpt}</transcript>"},
    ]

    total_cost = 0.0
    response = ""
    for attempt in range(_MAX_PARSE_RETRIES):
        try:
            response, cost = await complete(messages, cfg.interpretation)
            total_cost += cost
        except Exception as exc:
            logger.warning(f"Topic extraction LLM call failed: {exc}")
            return None

        try:
            data = json.loads(response)
            topic = TopicContext(
                domain=data.get("domain", "unknown"),
                topic=data.get("topic", "unknown"),
                hosts=tuple(data.get("hosts", [])),
                notes=data.get("notes", ""),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            if attempt < _MAX_PARSE_RETRIES - 1:
                logger.warning(
                    f"Topic extraction invalid JSON"
                    f" (attempt {attempt + 1}/{_MAX_PARSE_RETRIES}), retrying: {exc}"
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": response},
                    {
                        "role": "user",
                        "content": (
                            "Your response was not valid JSON. Please respond with only a valid"
                            " JSON object, no markdown, no code blocks, no preamble."
                        ),
                    },
                ]
            else:
                logger.warning(
                    f"Topic extraction failed after {_MAX_PARSE_RETRIES} parse attempts: {exc}"
                )
                return None
            continue

        llm_repo = LLMCallRepository(db)
        await llm_repo.save(
            LLMCall(
                episode_guid=transcript.episode_guid,
                call_type=CallType.TOPIC_EXTRACTION,
                model=cfg.interpretation.provider_model,
                cost_usd=total_cost,
            )
        )
        await _save_topic_context(topic, transcript.episode_guid, db)
        logger.info(f"Topic extracted: domain={topic.domain} topic={topic.topic}")
        return topic

    return None


async def _save_topic_context(topic: TopicContext, episode_guid: str, db) -> None:
    """Persist topic context to database."""
    import aiosqlite

    await db.execute(
        "INSERT OR REPLACE INTO topic_contexts (episode_guid, domain, topic, hosts, notes)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            episode_guid,
            topic.domain,
            topic.topic,
            json.dumps(list(topic.hosts)),
            topic.notes,
        ),
    )
    await db.commit()
