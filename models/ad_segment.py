from pydantic import BaseModel, Field, model_validator


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

    @model_validator(mode="after")
    def end_after_start(self) -> "AdSegment":
        if self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms {self.end_ms} must be > start_ms {self.start_ms}")
        return self
