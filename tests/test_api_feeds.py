"""Tests for feed management endpoints — GET/POST/PATCH/DELETE /api/v1/feeds."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app
from tests.test_config_loader import VALID_YAML

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TWO_FEEDS_YAML = """\
feeds:
  - title: "Show A"
    url: "https://show-a.example/feed.rss"
    enabled: true
    episodes_to_keep: 5
  - title: "Show B"
    url: "https://show-b.example/feed.rss"
    enabled: false
    episodes_to_keep: 10
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
paths:
  output_dir: "./output"
  cache_dir: "./cache"
  data_dir: "./data"
  log_dir: "./logs"
ad_detection:
  min_duration: 10000
  min_confidence: 0.7
output:
  file_type: "mp3"
  bitrate: "128k"
log:
  level: "ERROR"
  to_file: false
base_url: "http://localhost:8080"
"""


def _make_db_patch(*, counts: dict[str, int] | None = None):
    """Return a context manager that patches Database for feeds handler tests.

    counts: mapping of feed title → COUNT(*) value. Defaults to {} (0 for all).
    """
    if counts is None:
        counts = {}

    mock_db_obj = MagicMock()
    mock_db_cm = MagicMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db_obj)
    mock_db_cm.__aexit__ = AsyncMock(return_value=False)

    async def _execute(sql: str, params: tuple) -> MagicMock:
        cursor = MagicMock()
        title = params[0] if params else ""
        count = counts.get(title, 0)
        cursor.fetchone = AsyncMock(return_value=(count,))
        return cursor

    mock_db_obj.conn.execute = _execute

    import contextlib

    @contextlib.contextmanager
    def _patches():
        with patch("api.routes.feeds.Database", return_value=mock_db_cm) as mock_db_cls:
            yield mock_db_cls

    return _patches()


def _make_app(tmp_path: Path, yaml_content: str = _TWO_FEEDS_YAML) -> tuple[object, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml_content)
    cfg = MagicMock()
    cfg.app.paths.data_dir = tmp_path
    app = create_app(EventBus(), time.monotonic(), RunState(), cfg, config_path)
    return app, config_path


# ---------------------------------------------------------------------------
# GET /api/v1/feeds
# ---------------------------------------------------------------------------

class TestGetFeeds:
    async def test_returns_both_feeds_with_slugs_and_counts(self, tmp_path) -> None:
        """GET /api/v1/feeds returns all feeds with slug/title/url/enabled/episodes_to_keep/episode_count."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch(counts={"Show A": 5, "Show B": 0}):
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/v1/feeds")
                assert resp.status == 200
                data = await resp.json()
                assert len(data) == 2
                show_a = next(f for f in data if f["slug"] == "show-a")
                show_b = next(f for f in data if f["slug"] == "show-b")
                assert show_a["title"] == "Show A"
                assert show_a["url"] == "https://show-a.example/feed.rss"
                assert show_a["enabled"] is True
                assert show_a["episodes_to_keep"] == 5
                assert show_a["episode_count"] == 5
                assert show_b["episode_count"] == 0

    async def test_slug_matches_slugify_of_title(self, tmp_path) -> None:
        """Slug is produced by slugify(feed.title)."""
        # Write explicit yaml with special title
        config_path = tmp_path / "config.yaml"
        yaml_data = {
            "feeds": [
                {"title": "My Cool Show!", "url": "https://x.example/", "enabled": True, "episodes_to_keep": 5},
            ],
            "models": {
                "transcription": {"provider": "groq", "model": "whisper-large-v3"},
                "context_extraction": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
                "ad_detection": {"provider": "groq", "model": "llama-3.3-70b-versatile"},
            },
            "paths": {"output_dir": "./output", "cache_dir": "./cache", "data_dir": "./data", "log_dir": "./logs"},
            "ad_detection": {"min_duration": 10000, "min_confidence": 0.7},
            "output": {"file_type": "mp3", "bitrate": "128k"},
            "log": {"level": "ERROR", "to_file": False},
            "base_url": "http://localhost:8080",
        }
        with config_path.open("w") as f:
            yaml.dump(yaml_data, f)
        cfg = MagicMock()
        cfg.app.paths.data_dir = tmp_path
        app = create_app(EventBus(), time.monotonic(), RunState(), cfg, config_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/v1/feeds")
                assert resp.status == 200
                data = await resp.json()
                assert data[0]["slug"] == "my-cool-show"

    async def test_database_context_entered_once_per_request(self, tmp_path) -> None:
        """Database context manager is entered exactly once per request (not once per feed)."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch(counts={"Show A": 1, "Show B": 2}) as mock_db_cls:
            async with TestClient(TestServer(app)) as client:
                await client.get("/api/v1/feeds")
                # Database() constructor should be called once
                assert mock_db_cls.call_count == 1

    async def test_episode_count_from_db_not_episodes_to_keep(self, tmp_path) -> None:
        """episode_count comes from DB COUNT query, not from episodes_to_keep."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch(counts={"Show A": 99, "Show B": 42}):
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/v1/feeds")
                data = await resp.json()
                show_a = next(f for f in data if f["slug"] == "show-a")
                show_b = next(f for f in data if f["slug"] == "show-b")
                # episodes_to_keep is 5 and 10 — counts must differ
                assert show_a["episode_count"] == 99
                assert show_b["episode_count"] == 42


# ---------------------------------------------------------------------------
# POST /api/v1/feeds
# ---------------------------------------------------------------------------

class TestPostFeed:
    async def test_post_creates_feed_and_returns_201(self, tmp_path) -> None:
        """POST with valid payload returns 201 with created feed JSON."""
        app, config_path = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/v1/feeds",
                    json={"title": "New Show", "url": "https://new.example/feed.rss"},
                )
                assert resp.status == 201
                data = await resp.json()
                assert data["title"] == "New Show"
                assert data["url"] == "https://new.example/feed.rss"
                assert data["enabled"] is True  # default
                # Check disk state
                on_disk = yaml.safe_load(config_path.read_text())
                titles = [f["title"] for f in on_disk["feeds"]]
                assert "New Show" in titles

    async def test_post_duplicate_title_returns_409(self, tmp_path) -> None:
        """POST with duplicate title returns 409; config unchanged."""
        app, config_path = _make_app(tmp_path)
        original = config_path.read_text()
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/v1/feeds",
                    json={"title": "Show A", "url": "https://dupe.example/"},
                )
                assert resp.status == 409
                data = await resp.json()
                assert "error" in data
                assert config_path.read_text() == original

    async def test_post_missing_url_returns_422(self, tmp_path) -> None:
        """POST with missing url returns 422 with field-level error body."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/v1/feeds",
                    json={"title": "No URL Show"},
                )
                assert resp.status == 422

    async def test_post_extra_key_returns_422(self, tmp_path) -> None:
        """POST with extra key returns 422 (FeedConfig extra='forbid')."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/v1/feeds",
                    json={"title": "Show X", "url": "https://x.example/", "bogus": 1},
                )
                assert resp.status == 422

    async def test_post_atomic_write(self, tmp_path) -> None:
        """POST calls os.replace exactly once for the atomic write."""
        app, _ = _make_app(tmp_path)
        with (
            _make_db_patch(),
            patch("api.routes.feeds.os.replace") as mock_replace,
        ):
            async with TestClient(TestServer(app)) as client:
                resp = await client.post(
                    "/api/v1/feeds",
                    json={"title": "Atomic Show", "url": "https://atomic.example/"},
                )
                assert resp.status == 201
                assert mock_replace.call_count == 1


