"""Tests for GET /api/v1/health."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.routes.health import _read_version
from api.run_state import RunState
from api.server import create_app

# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = []
    return cfg


class TestHealthEndpoint:
    async def test_health_returns_200(self, tmp_path) -> None:
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status == 200

    async def test_health_response_has_status_ok(self, tmp_path) -> None:
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert data["status"] == "ok"

    async def test_health_response_uptime_is_float(self, tmp_path) -> None:
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert isinstance(data["uptime_seconds"], float)

    async def test_health_response_version_is_nonempty_str(self, tmp_path) -> None:
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert isinstance(data["version"], str)
            assert len(data["version"]) > 0

    async def test_health_response_has_all_expected_keys(self, tmp_path) -> None:
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            data = await resp.json()
            assert set(data.keys()) == {"status", "uptime_seconds", "version"}


class TestReadVersion:
    def test_returns_unknown_when_pyproject_missing(self) -> None:
        import importlib.metadata

        with (
            patch.object(
                importlib.metadata,
                "version",
                side_effect=importlib.metadata.PackageNotFoundError,
            ),
            patch("api.routes.health.Path.open", side_effect=FileNotFoundError),
        ):
            assert _read_version() == "unknown"

    def test_returns_unknown_when_version_key_missing(self) -> None:
        import importlib.metadata
        import io

        toml_bytes = b"[project]\nname = 'test'\n"

        with (
            patch.object(
                importlib.metadata,
                "version",
                side_effect=importlib.metadata.PackageNotFoundError,
            ),
            patch("api.routes.health.Path.open", return_value=io.BytesIO(toml_bytes)),
            patch("api.routes.health.tomllib.load", return_value={"project": {}}),
        ):
            assert _read_version() == "unknown"
