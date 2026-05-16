# Phase 3: Pipeline Control - Pattern Map

**Mapped:** 2026-05-16
**Files analyzed:** 8
**Analogs found:** 8 / 8

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `api/run_state.py` | model/dataclass | N/A (state container) | `components/pipeline.py` (`_Stores` dataclass) | role-match |
| `api/routes/control.py` | controller | request-response | `api/routes/events.py` + `api/routes/health.py` | exact |
| `api/server.py` | config/bootstrap | request-response | `api/server.py` (self) | self-mod |
| `components/pipeline.py` | service/orchestrator | batch + event-driven | `components/pipeline.py` (self) | self-mod |
| `database/connection.py` | config/migration | CRUD | `database/connection.py` (self — existing ALTER TABLE block) | self-mod |
| `database/episode_store.py` | service/DAO | CRUD | `database/episode_store.py` (self — `update_episode_url`) | self-mod |
| `tests/test_api_control.py` | test | request-response | `tests/test_api_health.py` + `tests/test_api_events.py` | exact |
| `tests/test_pipeline_stop.py` | test | batch + event-driven | `tests/test_pipeline.py` | exact |

---

## Pattern Assignments

### `api/run_state.py` (model/dataclass, state container)

**Analog:** `components/pipeline.py` — `_Stores` dataclass (lines 54–73)

**Dataclass pattern** (`components/pipeline.py` lines 54–73):
```python
@dataclass(slots=True)
class _Stores:
    episode: EpisodeStore
    transcription: TranscriptionStore
    # ...
    episodes_total: int
    episodes_done: int = 0
    episodes_failed: int = 0
```

**Imports pattern to copy** (`components/pipeline.py` lines 1–8):
```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
```

**Core pattern for `run_state.py`** — mirror `_Stores` with `slots=True`, use `field(default_factory=...)` for mutable defaults:
```python
# VALID_STAGES constant — tuple, not list (immutable, hashable)
VALID_STAGES: tuple[str, ...] = ("download", "transcribe", "topic", "ad-detect", "edit")

@dataclass(slots=True)
class FeedRunCounts:
    episodes_total: int = 0
    episodes_done: int = 0
    episodes_failed: int = 0

@dataclass(slots=True)
class RunState:
    state: str = "idle"                     # "idle" | "running" | "stopping"
    started_at: datetime | None = None
    active_feed_slug: str | None = None
    current_episode_guid: str | None = None
    task: asyncio.Task | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    feeds: dict[str, FeedRunCounts] = field(default_factory=dict)
```

**Critical:** `RunState()` must be instantiated inside an `async def` function, never at module level. `asyncio.Event()` requires a running event loop context.

---

### `api/routes/control.py` (controller, request-response)

**Analog:** `api/routes/health.py` (exact factory pattern) + `api/routes/events.py` (dependency injection via closure)

**Imports pattern** (`api/routes/health.py` lines 1–13 and `api/routes/events.py` lines 1–14):
```python
"""Control routes — POST /api/v1/run, GET /api/v1/status, etc."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from api.event_bus import EventBus
    from api.run_state import RunState
    from config.config_loader import Config

logger = logging.getLogger(__name__)
```

**Factory pattern** (`api/routes/health.py` lines 36–56 and `api/routes/events.py` lines 17–46):
```python
def create_control_router(
    config: Config,
    event_bus: EventBus,
    run_state: RunState,
) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/status")
    async def status(_request: web.Request) -> web.Response:
        ...
        return web.json_response(...)

    @routes.post("/api/v1/run")
    async def start_run(request: web.Request) -> web.Response:
        ...

    return routes
```

**409 gate pattern** — use `raise web.HTTPConflict(...)`, not `return web.Response(status=409)`. Consistent with how aiohttp propagates HTTP errors through middleware:
```python
if run_state.state != "idle":
    raise web.HTTPConflict(
        text='{"error": "a run is already active"}',
        content_type="application/json",
    )
```

**422 pattern** for invalid stage:
```python
raise web.HTTPUnprocessableEntity(
    text=f'{{"error": "invalid stage: {stage}"}}',
    content_type="application/json",
)
```

**404 pattern** for unknown slug:
```python
raise web.HTTPNotFound(
    text='{"error": "feed not found"}',
    content_type="application/json",
)
```

**202 response** for accepted run:
```python
return web.json_response(
    {"status": "started", "started_at": run_state.started_at.isoformat()},
    status=202,
)
```

