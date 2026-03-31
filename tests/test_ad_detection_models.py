"""Tests for ad detection dataclasses."""

from __future__ import annotations

from models.ad_detection import AdDetectionCost, AdSegment, AdSegmentDetection


def test_ad_segment_detection_fields() -> None:
    d = AdSegmentDetection(indices=[3, 4, 5], confidence=0.92, sponsor="Acme", ad_topic="widget app")
    assert d.indices == [3, 4, 5]
    assert d.confidence == 0.92
    assert d.sponsor == "Acme"
    assert d.ad_topic == "widget app"


def test_ad_segment_fields() -> None:
    seg = AdSegment(
        guid="ep-1",
        start_ms=60000,
        end_ms=90000,
        confidence=0.95,
        sponsor="Acme",
        ad_topic="widget app",
        indices=[3, 4, 5],
    )
    assert seg.guid == "ep-1"
    assert seg.start_ms == 60000
    assert seg.end_ms == 90000
    assert seg.confidence == 0.95
    assert seg.sponsor == "Acme"
    assert seg.ad_topic == "widget app"
    assert seg.indices == [3, 4, 5]


def test_ad_detection_cost_fields() -> None:
    cost = AdDetectionCost(provider="groq", model="llama-3.3-70b-versatile", cost=0.003)
    assert cost.provider == "groq"
    assert cost.model == "llama-3.3-70b-versatile"
    assert cost.cost == 0.003
