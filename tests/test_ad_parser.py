"""Tests for AdParser."""

from __future__ import annotations

import pytest

from components.ad_parser import AdParser
from models.ad_detection import AdSegment, CutRange


def _seg(
    start: int,
    end: int,
    confidence: float = 0.9,
    sponsor: str = "Acme",
) -> AdSegment:
    return AdSegment(
        guid="ep-1", start_ms=start, end_ms=end,
        confidence=confidence, sponsor=sponsor, ad_topic="widgets",
        indices=[0, 1],
    )


def test_parse_empty_detections_returns_empty() -> None:
    parser = AdParser()
    assert parser.parse([], 0, 0.0) == []


def test_parse_single_detection() -> None:
    parser = AdParser()
    result = parser.parse([_seg(4500, 18000)], min_duration_ms=0, min_confidence=0.0)
    assert len(result) == 1
    assert isinstance(result[0], CutRange)
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 18000


def test_parse_consecutive_detections_merged() -> None:
    # gap = 18000 - 18000 = 0ms ≤ 10s → merge
    parser = AdParser()
    result = parser.parse(
        [_seg(4500, 18000), _seg(18000, 35000)],
        min_duration_ms=0, min_confidence=0.0,
    )
    assert len(result) == 1
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 35000


# ---------------------------------------------------------------------------
# Time-gap boundary tests
# ---------------------------------------------------------------------------

def test_parse_gap_within_threshold_merges_all() -> None:
    # gaps: 0ms, 7000ms — both ≤ 10s → all merge into one
    parser = AdParser()
    result = parser.parse(
        [_seg(4500, 18000), _seg(18000, 35000), _seg(42000, 60000)],
        min_duration_ms=0, min_confidence=0.0,
    )
    assert len(result) == 1
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 60000


def test_parse_gap_exceeds_threshold_stays_separate() -> None:
    # gap = 35000 - 18000 = 17000ms > 10s → 2 groups
    parser = AdParser()
    result = parser.parse(
        [_seg(4500, 18000), _seg(35000, 42000)],
        min_duration_ms=0, min_confidence=0.0,
    )
    assert len(result) == 2
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 18000
    assert result[1].start_ms == 35000
    assert result[1].end_ms == 42000


def test_parse_gap_within_threshold_merges() -> None:
    # gap = 42000 - 35000 = 7000ms ≤ 10s → 1 group
    parser = AdParser()
    result = parser.parse(
        [_seg(18000, 35000), _seg(42000, 60000)],
        min_duration_ms=0, min_confidence=0.0,
    )
    assert len(result) == 1
    assert result[0].start_ms == 18000
    assert result[0].end_ms == 60000


def test_parse_gap_exactly_at_threshold_merges() -> None:
    # gap = 15000 - 5000 = 10000ms exactly → merge
    parser = AdParser()
    result = parser.parse(
        [_seg(0, 5000), _seg(15000, 25000)],
        min_duration_ms=0, min_confidence=0.0,
    )
    assert len(result) == 1
    assert result[0].start_ms == 0
    assert result[0].end_ms == 25000


def test_parse_gap_one_ms_over_threshold_stays_separate() -> None:
    # gap = 15001 - 5000 = 10001ms > 10s → separate
    parser = AdParser()
    result = parser.parse(
        [_seg(0, 5000), _seg(15001, 25001)],
        min_duration_ms=0, min_confidence=0.0,
    )
    assert len(result) == 2


# ---------------------------------------------------------------------------
# min_duration_ms filter tests
# ---------------------------------------------------------------------------

def test_parse_min_duration_filters_short_group() -> None:
    # 4500ms duration < 5000ms min → discarded
    parser = AdParser()
    assert parser.parse([_seg(0, 4500)], min_duration_ms=5000, min_confidence=0.0) == []


def test_parse_min_duration_keeps_qualifying_group() -> None:
    # 13500ms >= 5000ms → kept
    parser = AdParser()
    result = parser.parse([_seg(4500, 18000)], min_duration_ms=5000, min_confidence=0.0)
    assert len(result) == 1
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 18000


def test_parse_min_duration_zero_keeps_all() -> None:
    parser = AdParser()
    result = parser.parse([_seg(0, 4500)], min_duration_ms=0, min_confidence=0.0)
    assert len(result) == 1


def test_parse_min_duration_filters_some_groups() -> None:
    # gap = 18000 - 4500 = 13500ms > 10s → 2 groups
    # group 0: 4500ms < 10000ms → discarded
    # group 1: 17000ms >= 10000ms → kept
    parser = AdParser()
    result = parser.parse(
        [_seg(0, 4500), _seg(18000, 35000)],
        min_duration_ms=10000, min_confidence=0.0,
    )
    assert len(result) == 1
    assert result[0].start_ms == 18000
    assert result[0].end_ms == 35000


# ---------------------------------------------------------------------------
# Confidence filter tests (new responsibility)
# ---------------------------------------------------------------------------

def test_parse_below_confidence_threshold_filtered() -> None:
    parser = AdParser()
    result = parser.parse(
        [_seg(0, 30000, confidence=0.5)],
        min_duration_ms=0, min_confidence=0.7,
    )
    assert result == []


def test_parse_above_confidence_threshold_kept() -> None:
    parser = AdParser()
    result = parser.parse(
        [_seg(0, 30000, confidence=0.9)],
        min_duration_ms=0, min_confidence=0.7,
    )
    assert len(result) == 1
    assert result[0] == CutRange(start_ms=0, end_ms=30000)


def test_parse_mixed_confidence_filters_partial() -> None:
    # Low-confidence seg filtered before grouping
    # After filter: only seg(20000, 40000) remains → 1 CutRange
    parser = AdParser()
    result = parser.parse(
        [_seg(0, 15000, confidence=0.5), _seg(20000, 40000, confidence=0.9)],
        min_duration_ms=0, min_confidence=0.7,
    )
    assert len(result) == 1
    assert result[0].start_ms == 20000
    assert result[0].end_ms == 40000


def test_parse_unsorted_input_sorted_correctly() -> None:
    # Input out of order — parse must sort by start_ms
    parser = AdParser()
    result = parser.parse(
        [_seg(18000, 35000), _seg(4500, 18000)],
        min_duration_ms=0, min_confidence=0.0,
    )
    assert len(result) == 1
    assert result[0].start_ms == 4500
    assert result[0].end_ms == 35000


def test_parse_confidence_at_exact_threshold_kept() -> None:
    parser = AdParser()
    result = parser.parse(
        [_seg(0, 30000, confidence=0.7)],
        min_duration_ms=0, min_confidence=0.7,
    )
    assert len(result) == 1


def test_parse_returns_cut_range_not_ad_segment() -> None:
    parser = AdParser()
    result = parser.parse([_seg(0, 30000)], min_duration_ms=0, min_confidence=0.0)
    assert isinstance(result[0], CutRange)
    assert not hasattr(result[0], "confidence")
    assert not hasattr(result[0], "sponsor")


@pytest.mark.parametrize("confidence", [0.69, 0.0])
def test_parse_confidence_below_threshold_filtered_parametrized(confidence: float) -> None:
    parser = AdParser()
    assert parser.parse(
        [_seg(0, 30000, confidence=confidence)],
        min_duration_ms=0, min_confidence=0.7,
    ) == []