**EventBus guard pattern** (`components/pipeline.py` lines 204–205) — used inside `_run_pipeline_task` wrapper:
```python
if self._event_bus is not None:
    self._event_bus.emit(...)
```

**finally-unsubscribe pattern** (`api/routes/events.py` lines 37–43) — apply same discipline to the pipeline task wrapper's `finally` block:
```python
try:
    ...
finally:
    event_bus.unsubscribe(queue)  # always unregister in finally
```

**Background task wrapper pattern** — `CancelledError` must be re-raised:
```python
async def _run_pipeline_task(pipeline: Pipeline, run_state: RunState) -> None:
    try:
        await pipeline.run()
    except asyncio.CancelledError:
        logger.info("Pipeline task cancelled (force stop requested)")
        raise                       # MUST re-raise — do not swallow
    except Exception:
        logger.exception("Pipeline run failed")
    finally:
        # Always reset state regardless of outcome
        run_state.state = "idle"
        run_state.started_at = None
        run_state.active_feed_slug = None
        run_state.current_episode_guid = None
        run_state.task = None
        run_state.stop_event.clear()
        run_state.feeds.clear()
```

**Slug resolution helper** — mirrors `pipeline.py` `_select_feeds` logic (lines 357–377), uses same `python-slugify` already imported in pipeline.py:
```python
from slugify import slugify

def _resolve_slug(slug: str, feeds: list) -> str | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None
```

---

### `api/server.py` (bootstrap, request-response) — MODIFIED

**Analog:** `api/server.py` (self)

**Current `create_app` signature** (`api/server.py` lines 18–35):
```python
def create_app(event_bus: EventBus, start_time: float) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(start_time))
    app.add_routes(create_events_router(event_bus))
    return app
```

**Modified signature** — add `run_state` and `config` params; register control router:
```python
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app["run_state"] = run_state
    app.add_routes(create_health_router(start_time))
    app.add_routes(create_events_router(event_bus))
    app.add_routes(create_control_router(config, event_bus, run_state))
    return app
```

**Current `serve` coroutine** (`api/server.py` lines 38–61):
```python
async def serve(host: str, port: int) -> None:
    start_time = time.monotonic()
    event_bus = EventBus()
    app = create_app(event_bus, start_time)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"API server listening on {host}:{port}")
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
```

**Modified `serve`** — construct `RunState` inside `async def` (after event loop is running), load config, pass to `create_app`:
```python
async def serve(host: str, port: int) -> None:
    start_time = time.monotonic()
    event_bus = EventBus()
    run_state = RunState()          # inside async def — event loop is running
    config = load_config(...)       # existing config loading pattern
    app = create_app(event_bus, start_time, run_state, config)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"API server listening on {host}:{port}")
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
```

---

### `components/pipeline.py` (service/orchestrator, batch) — MODIFIED

**Analog:** `components/pipeline.py` (self)

**Current `__init__` signature** (lines 98–107):
```python
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
```

**Modified `__init__`** — add `stop_event` and `run_state` optional params after `event_bus`, following the same `* ,` keyword-only pattern:
```python
def __init__(
    self,
    config: Config,
    feed_name: str | None = None,
    *,
    event_bus: EventBus | None = None,
    stop_event: asyncio.Event | None = None,
    run_state: RunState | None = None,
) -> None:
    self._config = config
    self._feed_name = feed_name
    self._event_bus = event_bus
    self._stop_event = stop_event
    self._run_state = run_state
```

**Current episode loop** (lines 247–298) — the stop check and run_state updates insert AFTER the episode's existing `try/except/finally` block:
```python
for episode in episodes:
    ...
    try:
        outcome = await self._process_episode_until_final(...)
        stores.episodes_done += 1
        if self._event_bus is not None:
            self._event_bus.emit(...)
    except Exception as exc:
        ...
        stores.episodes_failed += 1
        if self._event_bus is not None:
            self._event_bus.emit(...)
    finally:
        if handler is not None:
            close_episode_log(handler)
        ...

    # --- NEW: stop check after the episode's try/finally ---
    if self._stop_event is not None and self._stop_event.is_set():
        logger.info(f"Graceful stop requested — halting after episode '{episode.guid}'")
        break
```

**run_state update placement** — before `_process_episode_until_final` call (set current GUID), and inside the existing `stores.episodes_done` block (update counters):
```python
# Before processing:
if self._run_state is not None:
    self._run_state.current_episode_guid = episode.guid

# After stores.episodes_done += 1 (inside try block):
if self._run_state is not None:
    self._run_state.current_episode_guid = None
    self._run_state.feeds[feed_slug] = FeedRunCounts(
        episodes_total=stores.episodes_total,
        episodes_done=stores.episodes_done,
        episodes_failed=stores.episodes_failed,
    )
```

