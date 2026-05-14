"""Tests for GET /api/v1/health."""

from __future__ import annotations

import time

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.server import create_app

# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_returns_200(self) -> None:
        app = create_app(EventBus(), time.monotonic())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status == 200

    async def test_health_response_has_status_ok(self) -> None:
        app = create_app(EventBus(), time.monotonic())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert data["status"] == "ok"

    async def test_health_response_uptime_is_float(self) -> None:
        app = create_app(EventBus(), time.monotonic())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert isinstance(data["uptime_seconds"], float)

    async def test_health_response_version_is_nonempty_str(self) -> None:
        app = create_app(EventBus(), time.monotonic())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert isinstance(data["version"], str)
            assert len(data["version"]) > 0

    async def test_health_response_has_all_expected_keys(self) -> None:
        app = create_app(EventBus(), time.monotonic())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert set(data.keys()) == {"status", "uptime_seconds", "version"}
