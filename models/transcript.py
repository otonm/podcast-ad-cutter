from pydantic import BaseModel, model_validator


class Segment(BaseModel, frozen=True):
    start_ms: int
    end_ms: int
    text: str

    @model_validator(mode="after")
    def end_after_start(self) -> "Segment":
        if self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms {self.end_ms} must be > start_ms {self.start_ms}")
        return self


class Transcript(BaseModel, frozen=True):
    episode_guid: str
    segments: tuple[Segment, ...]
    full_text: str
    language: str
    provider_model: str
