"""Tests for api/server.py — AppRunner + TCPSite lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from api.server import serve


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = []
    return cfg


# ---------------------------------------------------------------------------
# serve() lifecycle tests
# ---------------------------------------------------------------------------


class TestServe:
    async def test_serve_applies_migrations_before_starting_runner(self, tmp_path) -> None:
        with (
            patch("api.server.Database") as mock_db_cls,
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_db = AsyncMock()
            mock_db_cls.return_value = mock_db
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()
            cfg = _make_config()

            await serve("127.0.0.1", 8080, cfg, tmp_path / "config.yaml")

            mock_db_cls.assert_called_once_with(cfg.app.paths.data_dir / "data.db")
            mock_db.__aenter__.assert_awaited_once()
            mock_db.__aexit__.assert_awaited_once()
            mock_runner.setup.assert_awaited_once()

    async def test_serve_sets_up_runner(self, tmp_path) -> None:
        with (
            patch("api.server.Database") as mock_db_cls,
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_db_cls.return_value = AsyncMock()
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 8080, _make_config(), tmp_path / "config.yaml")

            mock_runner.setup.assert_awaited_once()

    async def test_serve_starts_site(self, tmp_path) -> None:
        with (
            patch("api.server.Database") as mock_db_cls,
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_db_cls.return_value = AsyncMock()
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 8080, _make_config(), tmp_path / "config.yaml")

            mock_site.start.assert_awaited_once()

    async def test_serve_cleans_up_runner(self, tmp_path) -> None:
        with (
            patch("api.server.Database") as mock_db_cls,
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_db_cls.return_value = AsyncMock()
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 8080, _make_config(), tmp_path / "config.yaml")

            mock_runner.cleanup.assert_awaited_once()

    async def test_serve_passes_host_and_port_to_site(self, tmp_path) -> None:
        with (
            patch("api.server.Database") as mock_db_cls,
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_db_cls.return_value = AsyncMock()
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 9000, _make_config(), tmp_path / "config.yaml")

            call_args = mock_site_cls.call_args
            assert call_args[0][1] == "127.0.0.1"
            assert call_args[0][2] == 9000
