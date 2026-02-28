from pydantic import BaseModel, model_validator


class Segment(BaseModel, frozen=True):
    """A single timed word or phrase from the transcription output."""

    start_ms: int
    end_ms: int
    text: str

    @model_validator(mode="after")
    def end_after_start(self) -> "Segment":
        """Validate that end_ms is strictly greater than start_ms."""
        if self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms {self.end_ms} must be > start_ms {self.start_ms}")
        return self


class Transcript(BaseModel, frozen=True):
    """Full transcript for an episode with all word-level segments."""

    episode_guid: str
    segments: tuple[Segment, ...]
    full_text: str
    language: str
    provider_model: str