# ---------------------------------------------------------------------------
# PATCH /api/v1/feeds/{slug}
# ---------------------------------------------------------------------------

class TestPatchFeed:
    async def test_patch_url_updates_feed(self, tmp_path) -> None:
        """PATCH with url only returns 200 with updated feed; disk has new URL."""
        app, config_path = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.patch(
                    "/api/v1/feeds/show-a",
                    json={"url": "https://updated.example/"},
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["url"] == "https://updated.example/"
                assert data["title"] == "Show A"
                on_disk = yaml.safe_load(config_path.read_text())
                show_a = next(f for f in on_disk["feeds"] if f["title"] == "Show A")
                assert show_a["url"] == "https://updated.example/"

    async def test_patch_multi_field_updates_both(self, tmp_path) -> None:
        """PATCH with multiple fields updates all of them."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.patch(
                    "/api/v1/feeds/show-b",
                    json={"enabled": True, "episodes_to_keep": 25},
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["enabled"] is True
                assert data["episodes_to_keep"] == 25

    async def test_patch_ignores_title_field(self, tmp_path) -> None:
        """PATCH with title in payload silently strips it; title unchanged on disk."""
        app, config_path = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.patch(
                    "/api/v1/feeds/show-a",
                    json={"title": "Hijack", "url": "https://x.example/"},
                )
                assert resp.status == 200
                on_disk = yaml.safe_load(config_path.read_text())
                titles = [f["title"] for f in on_disk["feeds"]]
                assert "Show A" in titles
                assert "Hijack" not in titles

    async def test_patch_unknown_slug_returns_404(self, tmp_path) -> None:
        """PATCH unknown slug returns 404."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.patch(
                    "/api/v1/feeds/does-not-exist",
                    json={"url": "https://x.example/"},
                )
                assert resp.status == 404
                data = await resp.json()
                assert "error" in data

    async def test_patch_invalid_type_returns_422(self, tmp_path) -> None:
        """PATCH with invalid type for episodes_to_keep returns 422."""
        app, _ = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.patch(
                    "/api/v1/feeds/show-a",
                    json={"episodes_to_keep": "many"},
                )
                assert resp.status == 422

    async def test_patch_atomic_write(self, tmp_path) -> None:
        """PATCH calls os.replace exactly once."""
        app, _ = _make_app(tmp_path)
        with (
            _make_db_patch(),
            patch("api.routes.feeds.os.replace") as mock_replace,
        ):
            async with TestClient(TestServer(app)) as client:
                resp = await client.patch(
                    "/api/v1/feeds/show-a",
                    json={"url": "https://new.example/"},
                )
                assert resp.status == 200
                assert mock_replace.call_count == 1


