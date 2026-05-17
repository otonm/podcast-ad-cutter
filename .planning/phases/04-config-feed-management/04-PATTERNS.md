# Phase 4: Config & Feed Management - Pattern Map

**Mapped:** 2026-05-17
**Files analyzed:** 7 (2 new routes, 2 new test files, 3 modified files)
**Analogs found:** 7 / 7

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `api/routes/settings.py` | route | request-response | `api/routes/control.py` | role-match (same factory, different deps) |
| `api/routes/feeds.py` | route | CRUD + request-response | `api/routes/control.py` | exact (slug resolution, DB read, atomic write) |
| `api/server.py` | config | request-response | `api/server.py` (self) | self-modify |
| `main.py` | config | request-response | `main.py` (self) | self-modify |
| `config/config_loader.py` | model | transform | `config/config_loader.py` (self) | self-modify |
| `tests/test_api_settings.py` | test | request-response | `tests/test_api_control.py` | exact |
| `tests/test_api_feeds.py` | test | CRUD | `tests/test_api_control.py` | exact |

---

## Pattern Assignments

### `api/routes/settings.py` (route, request-response)

**Analog:** `api/routes/health.py` (factory shape) + `api/routes/control.py` (error handling, DB pattern)

**Imports pattern** — copy from `api/routes/control.py` lines 1–23, adapt:
```python
"""Settings routes — GET /api/v1/settings, PATCH /api/v1/settings."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from aiohttp import web
from pydantic import ValidationError

from config.config_loader import PROVIDER_KEY_MAP, AppConfig, Credentials

logger = logging.getLogger(__name__)
```

**Factory signature pattern** — copy from `api/routes/health.py` lines 36–46, adapt to `config_path`:
```python
# api/routes/health.py lines 36-46
def create_health_router(start_time: float) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/health")
    async def health(_request: web.Request) -> web.Response:
        ...

    return routes
```
New files follow the identical pattern — factory receives its deps, closes over them, returns `routes`.

**Core GET handler pattern** — direct from RESEARCH.md (Pattern 1), verified against `api/routes/control.py`:
```python
def create_settings_router(config_path: Path) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/settings")
    async def get_settings(_request: web.Request) -> web.Response:
        with config_path.open() as f:
            raw = yaml.safe_load(f)
        cfg = AppConfig.model_validate(raw)
        creds = Credentials()
        body = cfg.model_dump(mode="json")
        body["credentials"] = {
            field: ("set" if getattr(creds, field) else "not set")
            for field in PROVIDER_KEY_MAP.values()
        }
        return web.json_response(body)

    return routes
```

**Error handling pattern (422)** — copy from `api/routes/control.py` lines 191–193:
```python
# api/routes/control.py lines 191-193
raise web.HTTPUnprocessableEntity(
    text=f'{{"error": "invalid stage: {stage}"}}',
    content_type="application/json",
)
```
For PATCH /settings, use `exc.json()` from `ValidationError` as the text body:
```python
except ValidationError as exc:
    raise web.HTTPUnprocessableEntity(
        text=exc.json(), content_type="application/json"
    ) from exc
```

**Atomic write helper** — no codebase analog exists; implement as private sync function + `asyncio.to_thread`:
```python
def _write_config_sync(config_path: Path, cfg: AppConfig) -> None:
    data = cfg.model_dump(mode="json")
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=config_path.parent,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        tmp_path = f.name
    os.replace(tmp_path, config_path)
```
Called as: `await asyncio.to_thread(_write_config_sync, config_path, cfg)`

**Deep merge helper** — no codebase analog; implement as private function:
```python
def _deep_merge(base: dict, patch: dict) -> dict:  # type: ignore[type-arg]
    result = dict(base)
    for key, val in patch.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result
```

**PATCH handler pattern** — combines error handling + atomic write:
```python
@routes.patch("/api/v1/settings")
async def patch_settings(request: web.Request) -> web.Response:
    payload = await request.json()
    payload.pop("feeds", None)          # D-06: feeds managed by /api/v1/feeds/*
    with config_path.open() as f:
        base_raw = yaml.safe_load(f)
    merged = _deep_merge(base_raw, payload)
    try:
        cfg = AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise web.HTTPUnprocessableEntity(
            text=exc.json(), content_type="application/json"
        ) from exc
    await asyncio.to_thread(_write_config_sync, config_path, cfg)
    return web.json_response(cfg.model_dump(mode="json"))
```

