# Phase 1: API Foundation - Pattern Map

**Mapped:** 2026-05-14
**Files analyzed:** 8 (5 new, 1 modified, 2 new test files + 1 extended test)
**Analogs found:** 8 / 8

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `api/__init__.py` | config | — | `database/__init__.py` | role-match |
| `api/event_bus.py` | service | event-driven | `components/feed_downloader.py` | role-match (stateless class + async) |
| `api/server.py` | service | request-response | `main.py` (async lifecycle) | partial-match |
| `api/routes/__init__.py` | config | — | `database/__init__.py` | role-match |
| `api/routes/health.py` | controller | request-response | `components/feed_downloader.py` (aiohttp usage) | partial-match |
| `main.py` *(modify)* | utility | request-response | `main.py` itself | exact (self-extension) |
| `tests/test_api_event_bus.py` | test | event-driven | `tests/test_feed_downloader.py` | role-match |
| `tests/test_api_health.py` | test | request-response | `tests/test_feed_downloader.py` | role-match |
| `tests/test_api_server.py` | test | request-response | `tests/test_main.py` | role-match |
| `tests/test_main.py` *(extend)* | test | request-response | `tests/test_main.py` itself | exact (self-extension) |

---

## Pattern Assignments

### `api/__init__.py` (config, package init)

**Analog:** `database/__init__.py` (empty or minimal re-export pattern)

**Pattern:** Keep empty or expose only `create_app` at the package level. No logic. Matches how `database/__init__.py` exposes nothing — callers import specific modules directly.

```python
# api/__init__.py
"""HTTP API layer for podcast-ad-cutter."""

from api.server import create_app

__all__ = ["create_app"]
```

---

### `api/event_bus.py` (service, event-driven)

**Analog:** `components/feed_downloader.py`

**Imports pattern** (`components/feed_downloader.py` lines 1–11):
```python
"""Feed downloader — fetches RSS/Atom XML for each podcast feed."""

from __future__ import annotations

import http
import logging

import aiohttp

logger = logging.getLogger(__name__)
```

**Stateless class pattern** (`components/feed_downloader.py` lines 13–23):
The project uses stateless classes with constructor injection and no module-level singletons. `FeedDownloader` has no `__init__` — EventBus will have one to hold `_subscribers`.

**Core pattern** — asyncio Queue broadcast (no codebase analog; use RESEARCH.md Pattern 4):
```python
"""Event bus — typed asyncio broadcast for pipeline events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class PipelineEventType(StrEnum):
    EPISODE_STAGE_CHANGED = "episode.stage_changed"
    DOWNLOAD_PROGRESS = "episode.download_progress"
    ENCODE_PROGRESS = "episode.encode_progress"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    EPISODE_COMPLETED = "episode.completed"
    EPISODE_FAILED = "episode.failed"


@dataclass
class PipelineEvent:
    type: PipelineEventType
    payload: dict  # serializable; exact shape per event type defined in Phase 2


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[PipelineEvent]] = []

    def subscribe(self) -> asyncio.Queue[PipelineEvent]:
        q: asyncio.Queue[PipelineEvent] = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[PipelineEvent]) -> None:
        self._subscribers.remove(q)

    def emit(self, event: PipelineEvent) -> None:
        # Iterate snapshot to avoid RuntimeError if unsubscribe() runs concurrently
        for q in list(self._subscribers):
            q.put_nowait(event)
```

**Note:** `StrEnum` is used (Python 3.11+ stdlib) — consistent with the project's Python 3.12 target and existing use of `StrEnum` in `models/`.

---

### `api/server.py` (service, request-response)

**Analog:** `main.py` (async lifecycle management, argparse pattern)

**Module-level logger pattern** (`main.py` line 16):
```python
logger = logging.getLogger(__name__)
```

**Async lifecycle + resource cleanup pattern** (`main.py` lines 143–178):
The project's `main()` coroutine uses try/except for clean error handling, then exits. `serve()` follows the same shape but with AppRunner lifecycle instead of Pipeline:

```python
"""API server — AppRunner + TCPSite lifecycle (no web.run_app)."""

from __future__ import annotations

import asyncio
import logging
import time

from aiohttp import web

from api.event_bus import EventBus
from api.routes.health import create_health_router

logger = logging.getLogger(__name__)


def create_app(event_bus: EventBus, start_time: float) -> web.Application:
    """Build and return a configured aiohttp Application.

    No side effects — safe to call in tests with TestClient.
    """
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(start_time))
    return app


async def serve(host: str, port: int) -> None:
    """Start the aiohttp server and keep it running until cancelled."""
    start_time = time.monotonic()
    event_bus = EventBus()
    app = create_app(event_bus, start_time)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"API server listening on {host}:{port}")
    # Block until cancelled (KeyboardInterrupt → asyncio.run cancels all tasks)
    await asyncio.Event().wait()
    await runner.cleanup()
```

**Critical constraint:** Never call `web.run_app()` — CLAUDE.md hard rule. Always `AppRunner` + `TCPSite`.

---

