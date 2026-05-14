"""Tests for EventBus — asyncio broadcast event bus."""

from __future__ import annotations

import asyncio

import pytest

from api.event_bus import EventBus, PipelineEvent, PipelineEventType


# ---------------------------------------------------------------------------
# PipelineEventType enum tests
# ---------------------------------------------------------------------------


class TestPipelineEventType:
    def test_all_required_members_present(self) -> None:
        members = {e.name for e in PipelineEventType}
        assert "EPISODE_STAGE_CHANGED" in members
        assert "DOWNLOAD_PROGRESS" in members
        assert "ENCODE_PROGRESS" in members
        assert "RUN_STARTED" in members
        assert "RUN_COMPLETED" in members
        assert "EPISODE_COMPLETED" in members
        assert "EPISODE_FAILED" in members

    def test_seven_members_total(self) -> None:
        assert len(PipelineEventType) == 7

    def test_is_str_enum(self) -> None:
        # StrEnum members are also strings
        assert isinstance(PipelineEventType.EPISODE_STAGE_CHANGED, str)


# ---------------------------------------------------------------------------
# PipelineEvent dataclass tests
# ---------------------------------------------------------------------------


class TestPipelineEvent:
    def test_event_has_type_field(self) -> None:
        event = PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={})
        assert event.type == PipelineEventType.RUN_STARTED

    def test_event_has_payload_field(self) -> None:
        payload = {"episode": "test-ep-1"}
        event = PipelineEvent(type=PipelineEventType.EPISODE_COMPLETED, payload=payload)
        assert event.payload == payload


# ---------------------------------------------------------------------------
# EventBus.subscribe tests
# ---------------------------------------------------------------------------


class TestEventBusSubscribe:
    async def test_subscribe_returns_asyncio_queue(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    async def test_each_subscribe_returns_different_queue(self) -> None:
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        assert q1 is not q2


# ---------------------------------------------------------------------------
# EventBus.unsubscribe tests
# ---------------------------------------------------------------------------


class TestEventBusUnsubscribe:
    async def test_unsubscribe_removes_queue(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        # After unsubscribe, emitting should not put anything in the removed queue
        bus.emit(PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={}))
        assert q.empty()

    async def test_unsubscribe_nonexistent_raises(self) -> None:
        bus = EventBus()
        q: asyncio.Queue[PipelineEvent] = asyncio.Queue()
        with pytest.raises(ValueError):
            bus.unsubscribe(q)


# ---------------------------------------------------------------------------
# EventBus.emit tests
# ---------------------------------------------------------------------------


class TestEventBusEmit:
    async def test_emit_delivers_event_to_subscriber(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        event = PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"run": 1})
        bus.emit(event)
        received = q.get_nowait()
        assert received is event

    async def test_emit_delivers_to_all_subscribers(self) -> None:
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        event = PipelineEvent(type=PipelineEventType.RUN_COMPLETED, payload={})
        bus.emit(event)
        assert q1.get_nowait() is event
        assert q2.get_nowait() is event

    async def test_emit_with_no_subscribers_is_noop(self) -> None:
        bus = EventBus()
        # Should not raise any exception
        bus.emit(PipelineEvent(type=PipelineEventType.EPISODE_FAILED, payload={}))

    async def test_emit_multiple_events_in_order(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        e1 = PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"n": 1})
        e2 = PipelineEvent(type=PipelineEventType.RUN_COMPLETED, payload={"n": 2})
        bus.emit(e1)
        bus.emit(e2)
        assert q.get_nowait() is e1
        assert q.get_nowait() is e2