---

### `api/routes/feeds.py` (route, CRUD + request-response)

**Analog:** `api/routes/control.py` (all patterns — slug resolution, DB context manager, error raising, factory)

**Imports pattern** — adapt from `api/routes/control.py` lines 1–23:
```python
"""Feed routes — GET/POST/PATCH/DELETE /api/v1/feeds."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import yaml
from aiohttp import web
from pydantic import ValidationError
from slugify import slugify

from config.config_loader import AppConfig, FeedConfig
from database.connection import Database

logger = logging.getLogger(__name__)
```

**Slug resolver** — copy `api/routes/control.py` lines 26–31, return `FeedConfig` instead of `str`:
```python
# api/routes/control.py lines 26-31 (existing returns str; feeds version returns FeedConfig)
def _resolve_slug(slug: str, feeds: list) -> str | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None
```
New version for feeds returns full object:
```python
def _find_feed_by_slug(slug: str, feeds: list[FeedConfig]) -> FeedConfig | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed
    return None
```

**Factory signature** — follows `create_control_router` at `api/routes/control.py` lines 64–80:
```python
# api/routes/control.py lines 64-80
def create_control_router(
    config: Config,
    event_bus: EventBus,
    run_state: RunState,
) -> web.RouteTableDef:
    routes = web.RouteTableDef()
    db_path = config.app.paths.data_dir / "data.db"
    ...
    return routes
```
Feeds router receives `config_path` and `db_path` directly:
```python
def create_feeds_router(config_path: Path, db_path: Path) -> web.RouteTableDef:
    routes = web.RouteTableDef()
    ...
    return routes
```

**Per-request DB read pattern** — copy `api/routes/control.py` lines 171–178:
```python
# api/routes/control.py lines 171-178
async with Database(db_path) as db:
    store = EpisodeStore(db.conn)
    ok = await store.skip_episode(guid)
```
For episode COUNT query (no EpisodeStore needed):
```python
async with Database(db_path) as db:
    for feed in cfg.feeds:
        async with db.conn.execute(
            "SELECT COUNT(*) FROM episodes WHERE podcast = ?", (feed.title,)
        ) as cursor:
            row = await cursor.fetchone()
        count = row[0] if row else 0
```

**404 pattern** — copy `api/routes/control.py` lines 137–140:
```python
# api/routes/control.py lines 137-140
if feed_title is None:
    raise web.HTTPNotFound(
        text='{"error": "feed not found"}',
        content_type="application/json",
    )
```

**409 duplicate pattern** — copy `api/routes/control.py` lines 89–93:
```python
# api/routes/control.py lines 89-93
raise web.HTTPConflict(
    text='{"error": "a run is already active"}',
    content_type="application/json",
)
```
For duplicate feed title:
```python
raise web.HTTPConflict(
    text='{"error": "feed title already exists"}',
    content_type="application/json",
)
```

**Path param extraction** — copy `api/routes/control.py` line 134:
```python
# api/routes/control.py line 134
slug = request.match_info["slug"]
```

**Atomic write** — same `_write_config_sync` helper shared with `settings.py`; move to a shared utility or duplicate in `feeds.py`.

---

### `api/server.py` (modify — add `config_path` param)

**Analog:** `api/server.py` itself (lines 24–50)

**Current `create_app` signature** (lines 24–29):
```python
# api/server.py lines 24-29
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
) -> web.Application:
```
**Required change** — add `config_path: Path` as 5th positional parameter (update all test call sites, do NOT use a default):
```python
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
    config_path: Path,
) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app["run_state"] = run_state
    app["config_path"] = config_path          # new
    app.add_routes(create_health_router(start_time))
    app.add_routes(create_events_router(event_bus))
    app.add_routes(create_control_router(config, event_bus, run_state))
    app.add_routes(create_settings_router(config_path))                   # new
    app.add_routes(create_feeds_router(config_path, config.app.paths.data_dir / "data.db"))  # new
    return app
```

**Current `serve` signature** (line 53):
```python
# api/server.py line 53
async def serve(host: str, port: int, config: Config) -> None:
```
**Required change** — add `config_path: Path`:
```python
async def serve(host: str, port: int, config: Config, config_path: Path) -> None:
    ...
    app = create_app(event_bus, start_time, run_state, config, config_path)
```

**New import needed** in `api/server.py`:
```python
from pathlib import Path
from api.routes.feeds import create_feeds_router
from api.routes.settings import create_settings_router
```

