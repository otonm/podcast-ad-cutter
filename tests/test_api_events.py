"""Tests for GET /api/v1/events SSE endpoint."""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus, PipelineEvent, PipelineEventType
from api.server import create_app


class TestSSERouteBasics:
    async def test_events_route_returns_200_with_event_stream_content_type(self) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic())
        async with TestClient(TestServer(app)) as client:
            bus.emit(PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"feeds": [], "total_episodes": 0}))
            async with client.get("/api/v1/events") as resp:
                assert resp.status == 200
                assert "text/event-stream" in resp.headers["Content-Type"]

    async def test_events_route_sets_cache_and_buffering_headers(self) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic())
        async with TestClient(TestServer(app)) as client:
            bus.emit(PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"feeds": [], "total_episodes": 0}))
            async with client.get("/api/v1/events") as resp:
                assert resp.headers["Cache-Control"] == "no-cache"
                assert resp.headers["X-Accel-Buffering"] == "no"

    async def test_events_route_registered_on_app(self) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic())
        async with TestClient(TestServer(app)) as client:
            bus.emit(PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"feeds": [], "total_episodes": 0}))
            async with client.get("/api/v1/events") as resp:
                assert resp.status != 404

    async def test_events_route_delivers_no_event_when_bus_is_idle(self) -> None:
        bus = EventBus()
        app = create_app(bus, time.monotonic())
        async with TestClient(TestServer(app)) as client:
            async with client.get("/api/v1/events") as resp:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(resp.content.read(1), timeout=0.1)
