"""Tests for control endpoints — GET /api/v1/status."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import MagicMock

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = []
    return cfg


class TestStatus:
    async def test_status_returns_200_when_idle(self) -> None:
        run_state = RunState()
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "idle"
            assert data["started_at"] is None
            assert data["active_feed_slug"] is None
            assert data["current_episode_guid"] is None
            assert data["feeds"] == {}

    async def test_status_returns_running_when_state_set(self) -> None:
        run_state = RunState()
        run_state.state = "running"
        run_state.started_at = datetime.now(UTC)
        run_state.active_feed_slug = "my-show"
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "running"
            assert isinstance(data["started_at"], str)
            assert data["active_feed_slug"] == "my-show"

    async def test_status_returns_stopping_state(self) -> None:
        run_state = RunState()
        run_state.state = "stopping"
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "stopping"