**Guard pattern to follow** (`components/pipeline.py` lines 204–205 and 267–268) — same optional-check idiom for run_state:
```python
if self._event_bus is not None:
    self._event_bus.emit(...)
# Same pattern:
if self._run_state is not None:
    self._run_state.current_episode_guid = episode.guid
```

---

### `database/connection.py` (migration, CRUD) — MODIFIED

**Analog:** `database/connection.py` (self — existing ALTER TABLE block, lines 160–167)

**Existing idempotent migration pattern** (lines 160–167):
```python
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN length INTEGER NOT NULL DEFAULT 0"
    )
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"
    )
await self.conn.commit()
```

**New migration** — copy the exact same pattern, add after the existing two `contextlib.suppress` blocks before `await self.conn.commit()`:
```python
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0"
    )
```

No new imports needed — `contextlib` and `aiosqlite` are already imported.

---

### `database/episode_store.py` (service/DAO, CRUD) — MODIFIED

**Analog:** `database/episode_store.py` (self — `update_episode_url` method, lines 171–189)

**Existing UPDATE method pattern** (lines 171–189):
```python
async def update_episode_url(self, guid: str, new_url: str, length: int = 0) -> None:
    await self._conn.execute(
        "UPDATE episodes SET url = ?, length = ? WHERE guid = ?",
        (new_url, length, guid),
    )
    await self._conn.commit()
    logger.info(f"Episode '{guid}': enclosure URL updated to {new_url!r}")
```

**New `skip_episode` method** — follow same pattern, return `bool` for 404 detection (rowcount check):
```python
async def skip_episode(self, guid: str) -> bool:
    """Mark episode as permanently skipped. Returns False if GUID not found."""
    result = await self._conn.execute(
        "UPDATE episodes SET skipped = 1 WHERE guid = ?",
        (guid,),
    )
    await self._conn.commit()
    return result.rowcount > 0
```

**New `reset_episode` method** — uses a `STAGE_CASCADE` dict defined at module level as a whitelist (prevents SQL injection via closed set); `# noqa: S608` on the f-string interpolation line:
```python
STAGE_CASCADE: dict[str, list[str]] = {
    "download":   ["episode_audio_metadata", "transcriptions", "transcription_segments",
                   "topic_extractions", "ad_segments", "ad_detection_runs"],
    "transcribe": ["transcriptions", "transcription_segments", "topic_extractions",
                   "ad_segments", "ad_detection_runs"],
    "topic":      ["topic_extractions", "ad_segments", "ad_detection_runs"],
    "ad-detect":  ["ad_segments", "ad_detection_runs"],
    "edit":       [],
}

async def reset_episode(self, guid: str, *, from_stage: str | None = None) -> bool:
    """Reset episode for reprocessing. Returns False if GUID not found."""
    tables = (
        STAGE_CASCADE[from_stage] if from_stage else STAGE_CASCADE["download"]
    )
    for table in tables:
        await self._conn.execute(
            f"DELETE FROM {table} WHERE guid = ?",  # noqa: S608
            (guid,),
        )
    if from_stage in (None, "download"):
        result = await self._conn.execute(
            "UPDATE episodes SET url = source_url WHERE guid = ?", (guid,)
        )
    else:
        result = await self._conn.execute(
            "SELECT id FROM episodes WHERE guid = ?", (guid,)
        )
    await self._conn.commit()
    return result.rowcount > 0
```

**Logging style** — use f-strings, never `%` operator (CLAUDE.md mandate, already followed in existing methods).

---

### `tests/test_api_control.py` (test, request-response)

**Analog:** `tests/test_api_health.py` (TestClient/TestServer setup) + `tests/test_api_events.py` (dependency injection into app)

**File header and imports pattern** (`tests/test_api_health.py` lines 1–13):
```python
"""Tests for <endpoint group>."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app
```

**App fixture pattern** (`tests/test_api_health.py` lines 21–24) — each test constructs its own `app` inline:
```python
async def test_status_returns_idle_on_fresh_app(self) -> None:
    run_state = RunState()
    app = create_app(EventBus(), time.monotonic(), run_state, config)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/status")
        assert resp.status == 200
```

