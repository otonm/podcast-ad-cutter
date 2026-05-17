"""Tests for GET /api/v1/settings and PATCH /api/v1/settings endpoints."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app

# Reuse the VALID_YAML constant to avoid duplication
from tests.test_config_loader import VALID_YAML


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = []
    return cfg


class TestGetSettings:
    async def test_get_settings_returns_200_with_all_appconfig_fields(self, tmp_path, monkeypatch) -> None:
        """GET /api/v1/settings returns 200 with all AppConfig fields."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(EventBus(), time.monotonic(), RunState(), _make_config(), config_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/settings")
            assert resp.status == 200
            body = await resp.json()
            for field in ("feeds", "models", "paths", "ad_detection", "output", "log", "base_url"):
                assert field in body
            assert "credentials" in body
            for val in body["credentials"].values():
                assert val == "not set"

    async def test_get_settings_credentials_set_when_env_var_present(self, tmp_path, monkeypatch) -> None:
        """GET /api/v1/settings returns 'set' for credentials when env var is set."""
        monkeypatch.setenv("GROQ_API_KEY", "xyz-secret-marker")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(EventBus(), time.monotonic(), RunState(), _make_config(), config_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/settings")
            assert resp.status == 200
            body = await resp.json()
            assert body["credentials"]["groq_api_key"] == "set"

        async with TestClient(TestServer(app)) as client2:
            resp2 = await client2.get("/api/v1/settings")
            text2 = await resp2.text()
            assert "xyz-secret-marker" not in text2

    async def test_get_settings_rereads_disk_on_each_request(self, tmp_path, monkeypatch) -> None:
        """GET /api/v1/settings re-reads config from disk on every request."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(EventBus(), time.monotonic(), RunState(), _make_config(), config_path)
        async with TestClient(TestServer(app)) as client:
            resp1 = await client.get("/api/v1/settings")
            body1 = await resp1.json()
            assert body1["base_url"] == "http://localhost:8080"

            # Modify the config on disk
            modified = VALID_YAML.replace("http://localhost:8080", "http://localhost:9999")
            config_path.write_text(modified)

            resp2 = await client.get("/api/v1/settings")
            body2 = await resp2.json()
            assert body2["base_url"] == "http://localhost:9999"

    async def test_get_settings_paths_serialize_as_strings(self, tmp_path, monkeypatch) -> None:
        """GET /api/v1/settings returns paths fields as strings, not PosixPath repr."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(EventBus(), time.monotonic(), RunState(), _make_config(), config_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/settings")
            body = await resp.json()
            assert isinstance(body["paths"]["data_dir"], str)
            assert isinstance(body["paths"]["output_dir"], str)
