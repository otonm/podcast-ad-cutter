"""Tests for GET /api/v1/events SSE endpoint."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus, PipelineEvent, PipelineEventType
from api.run_state import RunState
from api.server import create_app


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = []
    return cfg


class TestSSERouteBasics:
    async def test_events_route_returns_200_with_event_stream_content_type(self, tmp_path) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml", tmp_path / "logs")
        async with TestClient(TestServer(app)) as client:
            bus.emit(PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"feeds": [], "total_episodes": 0}))
            async with client.get("/api/v1/events") as resp:
                assert resp.status == 200
                assert "text/event-stream" in resp.headers["Content-Type"]

    async def test_events_route_sets_cache_and_buffering_headers(self, tmp_path) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml", tmp_path / "logs")
        async with TestClient(TestServer(app)) as client:
            bus.emit(PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"feeds": [], "total_episodes": 0}))
            async with client.get("/api/v1/events") as resp:
                assert resp.headers["Cache-Control"] == "no-cache"
                assert resp.headers["X-Accel-Buffering"] == "no"

    async def test_events_route_registered_on_app(self, tmp_path) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml", tmp_path / "logs")
        async with TestClient(TestServer(app)) as client:
            bus.emit(PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"feeds": [], "total_episodes": 0}))
            async with client.get("/api/v1/events") as resp:
                assert resp.status != 404

    async def test_events_route_delivers_no_event_when_bus_is_idle(self, tmp_path) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml", tmp_path / "logs")
        async with TestClient(TestServer(app)) as client:
            async with client.get("/api/v1/events") as resp:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(resp.content.read(1), timeout=0.1)


class TestSSEEventDelivery:
    async def test_events_route_delivers_event_payload(self, tmp_path) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml", tmp_path / "logs")
        async with TestClient(TestServer(app)) as client:
            async with client.get("/api/v1/events") as resp:
                bus.emit(PipelineEvent(
                    type=PipelineEventType.RUN_STARTED,
                    payload={"feeds": ["slug-a"], "total_episodes": 1},
                ))
                chunk = await asyncio.wait_for(resp.content.read(1024), timeout=1.0)
                text = chunk.decode()
                assert "run.started" in text
                assert "slug-a" in text

    async def test_events_route_unsubscribes_on_disconnect(self, tmp_path) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml", tmp_path / "logs")
        async with TestClient(TestServer(app)) as client:
            async with client.get("/api/v1/events"):
                pass
            # Allow the handler task to observe cancellation and run the finally block.
            for _ in range(10):
                await asyncio.sleep(0)
            assert len(bus._subscribers) == 0

    async def test_events_route_supports_multiple_concurrent_clients(self, tmp_path) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml", tmp_path / "logs")
        async with TestClient(TestServer(app)) as client:
            async with client.get("/api/v1/events") as resp1:
                async with client.get("/api/v1/events") as resp2:
                    assert len(bus._subscribers) == 2
                    bus.emit(PipelineEvent(
                        type=PipelineEventType.RUN_STARTED,
                        payload={"feeds": ["slug-b"], "total_episodes": 2},
                    ))
                    chunk1 = await asyncio.wait_for(resp1.content.read(1024), timeout=1.0)
                    chunk2 = await asyncio.wait_for(resp2.content.read(1024), timeout=1.0)
                    assert "run.started" in chunk1.decode()
                    assert "run.started" in chunk2.decode()
