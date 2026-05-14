"""Tests for api/server.py — AppRunner + TCPSite lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from api.server import serve


# ---------------------------------------------------------------------------
# serve() lifecycle tests
# ---------------------------------------------------------------------------


class TestServe:
    async def test_serve_sets_up_runner(self) -> None:
        with (
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 8080)

            mock_runner.setup.assert_awaited_once()

    async def test_serve_starts_site(self) -> None:
        with (
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 8080)

            mock_site.start.assert_awaited_once()

    async def test_serve_cleans_up_runner(self) -> None:
        with (
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 8080)

            mock_runner.cleanup.assert_awaited_once()

    async def test_serve_passes_host_and_port_to_site(self) -> None:
        with (
            patch("api.server.web.AppRunner") as mock_runner_cls,
            patch("api.server.web.TCPSite") as mock_site_cls,
            patch("api.server.asyncio.Event") as mock_event_cls,
        ):
            mock_runner = MagicMock()
            mock_runner.setup = AsyncMock()
            mock_runner.cleanup = AsyncMock()
            mock_runner_cls.return_value = mock_runner
            mock_site = MagicMock()
            mock_site.start = AsyncMock()
            mock_site_cls.return_value = mock_site
            mock_event_cls.return_value.wait = AsyncMock()

            await serve("127.0.0.1", 9000)

            call_args = mock_site_cls.call_args
            assert call_args[0][1] == "127.0.0.1"
            assert call_args[0][2] == 9000
