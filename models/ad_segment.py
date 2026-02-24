from pydantic import BaseModel, Field


class TopicContext(BaseModel, frozen=True):
    domain: str
    topic: str
    hosts: tuple[str, ...]
    notes: str


class AdSegment(BaseModel, frozen=True):
    episode_guid: str
    start_ms: int
    end_ms: int
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    sponsor_name: str | None = None
    was_cut: bool = False
