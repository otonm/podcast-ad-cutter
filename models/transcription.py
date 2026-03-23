"""Dataclasses for transcription results and cost tracking."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Transcription:
    """Full transcription text for a single episode.

    Args:
        guid: Episode GUID, used as the foreign key in ``transcriptions``.
        text: The complete transcription text returned by the STT model.

    """

    guid: str
    text: str


@dataclass
class TranscriptionSegment:
    """A single timestamped segment within an episode transcription.

    Args:
        guid: Episode GUID, used as the foreign key in ``transcription_segments``.
        start_ms: Segment start time in milliseconds.
        end_ms: Segment end time in milliseconds.
        text: Transcribed text for this segment.

    """

    guid: str
    start_ms: int
    end_ms: int
    text: str


@dataclass
class TranscriptionCost:
    """API cost record for a single transcription call.

    Args:
        provider: Provider name, e.g. ``"groq"`` or ``"openai"``.
        model: Model name, e.g. ``"whisper-large-v3-turbo"``.
        cost: Cost in USD, as reported by litellm.

    """

    provider: str
    model: str
    cost: float
