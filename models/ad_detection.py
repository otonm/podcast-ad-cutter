"""Ad detection dataclasses and LLM response schemas."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


class AdSegmentDetectionSchema(BaseModel):
    """Single ad segment as returned by the LLM."""

    index: int
    confidence: float
    sponsor: str
    ad_topic: str


class AdDetectionResponseSchema(BaseModel):
    """Top-level LLM response schema for ad detection."""

    ads: list[AdSegmentDetectionSchema]


@dataclass
class AdSegmentDetection:
    """Raw LLM output for a single segment identified as an advertisement."""

    index: int
    confidence: float
    sponsor: str
    ad_topic: str


@dataclass
class AdSegment:
    """A merged, time-bounded advertisement segment ready for audio editing."""

    guid: str
    start_ms: int
    end_ms: int
    confidence: float
    sponsor: str
    ad_topic: str


@dataclass
class AdDetectionCost:
    """API cost record for one ad detection call."""

    provider: str
    model: str
    cost: float
