from enum import StrEnum

from pydantic import BaseModel, Field


class CallType(StrEnum):
    """LLM call categories tracked in the database."""

    TRANSCRIPTION = "transcription"
    TOPIC_EXTRACTION = "topic_extraction"
    AD_DETECTION = "ad_detection"


class LLMCall(BaseModel, frozen=True):
    """A record of a single LLM API call with its cost."""

    episode_guid: str
    call_type: CallType
    model: str
    cost_usd: float = Field(ge=0.0, default=0.0)