### `api/routes/__init__.py` (config, package init)

**Analog:** `database/__init__.py`

Empty file — just marks the `routes/` directory as a package. No exports needed; callers import `create_health_router` directly from `api.routes.health`.

---

### `api/routes/health.py` (controller, request-response)

**Analog:** `components/feed_downloader.py` (aiohttp usage pattern)

**aiohttp session/response pattern** (`components/feed_downloader.py` lines 41–46):
```python
async with aiohttp.ClientSession() as session:
    for title, url in feeds:
        xml = await self._fetch_one(session, title, url)
```

The project uses `aiohttp` already; `web.json_response()` is the server-side equivalent of `session.get()` patterns. Health handler follows the same import structure:

**Imports pattern** (derive from `components/feed_downloader.py` lines 1–11 + RESEARCH.md):
```python
"""Health check route — GET /api/v1/health."""

from __future__ import annotations

import importlib.metadata
import logging
import time
import tomllib
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)
```

**Core handler pattern** (no direct codebase analog — use RESEARCH.md Code Examples):
```python
def _read_version() -> str:
    try:
        return importlib.metadata.version("podcast-ad-cutter")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        return str(data["project"]["version"])


def create_health_router(start_time: float) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/health")
    async def health(request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "uptime_seconds": round(time.monotonic() - start_time, 2),
            "version": _read_version(),
        })

    return routes
```

**Error envelope convention** (D-14 — define now, used in all future phases):
```python
# Success: return resource data directly
return web.json_response({"status": "ok", ...})

# Error: always use this shape
return web.json_response(
    {"error": "message", "detail": {...}},
    status=4xx_or_5xx,
)
```

---

### `main.py` *(modify)* (utility, request-response)

**Analog:** `main.py` itself — self-extension following existing patterns exactly.

**Existing argparse pattern** (`main.py` lines 19–61) — add `--serve`, `--host`, `--port` using the same `.add_argument()` style:
```python
parser.add_argument(
    "--serve",
    action="store_true",
    help="Start the HTTP API server instead of running the pipeline once.",
)
parser.add_argument(
    "--host",
    type=str,
    default="0.0.0.0",
    help="Host to bind the API server to (default: 0.0.0.0)",
)
parser.add_argument(
    "--port",
    type=int,
    default=8080,
    help="Port to bind the API server to (default: 8080)",
)
```

**Existing dispatch pattern** (`main.py` lines 143–178) — extend `main()` with a branch:
```python
async def main() -> None:
    args = parse_args()
    # ... existing config + logging setup unchanged ...

    if args.serve:
        await serve(args.host, args.port)
    else:
        pipeline = Pipeline(cfg, feed_name=args.feed)
        try:
            logger.info("Starting...")
            await pipeline.run()
        except ValueError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
```

**Import to add at top of `main.py`:**
```python
from api.server import serve
```

---

### `components/pipeline.py` *(modify)* — add optional EventBus parameter

**Analog:** `components/pipeline.py` itself — self-extension.

**Existing `__init__` signature** (`components/pipeline.py` lines 92–96):
```python
def __init__(self, config: Config, feed_name: str | None = None) -> None:
    self._config = config
    self._feed_name = feed_name
```

**Pattern to add** — keyword-only optional param with `None` default (D-06, RESEARCH.md Pitfall 5):
```python
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.event_bus import EventBus

def __init__(
    self,
    config: Config,
    feed_name: str | None = None,
    *,
    event_bus: EventBus | None = None,
) -> None:
    self._config = config
    self._feed_name = feed_name
    self._event_bus = event_bus
    # ... rest of __init__ unchanged ...
```

**Critical:** Use `TYPE_CHECKING` guard to avoid circular imports. `pipeline.py` must not import `api/` at runtime — only for type hints.

---

## Test Pattern Assignments

### `tests/test_api_event_bus.py` (test, event-driven)

**Analog:** `tests/test_feed_downloader.py` (async test, plain fixture, no mocking needed)

**File header pattern** (`tests/test_feed_downloader.py` lines 1–9):
```python
"""Tests for FeedDownloader — HTTP feed retrieval."""

from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from components.feed_downloader import FeedDownloader
```

**Adapted for EventBus:**
```python
"""Tests for EventBus — asyncio broadcast event bus."""

from __future__ import annotations

import asyncio

import pytest

from api.event_bus import EventBus, PipelineEvent, PipelineEventType
```

**Async test pattern** — no `@pytest.mark.asyncio` needed (`asyncio_mode = "auto"` in pyproject.toml):
```python
async def test_subscribe_returns_queue() -> None:
    bus = EventBus()
    q = bus.subscribe()
    assert isinstance(q, asyncio.Queue)
```

**Class grouping pattern** (`tests/test_main.py` lines 54–162) — group related tests in classes:
```python
class TestEventBusSubscribe:
    ...

class TestEventBusEmit:
    ...
```

---

### `tests/test_api_health.py` (test, request-response)

**Analog:** `tests/test_feed_downloader.py` — but uses `TestClient` instead of `aioresponses`

