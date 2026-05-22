"""Tests for log file access endpoints — GET /api/v1/logs and GET /api/v1/logs/{tail:.*}."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app

# ---------------------------------------------------------------------------
# App factory helper
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path) -> object:
    """Create a test app with log_dir pointing at tmp_path/logs."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("""\
feeds: []
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
""")
    cfg = MagicMock()
    cfg.app.paths.data_dir = tmp_path
    cfg.app.paths.output_dir = tmp_path / "output"
    cfg.app.paths.log_dir = tmp_path / "logs"
    return create_app(EventBus(), time.monotonic(), RunState(), cfg, config_path, tmp_path / "logs")


# ---------------------------------------------------------------------------
# GET /api/v1/logs — listing
# ---------------------------------------------------------------------------


class TestLogList:
    async def test_list_returns_empty_when_log_dir_absent(self, tmp_path: Path) -> None:
        """GET /api/v1/logs returns {"app_logs": [], "episode_logs": {}} when log_dir does not exist."""
        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs")
            assert resp.status == 200
            data = await resp.json()
            assert data == {"app_logs": [], "episode_logs": {}}

    async def test_list_returns_app_logs_at_top_level(self, tmp_path: Path) -> None:
        """GET /api/v1/logs lists top-level *.log files under app_logs."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_text("line1\n")
        (log_dir / "error.log").write_text("error1\n")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs")
            assert resp.status == 200
            data = await resp.json()
            filenames = {e["filename"] for e in data["app_logs"]}
            assert "app.log" in filenames
            assert "error.log" in filenames
            # Each entry has the required fields
            for entry in data["app_logs"]:
                assert "filename" in entry
                assert "size_bytes" in entry
                assert "last_modified" in entry
                assert isinstance(entry["size_bytes"], int)

    async def test_list_groups_episode_logs_by_slug(self, tmp_path: Path) -> None:
        """Episode logs appear under episode_logs grouped by feed slug."""
        log_dir = tmp_path / "logs"
        episodes_dir = log_dir / "episodes" / "prof-g"
        episodes_dir.mkdir(parents=True)
        (episodes_dir / "ep1.log").write_text("ep log\n")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs")
            assert resp.status == 200
            data = await resp.json()
            assert "prof-g" in data["episode_logs"]
            entries = data["episode_logs"]["prof-g"]
            assert len(entries) == 1
            assert entries[0]["filename"] == "episodes/prof-g/ep1.log"

    async def test_list_episode_logs_filename_is_relative_to_log_dir(self, tmp_path: Path) -> None:
        """filename in episode_logs is relative to log_dir (e.g. episodes/<slug>/file.log)."""
        log_dir = tmp_path / "logs"
        slug_dir = log_dir / "episodes" / "my-show"
        slug_dir.mkdir(parents=True)
        (slug_dir / "2026-01-01.log").write_text("content\n")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs")
            assert resp.status == 200
            data = await resp.json()
            assert "my-show" in data["episode_logs"]
            filename = data["episode_logs"]["my-show"][0]["filename"]
            assert filename == "episodes/my-show/2026-01-01.log"


# ---------------------------------------------------------------------------
# Path traversal security — list + read
# ---------------------------------------------------------------------------


class TestLogSecurity:
    async def test_traversal_on_list_path_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/logs/../etc/passwd returns 400."""
        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/../etc/passwd")
            assert resp.status == 400

    async def test_traversal_on_read_path_returns_400(self, tmp_path: Path) -> None:
        """GET /api/v1/logs/../../etc/passwd returns 400."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/../../etc/passwd")
            assert resp.status == 400


# ---------------------------------------------------------------------------
# GET /api/v1/logs/{filename} — read + paginate
# ---------------------------------------------------------------------------


class TestLogRead:
    async def test_read_returns_full_content_as_text_plain(self, tmp_path: Path) -> None:
        """GET /api/v1/logs/<file> returns 200 with text/plain content type."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_bytes(b"hello world\n")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/app.log")
            assert resp.status == 200
            assert "text/plain" in resp.content_type
            body = await resp.read()
            assert body == b"hello world\n"

    async def test_read_sets_x_log_headers(self, tmp_path: Path) -> None:
        """Response includes X-Log-Size, X-Log-Offset, X-Log-Limit headers."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        content = b"abcdefghij"  # 10 bytes
        (log_dir / "test.log").write_bytes(content)

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/test.log")
            assert resp.status == 200
            assert resp.headers["X-Log-Size"] == "10"
            assert resp.headers["X-Log-Offset"] == "0"
            assert resp.headers["X-Log-Limit"] == "10"

    async def test_read_byte_range_with_offset_and_limit(self, tmp_path: Path) -> None:
        """?offset=3&limit=4 returns exactly bytes [3:7] of the file."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        content = b"0123456789"  # 10 bytes
        (log_dir / "range.log").write_bytes(content)

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/range.log?offset=3&limit=4")
            assert resp.status == 200
            body = await resp.read()
            assert body == b"3456"
            assert resp.headers["X-Log-Size"] == "10"
            assert resp.headers["X-Log-Offset"] == "3"
            assert resp.headers["X-Log-Limit"] == "4"

    async def test_read_offset_only_returns_to_eof(self, tmp_path: Path) -> None:
        """?offset=5 with no limit returns bytes from 5 to EOF."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        content = b"0123456789"
        (log_dir / "offset.log").write_bytes(content)

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/offset.log?offset=5")
            assert resp.status == 200
            body = await resp.read()
            assert body == b"56789"

    async def test_read_non_integer_offset_returns_400(self, tmp_path: Path) -> None:
        """?offset=abc returns 400."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_bytes(b"data")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/app.log?offset=abc")
            assert resp.status == 400

    async def test_read_non_integer_limit_returns_400(self, tmp_path: Path) -> None:
        """?limit=xyz returns 400."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_bytes(b"data")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/app.log?limit=xyz")
            assert resp.status == 400

    async def test_read_missing_file_returns_404(self, tmp_path: Path) -> None:
        """GET /api/v1/logs/does-not-exist.log returns 404."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/does-not-exist.log")
            assert resp.status == 404

    async def test_read_existing_file_returns_200_not_501(self, tmp_path: Path) -> None:
        """GET /api/v1/logs/app.log returns 200, not shadowed by the /tail placeholder."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_bytes(b"content")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/app.log")
            assert resp.status == 200

    async def test_tail_placeholder_returns_501(self, tmp_path: Path) -> None:
        """GET /api/v1/logs/app.log/tail returns 501 (placeholder, full impl in plan 03)."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "app.log").write_bytes(b"content")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/app.log/tail")
            assert resp.status == 501

    async def test_read_episode_log_via_subpath(self, tmp_path: Path) -> None:
        """GET /api/v1/logs/episodes/my-show/ep.log returns 200 with correct content."""
        log_dir = tmp_path / "logs"
        slug_dir = log_dir / "episodes" / "my-show"
        slug_dir.mkdir(parents=True)
        (slug_dir / "ep.log").write_bytes(b"episode log content")

        app = _make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/logs/episodes/my-show/ep.log")
            assert resp.status == 200
            body = await resp.read()
            assert body == b"episode log content"
