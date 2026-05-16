"""Tests for control endpoints — GET /api/v1/status, POST /api/v1/run, etc."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.routes.control import _resolve_slug, _run_pipeline_task
from api.run_state import RunState
from api.server import create_app


def _make_feed(title: str) -> MagicMock:
    feed = MagicMock()
    feed.title = title
    return feed


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = [
        _make_feed("My Show"),
        _make_feed("Another Show"),
    ]
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


class TestStartRun:
    async def test_run_returns_202_when_idle(self) -> None:
        run_state = RunState()
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            with (
                patch("api.routes.control.Pipeline"),
                patch("api.routes.control.asyncio.create_task", return_value=MagicMock()) as mock_create_task,
            ):
                resp = await client.post("/api/v1/run")
                assert resp.status == 202
                data = await resp.json()
                assert data["status"] == "started"
                assert isinstance(data["started_at"], str)
                assert run_state.state == "running"
                assert run_state.task is not None
                assert mock_create_task.called

    async def test_run_returns_409_when_active(self) -> None:
        run_state = RunState()
        run_state.state = "running"
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run")
            assert resp.status == 409
            data = await resp.json()
            assert "error" in data


class TestStopRun:
    async def test_stop_graceful_sets_stop_event(self) -> None:
        run_state = RunState()
        run_state.state = "running"
        run_state.task = MagicMock()
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run/stop")
            assert resp.status == 202
            data = await resp.json()
            assert data["mode"] == "graceful"
            assert run_state.stop_event.is_set()
            assert run_state.state == "stopping"

    async def test_stop_force_cancels_task(self) -> None:
        run_state = RunState()
        run_state.state = "running"
        run_state.task = MagicMock()
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run/stop?force=true")
            assert resp.status == 202
            data = await resp.json()
            assert data["mode"] == "force"
            assert run_state.task.cancel.called

    async def test_stop_returns_409_when_idle(self) -> None:
        run_state = RunState()
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run/stop")
            assert resp.status == 409
            data = await resp.json()
            assert "error" in data


class TestFeedRun:
    async def test_feed_run_resolves_slug_and_returns_202(self) -> None:
        run_state = RunState()
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            with (
                patch("api.routes.control.Pipeline"),
                patch("api.routes.control.asyncio.create_task", return_value=MagicMock()),
            ):
                resp = await client.post("/api/v1/feeds/my-show/run")
                assert resp.status == 202
                data = await resp.json()
                assert data["status"] == "started"
                assert data["feed"] == "my-show"
                assert isinstance(data["started_at"], str)
                assert run_state.active_feed_slug == "my-show"

    async def test_feed_run_unknown_slug_returns_404(self) -> None:
        run_state = RunState()
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/feeds/does-not-exist/run")
            assert resp.status == 404
            data = await resp.json()
            assert "error" in data

    async def test_feed_run_returns_409_when_active(self) -> None:
        run_state = RunState()
        run_state.state = "running"
        app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/feeds/my-show/run")
            assert resp.status == 409
            data = await resp.json()
            assert "error" in data


class TestRunStateLifecycle:
    async def test_finally_resets_state_after_task_completes(self) -> None:
        run_state = RunState()
        run_state.state = "running"

        pipeline = MagicMock()
        pipeline.run = AsyncMock(return_value=[])

        await _run_pipeline_task(pipeline, run_state)

        assert run_state.state == "idle"
        assert run_state.feeds == {}
        assert not run_state.stop_event.is_set()

    async def test_cancelled_error_reraises_after_reset(self) -> None:
        run_state = RunState()
        run_state.state = "running"

        pipeline = MagicMock()
        pipeline.run = AsyncMock(side_effect=asyncio.CancelledError())

        import pytest
        with pytest.raises(asyncio.CancelledError):
            await _run_pipeline_task(pipeline, run_state)

        assert run_state.state == "idle"

    async def test_exception_logs_and_resets_state(self) -> None:
        run_state = RunState()
        run_state.state = "running"

        pipeline = MagicMock()
        pipeline.run = AsyncMock(side_effect=RuntimeError("boom"))

        await _run_pipeline_task(pipeline, run_state)

        assert run_state.state == "idle"


class TestResolveSlug:
    def test_resolves_known_slug(self) -> None:
        feeds = [_make_feed("My Show"), _make_feed("Another Show")]
        assert _resolve_slug("my-show", feeds) == "My Show"
        assert _resolve_slug("another-show", feeds) == "Another Show"

    def test_returns_none_for_unknown_slug(self) -> None:
        feeds = [_make_feed("My Show")]
        assert _resolve_slug("not-found", feeds) is None
