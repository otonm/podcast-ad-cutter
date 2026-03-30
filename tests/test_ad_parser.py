"""Tests for AdParser."""

from __future__ import annotations

import pytest

from components.ad_parser import AdParser
from models.ad_detection import AdSegment, AdSegmentDetection
from models.transcription import TranscriptionSegment


def _seg(i: int, start: int, end: int) -> TranscriptionSegment:
    return TranscriptionSegment(guid="ep-1", start_ms=start, end_ms=end, text=f"seg {i}")


def _det(index: int, confidence: float = 0.9, sponsor: str = "Acme", ad_topic: str = "widgets") -> AdSegmentDetection:
    return AdSegmentDetection(index=index, confidence=confidence, sponsor=sponsor, ad_topic=ad_topic)


SEGMENTS = [
    _seg(0, 0, 4500),
    _seg(1, 4500, 18000),
    _seg(2, 18000, 35000),
    _seg(3, 35000, 42000),
    _seg(4, 42000, 60000),
]


def test_parse_empty_detections_returns_empty() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [], SEGMENTS)
    assert result == []


def test_parse_single_detection() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [_det(1)], SEGMENTS)
    assert len(result) == 1
    seg = result[0]
    assert isinstance(seg, AdSegment)
    assert seg.guid == "ep-1"
    assert seg.start_ms == 4500
    assert seg.end_ms == 18000


def test_parse_consecutive_detections_merged() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [_det(1), _det(2)], SEGMENTS)
    assert len(result) == 1
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 35000


def test_parse_non_consecutive_detections_separate() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [_det(1), _det(3)], SEGMENTS)
    assert len(result) == 2
    assert result[0].start_ms == 4500
    assert result[1].start_ms == 35000


def test_parse_mixed_consecutive_and_gaps() -> None:
    parser = AdParser()
    # indexes 1,2 consecutive; 4 separate
    result = parser.parse("ep-1", [_det(1), _det(2), _det(4)], SEGMENTS)
    assert len(result) == 2
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 35000
    assert result[1].start_ms == 42000
    assert result[1].end_ms == 60000


def test_parse_confidence_average() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [_det(1, 0.8), _det(2, 0.6)], SEGMENTS)
    assert result[0].confidence == pytest.approx(0.7)


def test_parse_sponsor_from_first_in_group() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [
        _det(1, sponsor="FirstSponsor", ad_topic="topic-a"),
        _det(2, sponsor="SecondSponsor", ad_topic="topic-b"),
    ], SEGMENTS)
    assert result[0].sponsor == "FirstSponsor"
    assert result[0].ad_topic == "topic-a"


def test_parse_guid_propagated() -> None:
    parser = AdParser()
    result = parser.parse("ep-99", [_det(0)], SEGMENTS)
    assert result[0].guid == "ep-99"


def test_parse_out_of_range_index_skipped() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [_det(999)], SEGMENTS)
    assert result == []


def test_parse_start_ms_is_min_of_group() -> None:
    parser = AdParser()
    # Detections in reverse order — parser should sort by index
    result = parser.parse("ep-1", [_det(2), _det(1)], SEGMENTS)
    assert len(result) == 1
    assert result[0].start_ms == 4500  # segment 1 starts at 4500


def test_parse_end_ms_is_max_of_group() -> None:
    parser = AdParser()
    result = parser.parse("ep-1", [_det(1), _det(2)], SEGMENTS)
    assert result[0].end_ms == 35000  # segment 2 ends at 35000
