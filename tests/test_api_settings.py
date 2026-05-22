"""Tests for GET /api/v1/settings and PATCH /api/v1/settings endpoints."""

from __future__ import annotations

import os
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
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
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
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
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
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
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
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/settings")
            body = await resp.json()
            assert isinstance(body["paths"]["data_dir"], str)
            assert isinstance(body["paths"]["output_dir"], str)


class TestPatchSettings:
    async def test_patch_deep_merges_nested_field(self, tmp_path, monkeypatch) -> None:
        """PATCH updates only the patched nested field, leaving others intact."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/v1/settings", json={"ad_detection": {"min_confidence": 0.9}})
            assert resp.status == 200
            body = await resp.json()
            assert body["ad_detection"]["min_confidence"] == 0.9
            # Other fields in ad_detection should remain
            assert body["ad_detection"]["min_duration"] == 10000

    async def test_patch_unknown_key_returns_422(self, tmp_path, monkeypatch) -> None:
        """PATCH with a typo key returns 422 with JSON error body."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch(
                "/api/v1/settings", json={"ad_detecgion": {"min_confidence": 0.9}}
            )
            assert resp.status == 422
            body = await resp.json()
            assert isinstance(body, (dict, list))  # JSON error info

    async def test_patch_feeds_key_stripped_from_payload(self, tmp_path, monkeypatch) -> None:
        """PATCH ignores the feeds key and does not mutate feeds on disk."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch(
                "/api/v1/settings",
                json={
                    "feeds": [{"title": "Hijack", "url": "https://x", "enabled": True, "episodes_to_keep": 5}],
                    "ad_detection": {"min_confidence": 0.7},
                },
            )
            assert resp.status == 200
        # On-disk feeds should be unchanged
        import yaml as _yaml
        with config_path.open() as f:
            on_disk = _yaml.safe_load(f)
        assert on_disk["feeds"][0]["title"] == "Test Podcast"

    async def test_patch_invalid_field_type_returns_422(self, tmp_path, monkeypatch) -> None:
        """PATCH with wrong type returns 422."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch(
                "/api/v1/settings", json={"ad_detection": {"min_confidence": "not-a-number"}}
            )
            assert resp.status == 422
            body = await resp.json()
            assert isinstance(body, (dict, list))

    async def test_patch_config_roundtrips_after_write(self, tmp_path, monkeypatch) -> None:
        """After PATCH, config.yaml round-trips through yaml+AppConfig without error, no .tmp files remain."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.patch("/api/v1/settings", json={"ad_detection": {"min_confidence": 0.8}})
            assert resp.status == 200
        import yaml as _yaml

        from config.config_loader import AppConfig
        with config_path.open() as f:
            on_disk = _yaml.safe_load(f)
        AppConfig.model_validate(on_disk)  # must not raise
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    async def test_patch_get_reflects_change(self, tmp_path, monkeypatch) -> None:
        """GET after PATCH returns updated values from disk."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            await client.patch("/api/v1/settings", json={"ad_detection": {"min_confidence": 0.85}})
            resp = await client.get("/api/v1/settings")
            body = await resp.json()
            assert body["ad_detection"]["min_confidence"] == 0.85

    async def test_patch_os_replace_called_once(self, tmp_path, monkeypatch) -> None:
        """os.replace is called exactly once with the correct arguments."""
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(VALID_YAML)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, config_path.parent / "logs",
        )

        mock_replace = MagicMock(wraps=os.replace)
        with mock_patch("api.routes.settings.os.replace", mock_replace):
            async with TestClient(TestServer(app)) as client:
                resp = await client.patch("/api/v1/settings", json={"ad_detection": {"min_confidence": 0.9}})
                assert resp.status == 200

        mock_replace.assert_called_once()
        _, dst_arg = mock_replace.call_args[0]
        assert dst_arg == config_path