---

### `main.py` (modify — pass `args.config` to `serve`)

**Analog:** `main.py` itself (lines 183–185)

**Current call** (lines 183–185):
```python
# main.py lines 183-185
if args.serve:
    await serve(args.host, args.port, cfg)
    return
```
**Required change** — add `args.config` (already a `Path` from `parse_args` line 27):
```python
if args.serve:
    await serve(args.host, args.port, cfg, args.config)
    return
```
No other changes to `main.py`.

---

### `config/config_loader.py` (modify — add `extra='forbid'` to `AppConfig`)

**Analog:** `config/config_loader.py` itself (lines 91–101)

**Current `AppConfig`** (lines 91–101):
```python
# config/config_loader.py lines 91-101
class AppConfig(BaseModel):
    """Top-level application configuration loaded from YAML."""

    feeds: list[FeedConfig] = Field(min_length=1)
    models: ModelsConfig
    paths: PathsConfig
    ad_detection: AdDetectionConfig
    output: OutputConfig
    log: LoggingConfig
    base_url: str
```
**Required change** — add `model_config` as first line of the class body:
```python
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

class AppConfig(BaseModel):
    """Top-level application configuration loaded from YAML."""

    model_config = ConfigDict(extra="forbid")

    feeds: list[FeedConfig] = Field(min_length=1)
    # ... rest unchanged
```
`ConfigDict` must be added to the existing `pydantic` import on line 11.

---

### `tests/test_api_settings.py` (new test file)

**Analog:** `tests/test_api_control.py` (all patterns)

**File header + imports pattern** — copy from `tests/test_api_control.py` lines 1–16:
```python
"""Tests for settings endpoints — GET /api/v1/settings, PATCH /api/v1/settings."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app
```

**`_make_config` factory** — copy from `tests/test_api_control.py` lines 24–29:
```python
# tests/test_api_control.py lines 24-29
def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.app.feeds = [
        _make_feed("My Show"),
        _make_feed("Another Show"),
    ]
    return cfg
```
Settings tests need a real config YAML on disk (use `tmp_path` fixture), so `_make_config` returns a real `Config` object or a minimal YAML file.

**`create_app` call pattern** — copy from `tests/test_api_control.py` line 36:
```python
# tests/test_api_control.py line 36
app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
```
Updated to pass `config_path` as 5th argument (all existing test files must be updated too):
```python
app = create_app(EventBus(), time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml")
```

**TestClient + TestServer pattern** — copy from `tests/test_api_control.py` lines 37–41:
```python
# tests/test_api_control.py lines 37-41
async with TestClient(TestServer(app)) as client:
    resp = await client.get("/api/v1/status")
    assert resp.status == 200
    data = await resp.json()
    assert data["state"] == "idle"
```

**Class-per-endpoint grouping** — copy from `tests/test_api_control.py` (classes `TestStatus`, `TestStartRun`, etc.); use `TestGetSettings`, `TestPatchSettings`.

**YAML fixture helper** — use the `VALID_YAML` constant pattern from `tests/test_config_loader.py` lines 17–48:
```python
# tests/test_config_loader.py lines 17-48
VALID_YAML: str = """\
feeds:
  - title: "Test Podcast"
    url: "https://example.com/feed.rss"
    ...
"""
```
Write to `tmp_path / "config.yaml"` inside each test using `(tmp_path / "config.yaml").write_text(VALID_YAML)`.

---

### `tests/test_api_feeds.py` (new test file)

**Analog:** `tests/test_api_control.py` (all patterns — slug resolution, DB mocking)

**DB mock pattern** — copy `_make_db_patch` from `tests/test_api_control.py` lines 224–248:
```python
# tests/test_api_control.py lines 224-248
def _make_db_patch(*, skip_episode_return: bool = True, ...):
    mock_db_conn = MagicMock()
    mock_db_obj = MagicMock()
    mock_db_obj.conn = mock_db_conn

    mock_db_cm = MagicMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db_obj)
    mock_db_cm.__aexit__ = AsyncMock(return_value=False)
    ...
    with patch("api.routes.control.Database", return_value=mock_db_cm) as mock_db_cls:
        yield mock_db_cls, mock_store_instance
```
Adapt for feeds: patch `api.routes.feeds.Database`; mock `cursor.fetchone` to return `(N,)` for COUNT queries.

