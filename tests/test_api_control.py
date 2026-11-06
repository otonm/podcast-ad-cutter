"""Tests for control endpoints — GET /api/v1/status, POST /api/v1/run, etc."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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


_VALID_CONFIG_YAML = """\
feeds:
  - title: "My Show"
    url: "https://my-show.example/feed.rss"
  - title: "Another Show"
    url: "https://another-show.example/feed.rss"
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "model-from-disk"
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


def _write_config(tmp_path: Path, text: str = _VALID_CONFIG_YAML) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(text)
    return config_path


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's .env out of load_config and provide the groq key."""
    monkeypatch.setattr("config.config_loader.load_dotenv", lambda: None)
    monkeypatch.setenv("GROQ_API_KEY", "test")


class TestStatus:
    async def test_status_returns_200_when_idle(self, tmp_path) -> None:
        run_state = RunState()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "idle"
            assert data["started_at"] is None
            assert data["active_feed_slug"] is None
            assert data["current_episode_guid"] is None
            assert data["feeds"] == {}

    async def test_status_returns_running_when_state_set(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "running"
        run_state.started_at = datetime.now(UTC)
        run_state.active_feed_slug = "my-show"
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "running"
            assert isinstance(data["started_at"], str)
            assert data["active_feed_slug"] == "my-show"

    async def test_status_returns_stopping_state(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "stopping"
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "stopping"


class TestStartRun:
    async def test_run_returns_202_when_idle(self, tmp_path) -> None:
        run_state = RunState()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            _write_config(tmp_path), tmp_path / "logs",
        )
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

    async def test_run_returns_409_when_active(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "running"
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run")
            assert resp.status == 409
            data = await resp.json()
            assert "error" in data

    async def test_run_uses_config_reloaded_from_disk(self, tmp_path) -> None:
        config_path = _write_config(tmp_path)
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with (
                patch("api.routes.control.Pipeline") as mock_pipeline,
                patch("api.routes.control.asyncio.create_task", return_value=MagicMock()),
            ):
                resp = await client.post("/api/v1/run")
                assert resp.status == 202
                assert mock_pipeline.call_args.args[0].app.models.ad_detection.model == "model-from-disk"

    async def test_run_returns_422_and_stays_idle_when_config_invalid(self, tmp_path) -> None:
        config_path = _write_config(tmp_path)
        run_state = RunState()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            config_path, tmp_path / "logs",
        )
        _write_config(tmp_path, "feeds: []\n")  # broken by an edit after startup
        async with TestClient(TestServer(app)) as client:
            with patch("api.routes.control.Pipeline") as mock_pipeline:
                resp = await client.post("/api/v1/run")
                assert resp.status == 422
                assert "error" in await resp.json()
                assert run_state.state == "idle"
                assert not mock_pipeline.called


class TestStopRun:
    async def test_stop_graceful_sets_stop_event(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "running"
        run_state.task = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run/stop")
            assert resp.status == 202
            data = await resp.json()
            assert data["mode"] == "graceful"
            assert run_state.stop_event.is_set()
            assert run_state.state == "stopping"

    async def test_stop_force_cancels_task(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "running"
        run_state.task = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run/stop?force=true")
            assert resp.status == 202
            data = await resp.json()
            assert data["mode"] == "force"
            assert run_state.task.cancel.called

    async def test_stop_returns_409_when_idle(self, tmp_path) -> None:
        run_state = RunState()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/run/stop")
            assert resp.status == 409
            data = await resp.json()
            assert "error" in data


class TestFeedRun:
    async def test_feed_run_resolves_slug_and_returns_202(self, tmp_path) -> None:
        run_state = RunState()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            _write_config(tmp_path), tmp_path / "logs",
        )
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

    async def test_feed_run_unknown_slug_returns_404(self, tmp_path) -> None:
        run_state = RunState()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            _write_config(tmp_path), tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/feeds/does-not-exist/run")
            assert resp.status == 404
            data = await resp.json()
            assert "error" in data

    async def test_feed_run_finds_feed_added_after_startup(self, tmp_path) -> None:
        config_path = _write_config(
            tmp_path,
            _VALID_CONFIG_YAML.replace(
                "feeds:\n",
                'feeds:\n  - title: "New Show"\n    url: "https://new-show.example/feed.rss"\n',
                1,
            ),
        )
        app = create_app(
            EventBus(), time.monotonic(), RunState(), _make_config(),
            config_path, tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with (
                patch("api.routes.control.Pipeline") as mock_pipeline,
                patch("api.routes.control.asyncio.create_task", return_value=MagicMock()),
            ):
                resp = await client.post("/api/v1/feeds/new-show/run")
                assert resp.status == 202
                assert mock_pipeline.call_args.kwargs["feed_name"] == "New Show"

    async def test_feed_run_returns_422_when_api_key_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        config_path = _write_config(tmp_path)
        run_state = RunState()
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            config_path, tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/v1/feeds/my-show/run")
            assert resp.status == 422
            assert "groq_api_key" in (await resp.json())["error"]
            assert run_state.state == "idle"

    async def test_feed_run_returns_409_when_active(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "running"
        app = create_app(
            EventBus(), time.monotonic(), run_state, _make_config(),
            tmp_path / "config.yaml", tmp_path / "logs",
        )
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


def _make_db_patch(*, skip_episode_return: bool = True, reset_episode_return: bool = True):
    """Return a context manager that patches Database and EpisodeStore for handler tests."""
    mock_db_conn = MagicMock()
    mock_db_obj = MagicMock()
    mock_db_obj.conn = mock_db_conn

    mock_db_cm = MagicMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db_obj)
    mock_db_cm.__aexit__ = AsyncMock(return_value=False)

    mock_store_instance = MagicMock()
    mock_store_instance.skip_episode = AsyncMock(return_value=skip_episode_return)
    mock_store_instance.reset_episode = AsyncMock(return_value=reset_episode_return)

    import contextlib

    @contextlib.contextmanager
    def _patches():
        with (
            patch("api.routes.control.Database", return_value=mock_db_cm) as mock_db_cls,
            patch("api.routes.control.EpisodeStore", return_value=mock_store_instance),
        ):
            yield mock_db_cls, mock_store_instance

    return _patches()


class TestSkipEpisode:
    async def test_skip_returns_200_on_success(self, tmp_path) -> None:
        run_state = RunState()
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch(skip_episode_return=True):
                resp = await client.post("/api/v1/episodes/g123/skip")
                assert resp.status == 200
                data = await resp.json()
                assert data == {"status": "skipped", "guid": "g123"}

    async def test_skip_returns_404_when_not_found(self, tmp_path) -> None:
        run_state = RunState()
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch(skip_episode_return=False):
                resp = await client.post("/api/v1/episodes/g123/skip")
                assert resp.status == 404
                data = await resp.json()
                assert "error" in data

    async def test_skip_returns_409_when_active(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "running"
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch() as (mock_db_cls, _):
                resp = await client.post("/api/v1/episodes/g123/skip")
                assert resp.status == 409
                data = await resp.json()
                assert "error" in data
                mock_db_cls.assert_not_called()


class TestReprocess:
    async def test_full_reset_returns_200(self, tmp_path) -> None:
        run_state = RunState()
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch(reset_episode_return=True):
                resp = await client.post("/api/v1/episodes/g1/reprocess")
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "reset"
                assert data["guid"] == "g1"
                assert data["from_stage"] is None

    async def test_reset_with_valid_stage_returns_200(self, tmp_path) -> None:
        run_state = RunState()
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch(reset_episode_return=True):
                resp = await client.post("/api/v1/episodes/g1/reprocess?stage=transcribe")
                assert resp.status == 200
                data = await resp.json()
                assert data["from_stage"] == "transcribe"

    async def test_reset_with_invalid_stage_returns_422(self, tmp_path) -> None:
        run_state = RunState()
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch() as (mock_db_cls, _):
                resp = await client.post("/api/v1/episodes/g1/reprocess?stage=bogus")
                assert resp.status == 422
                data = await resp.json()
                assert "error" in data
                mock_db_cls.assert_not_called()

    async def test_reset_unknown_guid_returns_404(self, tmp_path) -> None:
        run_state = RunState()
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch(reset_episode_return=False):
                resp = await client.post("/api/v1/episodes/unknown-guid/reprocess")
                assert resp.status == 404
                data = await resp.json()
                assert "error" in data

    async def test_reset_returns_409_when_active(self, tmp_path) -> None:
        run_state = RunState()
        run_state.state = "running"
        cfg = _make_config()
        cfg.app.paths.data_dir = MagicMock()
        app = create_app(
            EventBus(), time.monotonic(), run_state, cfg,
            tmp_path / "config.yaml", tmp_path / "logs",
        )
        async with TestClient(TestServer(app)) as client:
            with _make_db_patch() as (mock_db_cls, _):
                resp = await client.post("/api/v1/episodes/g1/reprocess")
                assert resp.status == 409
                data = await resp.json()
                assert "error" in data
                mock_db_cls.assert_not_called()