**Class grouping pattern** (`tests/test_api_health.py` lines 19–53) — group by endpoint/requirement:
```python
class TestStatus:          # STAT-01
    ...
class TestStartRun:        # CTRL-01
    ...
class TestStopRun:         # CTRL-02
    ...
class TestFeedRun:         # CTRL-03
    ...
class TestReprocess:       # CTRL-04
    ...
class TestSkipEpisode:     # CTRL-05
    ...
class TestRunStateLifecycle:   # D-04 RunState reset
    ...
```

**Mocking async tasks** — mirror `tests/test_api_server.py` patch pattern (lines 17–28):
```python
from unittest.mock import AsyncMock, MagicMock, patch

with patch("api.routes.control.asyncio.create_task") as mock_create_task:
    mock_task = MagicMock()
    mock_create_task.return_value = mock_task
    ...
```

**409 assertion pattern** — check both status code and JSON body:
```python
resp = await client.post("/api/v1/run")
assert resp.status == 409
data = await resp.json()
assert "error" in data
```

---

### `tests/test_pipeline_stop.py` (test, batch)

**Analog:** `tests/test_pipeline.py` (lines 1–60)

**File header and imports pattern** (`tests/test_pipeline.py` lines 1–20):
```python
"""Tests for Pipeline — <specific behaviour>."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from api.event_bus import EventBus
from api.run_state import FeedRunCounts, RunState
from components.pipeline import Pipeline
from config.config_loader import FeedConfig
```

**Config mock helper** (`tests/test_pipeline.py` lines 45–60) — reuse or copy `make_config`:
```python
def make_config(feeds: list[FeedConfig]) -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = feeds
    cfg.app.models.transcription.provider = "groq"
    # ... (full mock as in test_pipeline.py)
    return cfg
```

**Episode/feed helpers** (`tests/test_pipeline.py` lines 36–42):
```python
def make_feed(title: str, *, enabled: bool = True) -> FeedConfig:
    return FeedConfig(
        title=title,
        url=f"https://example.com/{title}.rss",
        enabled=enabled,
        episodes_to_keep=10,
    )
```

**Stop event test pattern** — create `asyncio.Event`, pass to Pipeline, set it between yields:
```python
async def test_graceful_stop_halts_after_current_episode(self) -> None:
    stop_event = asyncio.Event()
    pipeline = Pipeline(make_config([make_feed("My Show")]), stop_event=stop_event)
    stop_event.set()
    with patch(...):
        await pipeline.run()
    # Assert only first episode completed, second not started
```

---

## Shared Patterns

### from `__future__ import annotations`
**Source:** Every file in the project
**Apply to:** All new files
```python
from __future__ import annotations
```

### Logger Initialization
**Source:** `api/routes/health.py` line 13, `api/routes/events.py` line 14
**Apply to:** `api/run_state.py` (if needed), `api/routes/control.py`, `database/episode_store.py` additions
```python
logger = logging.getLogger(__name__)
```

### TYPE_CHECKING guard for heavy imports
**Source:** `api/routes/events.py` lines 11–13, `database/episode_store.py` lines 13–14
**Apply to:** `api/routes/control.py` (Config, EventBus, RunState type hints only)
```python
if TYPE_CHECKING:
    from api.event_bus import EventBus
    from api.run_state import RunState
    from config.config_loader import Config
```

### aiohttp HTTP error raising
**Source:** Phase 2 established in events.py; verified via RESEARCH.md
**Apply to:** All POST/control handlers in `api/routes/control.py`
```python
# 409
raise web.HTTPConflict(text='{"error": "..."}', content_type="application/json")
# 404
raise web.HTTPNotFound(text='{"error": "..."}', content_type="application/json")
# 422
raise web.HTTPUnprocessableEntity(text='{"error": "..."}', content_type="application/json")
```

### Optional guard before EventBus/state access
**Source:** `components/pipeline.py` lines 204, 267, 283
**Apply to:** All optional-dependency accesses in `components/pipeline.py` modifications and `api/routes/control.py` wrapper
```python
if self._event_bus is not None:
    self._event_bus.emit(...)
if self._run_state is not None:
    self._run_state.current_episode_guid = episode.guid
```

### Idempotent ALTER TABLE migration
**Source:** `database/connection.py` lines 160–167
**Apply to:** `database/connection.py` addition for `skipped` column
```python
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0"
    )
```

---

## No Analog Found

All files have close analogs. No entries.

---

## Metadata

**Analog search scope:** `api/`, `api/routes/`, `components/`, `database/`, `tests/`
**Files scanned:** 10 (health.py, events.py, server.py, pipeline.py, connection.py, episode_store.py, test_api_health.py, test_api_events.py, test_api_server.py, test_pipeline.py)
**Pattern extraction date:** 2026-05-16