**Slug-based route test** — copy `TestFeedRun` from `tests/test_api_control.py` lines 137–171:
```python
# tests/test_api_control.py lines 154-161
async def test_feed_run_unknown_slug_returns_404(self) -> None:
    run_state = RunState()
    app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/v1/feeds/does-not-exist/run")
        assert resp.status == 404
        data = await resp.json()
        assert "error" in data
```

**PATCH body send pattern** — use `client.patch("/api/v1/feeds/{slug}", json={...})`.

---

## Shared Patterns

### Route Factory
**Source:** `api/routes/health.py` lines 36–56 and `api/routes/control.py` lines 64–80
**Apply to:** `api/routes/settings.py`, `api/routes/feeds.py`
```python
def create_X_router(deps...) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.METHOD("/api/v1/path")
    async def handler(_request: web.Request) -> web.Response:
        ...

    return routes
```

### `web.json_response` with error body
**Source:** `api/routes/control.py` lines 91–93, 115–118, 137–140
**Apply to:** All new route handlers
```python
raise web.HTTPNotFound(
    text='{"error": "feed not found"}',
    content_type="application/json",
)
raise web.HTTPConflict(
    text='{"error": "..."}',
    content_type="application/json",
)
raise web.HTTPUnprocessableEntity(
    text=exc.json(), content_type="application/json"
)
```

### Per-request aiosqlite connection
**Source:** `api/routes/control.py` lines 171–178
**Apply to:** `api/routes/feeds.py` GET handler (episode counts)
```python
async with Database(db_path) as db:
    async with db.conn.execute("SELECT ...", (param,)) as cursor:
        row = await cursor.fetchone()
```

### `model_dump(mode="json")`
**Source:** Pattern confirmed by RESEARCH.md (Pitfall 1 verified at runtime)
**Apply to:** Every `yaml.dump()` call and every `web.json_response()` that serializes an `AppConfig` or `FeedConfig`
```python
# REQUIRED — prevents RepresenterError for pathlib.Path fields
cfg.model_dump(mode="json")
```

### Atomic YAML write
**Source:** RESEARCH.md Pattern 2 (no existing codebase analog — new pattern for this phase)
**Apply to:** All config-write operations in `settings.py` and `feeds.py`
```python
def _write_config_sync(config_path: Path, cfg: AppConfig) -> None:
    data = cfg.model_dump(mode="json")
    with tempfile.NamedTemporaryFile(
        mode="w", dir=config_path.parent, suffix=".tmp",
        delete=False, encoding="utf-8",
    ) as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        tmp_path = f.name
    os.replace(tmp_path, config_path)

# In async handler:
await asyncio.to_thread(_write_config_sync, config_path, cfg)
```

### TestClient + TestServer test setup
**Source:** `tests/test_api_control.py` lines 34–41
**Apply to:** `tests/test_api_settings.py`, `tests/test_api_feeds.py`
```python
app = create_app(EventBus(), time.monotonic(), RunState(), _make_config(), config_path)
async with TestClient(TestServer(app)) as client:
    resp = await client.METHOD("/api/v1/...")
    assert resp.status == NNN
    data = await resp.json()
```

### Existing test call-site update (breaking change)
**Source:** `tests/test_api_control.py` line 36, `tests/test_api_health.py` lines 28–30, `tests/test_api_server.py` lines 37, 56, 75, 94
**Apply to:** All 3 existing test files — add `config_path` as 5th arg to every `create_app(...)` call, and `config_path` to every `serve(...)` call in `test_api_server.py`
```python
# Before (will break after server.py change):
app = create_app(EventBus(), time.monotonic(), run_state, _make_config())
await serve("127.0.0.1", 8080, _make_config())

# After:
app = create_app(EventBus(), time.monotonic(), run_state, _make_config(), tmp_path / "config.yaml")
await serve("127.0.0.1", 8080, _make_config(), tmp_path / "config.yaml")
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `_write_config_sync` (helper in routes) | utility | file-I/O | No atomic YAML write exists anywhere in the codebase — new pattern for this phase |
| `_deep_merge` (helper in settings.py) | utility | transform | No recursive dict merge exists in the codebase — new for this phase |

---

## Metadata

**Analog search scope:** `api/routes/`, `api/server.py`, `main.py`, `config/config_loader.py`, `tests/test_api_*.py`, `tests/test_config_loader.py`
**Files scanned:** 9 source files read directly
**Pattern extraction date:** 2026-05-17
