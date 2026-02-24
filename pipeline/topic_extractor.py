import json
import logging

from config.config_loader import AppConfig
from db.repositories.llm_call_repo import LLMCallRepository
from models.ad_segment import TopicContext
from models.llm_call import CallType, LLMCall
from models.transcript import Transcript
from pipeline.llm_client import complete

logger = logging.getLogger(__name__)

TOPIC_EXTRACTION_PROMPT = """Analyze the opening of this podcast transcript.
Return only a JSON object — no markdown, no preamble.
Schema: {"domain": str, "topic": str, "hosts": list[str], "notes": str}"""


async def extract_topic(
    transcript: Transcript,
    cfg: AppConfig,
    db,
) -> TopicContext | None:
    """Extract topic context from the transcript using LLM."""
    words = transcript.full_text.split()[: cfg.interpretation.topic_excerpt_words]
    excerpt = " ".join(words)

    messages = [
        {"role": "system", "content": TOPIC_EXTRACTION_PROMPT},
        {"role": "user", "content": f"<transcript>{excerpt}</transcript>"},
    ]

    try:
        response, cost = await complete(messages, cfg.interpretation)
    except Exception as exc:
        logger.warning("Topic extraction failed: %s", exc)
        return None

    llm_repo = LLMCallRepository(db)
    await llm_repo.save(
        LLMCall(
            episode_guid=transcript.episode_guid,
            call_type=CallType.TOPIC_EXTRACTION,
            model=cfg.interpretation.provider_model,
            cost_usd=cost,
        )
    )

    try:
        data = json.loads(response)
        topic = TopicContext(
            domain=data.get("domain", "unknown"),
            topic=data.get("topic", "unknown"),
            hosts=tuple(data.get("hosts", [])),
            notes=data.get("notes", ""),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("Failed to parse topic context: %s", exc)
        return None

    await _save_topic_context(topic, transcript.episode_guid, db)
    logger.info("Topic extracted: domain=%s topic=%s", topic.domain, topic.topic)
    return topic


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