# ---------------------------------------------------------------------------
# DELETE /api/v1/feeds/{slug}
# ---------------------------------------------------------------------------

class TestDeleteFeed:
    async def test_delete_removes_feed_and_returns_204(self, tmp_path) -> None:
        """DELETE known slug returns 204; feed removed from config.yaml."""
        app, config_path = _make_app(tmp_path)
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete("/api/v1/feeds/show-a")
                assert resp.status == 204
                on_disk = yaml.safe_load(config_path.read_text())
                titles = [f["title"] for f in on_disk["feeds"]]
                assert "Show A" not in titles
                assert "Show B" in titles

    async def test_delete_unknown_slug_returns_404(self, tmp_path) -> None:
        """DELETE unknown slug returns 404; config unchanged."""
        app, config_path = _make_app(tmp_path)
        original = config_path.read_text()
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete("/api/v1/feeds/does-not-exist")
                assert resp.status == 404
                data = await resp.json()
                assert "error" in data
                assert config_path.read_text() == original

    async def test_delete_last_feed_returns_422(self, tmp_path) -> None:
        """DELETE the only feed returns 422 (min_length=1 violated); config unchanged."""
        # Build config with a single feed
        single_feed_yaml = VALID_YAML  # test_config_loader.py VALID_YAML has one feed
        config_path = tmp_path / "config.yaml"
        config_path.write_text(single_feed_yaml)
        cfg = MagicMock()
        cfg.app.paths.data_dir = tmp_path
        app = create_app(EventBus(), time.monotonic(), RunState(), cfg, config_path)
        original = config_path.read_text()
        with _make_db_patch():
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete("/api/v1/feeds/test-podcast")
                assert resp.status == 422
                assert config_path.read_text() == original

    async def test_delete_atomic_write(self, tmp_path) -> None:
        """DELETE calls os.replace exactly once on success."""
        app, _ = _make_app(tmp_path)
        with (
            _make_db_patch(),
            patch("api.routes.feeds.os.replace") as mock_replace,
        ):
            async with TestClient(TestServer(app)) as client:
                resp = await client.delete("/api/v1/feeds/show-a")
                assert resp.status == 204
                assert mock_replace.call_count == 1