**TestClient pattern** (RESEARCH.md Pattern 3 — no codebase analog; this is the first route test):
```python
"""Tests for GET /api/v1/health."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.server import create_app


async def test_health_returns_200() -> None:
    import time
    app = create_app(EventBus(), time.monotonic())
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert isinstance(data["uptime_seconds"], float)
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0
```

---

### `tests/test_api_server.py` (test, request-response)

**Analog:** `tests/test_main.py` — mocking + AsyncMock pattern

**Mock pattern** (`tests/test_main.py` lines 260–311):
```python
from unittest.mock import AsyncMock, MagicMock, patch

async def test_pipeline_value_error_writes_to_stderr_and_exits(
    self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with (
        patch("main.load_config", return_value=mock_cfg),
        patch("main.configure_logging"),
        patch("main.Pipeline") as mock_pipeline_cls,
    ):
        mock_pipeline_cls.return_value.run = AsyncMock(...)
        ...
```

**Adapted for serve() tests** — patch `AppRunner` and `TCPSite` to avoid real port binding:
```python
from unittest.mock import AsyncMock, MagicMock, patch

async def test_serve_sets_up_runner_and_site() -> None:
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
        mock_site.start.assert_awaited_once()
        mock_runner.cleanup.assert_awaited_once()
```

---

### `tests/test_main.py` *(extend)* — add `--serve` dispatch tests

**Analog:** `tests/test_main.py` itself — follow the `TestMain` class structure exactly.

**Existing test class** (`tests/test_main.py` lines 260–339) — add new test methods inside `TestMain`:
```python
async def test_serve_flag_calls_serve(
    self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py", "--serve"])
    mock_cfg = MagicMock()
    mock_cfg.app.log.level = "INFO"
    mock_cfg.app.log.to_file = False
    mock_cfg.app.paths.log_dir = tmp_path
    with (
        patch("main.load_config", return_value=mock_cfg),
        patch("main.configure_logging"),
        patch("main.serve") as mock_serve,
    ):
        mock_serve.return_value = AsyncMock()
        await main()
    mock_serve.assert_awaited_once()

async def test_no_serve_flag_runs_pipeline(
    self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py"])
    # ... existing happy path test pattern ...
```

---

## Shared Patterns

### Module Docstring + `from __future__ import annotations`
**Source:** Every existing module (`main.py` line 1, `components/feed_downloader.py` line 1, etc.)
**Apply to:** All new `api/` files
```python
"""<Module purpose — one sentence.>"""

from __future__ import annotations
```

### Module-level Logger
**Source:** `main.py` line 16, `components/feed_downloader.py` line 14
**Apply to:** `api/event_bus.py`, `api/server.py`, `api/routes/health.py`
```python
logger = logging.getLogger(__name__)
```

### Logging with f-strings (never `%`)
**Source:** `main.py` lines 166–168, CLAUDE.md constraint
**Apply to:** All new files
```python
# Correct:
logger.info(f"API server listening on {host}:{port}")
# Wrong:
logger.info("API server listening on %s:%s", host, port)
```

### `async with` for every resource
**Source:** `components/feed_downloader.py` lines 41–46, CLAUDE.md constraint
**Apply to:** `api/server.py` (AppRunner cleanup), all test files using `TestClient`
```python
async with aiohttp.ClientSession() as session:
    ...
# Equivalent for server tests:
async with TestClient(TestServer(app)) as client:
    ...
```

### Test: No `@pytest.mark.asyncio` decorator
**Source:** `tests/test_feed_downloader.py` lines 32–38, `tests/test_main.py` lines 261–272
**Apply to:** All new `tests/test_api_*.py` files
```python
# asyncio_mode = "auto" is active — just write async def:
async def test_something() -> None:
    ...
```

### Test: Class grouping for related tests
**Source:** `tests/test_main.py` lines 54, 168, 211, 260 — `TestConfigureLogging`, `TestRotateLogs`, `TestParseArgs`, `TestMain`
**Apply to:** `tests/test_api_event_bus.py`, `tests/test_api_health.py`, `tests/test_api_server.py`

### Exception subclass pattern
**Source:** `utils/exceptions.py` lines 6–49
**Apply to:** If an `ApiError` subclass is added for HTTP error responses:
```python
class ApiError(PodcastAdCutterError):
    """Raised when an API handler cannot fulfill a request."""

    def __init__(self, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `api/routes/health.py` (handler body) | controller | request-response | No aiohttp server-side handlers exist yet; first route in the project |
| `tests/test_api_health.py` (TestClient usage) | test | request-response | No existing tests use `aiohttp.test_utils.TestClient`; first server route test |

For these, use RESEARCH.md Patterns 1, 2, 3 directly.

---

## Metadata

**Analog search scope:** `/home/oton/projects/podcast-ad-cutter/` — all `.py` files in `components/`, `database/`, `models/`, `utils/`, `config/`, `tests/`, `main.py`
**Files scanned:** 10 source files read in full or in part
**Pattern extraction date:** 2026-05-14
```
