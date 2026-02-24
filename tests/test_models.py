import pytest
from pydantic import ValidationError


def test_episode_creation():
    from models.episode import Episode

    ep = Episode(
        guid="abc-123",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    assert ep.guid == "abc-123"
    assert ep.duration_seconds is None


def test_episode_is_frozen():
    from models.episode import Episode

    ep = Episode(
        guid="abc-123",
        feed_title="Test Pod",
        title="Episode 1",
        audio_url="https://example.com/ep1.mp3",
        published="2025-01-01T00:00:00Z",
    )
    with pytest.raises(ValidationError):
        ep.guid = "new"  # type: ignore[misc]


def test_segment_valid():
    from models.transcript import Segment

    seg = Segment(start_ms=0, end_ms=1000, text="hello")
    assert seg.start_ms == 0
    assert seg.end_ms == 1000


def test_segment_end_before_start_raises():
    from models.transcript import Segment

    with pytest.raises(ValidationError):
        Segment(start_ms=1000, end_ms=500, text="bad")


def test_segment_equal_start_end_raises():
    from models.transcript import Segment

    with pytest.raises(ValidationError):
        Segment(start_ms=1000, end_ms=1000, text="bad")


def test_transcript_creation():
    from models.transcript import Segment, Transcript

    t = Transcript(
        episode_guid="abc-123",
        segments=(Segment(start_ms=0, end_ms=1000, text="hello"),),
        full_text="hello",
        language="en",
        provider_model="whisper-1",
    )
    assert len(t.segments) == 1


def test_ad_segment_valid():
    from models.ad_segment import AdSegment

    ad = AdSegment(
        episode_guid="abc-123",
        start_ms=60000,
        end_ms=120000,
        confidence=0.9,
        reason="Promo code mentioned",
        sponsor_name="Acme",
    )
    assert ad.was_cut is False


def test_ad_segment_confidence_out_of_range():
    from models.ad_segment import AdSegment

    with pytest.raises(ValidationError):
        AdSegment(
            episode_guid="abc-123",
            start_ms=0,
            end_ms=1000,
            confidence=1.5,
            reason="bad",
        )


def test_ad_segment_confidence_negative():
    from models.ad_segment import AdSegment

    with pytest.raises(ValidationError):
        AdSegment(
            episode_guid="abc-123",
            start_ms=0,
            end_ms=1000,
            confidence=-0.1,
            reason="bad",
        )


def test_ad_segment_end_before_start_raises():
    from models.ad_segment import AdSegment

    with pytest.raises(ValidationError):
        AdSegment(episode_guid="g", start_ms=1000, end_ms=500, confidence=0.9, reason="ad")


def test_ad_segment_equal_timestamps_raises():
    from models.ad_segment import AdSegment

    with pytest.raises(ValidationError):
        AdSegment(episode_guid="g", start_ms=1000, end_ms=1000, confidence=0.9, reason="ad")


def test_topic_context_creation():
    from models.ad_segment import TopicContext

    tc = TopicContext(
        domain="technology",
        topic="Rust programming",
        hosts=("Alice", "Bob"),
        notes="Weekly deep dive",
    )
    assert tc.hosts == ("Alice", "Bob")
