import json
import logging

import aiosqlite

from config.config_loader import AppConfig
from db.repositories.llm_call_repo import LLMCallRepository
from db.repositories.topic_context_repo import TopicContextRepository
from models.ad_segment import TopicContext
from models.llm_call import CallType, LLMCall
from models.transcript import Transcript
from pipeline.llm_client import append_json_correction, complete

logger = logging.getLogger(__name__)

_MAX_PARSE_RETRIES: int = 3


async def extract_topic(
    transcript: Transcript,
    cfg: AppConfig,
    db: aiosqlite.Connection,
) -> TopicContext | None:
    """Extract topic context from the transcript using LLM."""
    repo = TopicContextRepository(db)
    if cached := await repo.get_by_episode_guid(transcript.episode_guid):
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
            response, cost = await complete(
                messages, cfg.interpretation, response_format={"type": "json_object"}
            )
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
                messages = append_json_correction(messages, response, schema_hint="object")
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
        await repo.save(topic, episode_guid=transcript.episode_guid)
        logger.info(f"Topic extracted: domain={topic.domain} topic={topic.topic}")
        return topic

    return None
