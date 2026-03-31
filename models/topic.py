"""Dataclasses for topic extraction results and cost tracking."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


class TopicExtractionSchema(BaseModel):
    """LLM response schema for topic extraction."""

    topic: str
    hosts: str
    show: str


@dataclass
class TopicExtraction:
    """Topic and metadata extracted from a podcast episode transcription.

    Args:
        guid: Episode GUID, used as the foreign key in ``topic_extractions``.
        podcast: Config feed title — the logical identifier for the feed.
        title: Episode title.
        topic: ~3-sentence description of the main topic.
        hosts: Comma-separated host names as identified in the transcript.
        show: Show name as identified in the transcript.

    """

    guid: str
    podcast: str
    title: str
    topic: str
    hosts: str
    show: str


@dataclass
class TopicExtractionCost:
    """API cost record for a single topic extraction LLM call.

    Args:
        provider: Provider name, e.g. ``"openai"`` or ``"groq"``.
        model: Model name, e.g. ``"gpt-4o-mini"``.
        cost: Cost in USD, as reported by litellm.

    """

    provider: str
    model: str
    cost: float
