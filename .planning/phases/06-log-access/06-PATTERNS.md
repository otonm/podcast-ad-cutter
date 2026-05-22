# Phase 6: Log Access - Pattern Map

**Mapped:** 2026-05-22
**Files analyzed:** 4 (2 new, 2 modified)
**Analogs found:** 4 / 4

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `api/routes/logs.py` | route | request-response + streaming | `api/routes/db.py` + `api/routes/events.py` | role-match (factory pattern from db.py; SSE pattern from events.py) |
| `api/server.py` | config | request-response | `api/server.py` itself (modify) | exact |
| `main.py` | config | request-response | `main.py` itself (modify) | exact |
| `tests/test_api_logs.py` | test | request-response + streaming | `tests/test_api_db.py` | role-match |

---

## Pattern Assignments

### `api/routes/logs.py` (route, request-response + streaming)

**Primary analog:** `api/routes/db.py` (factory shape)
**Secondary analog:** `api/routes/events.py` (SSE StreamResponse)

#### Imports pattern

Copy from `api/routes/db.py` lines 1-19 and `api/routes/events.py` lines 1-14, merged:

```python
"""Log access routes — GET /api/v1/logs, /api/v1/logs/{tail:.*}, /api/v1/logs/{tail:.*}/tail."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

logger = logging.getLogger(__name__)
```

No `TYPE_CHECKING` imports needed — `Path` is used at runtime in the function signature.

#### Factory pattern (core structure)

Copy factory shell from `api/routes/db.py` lines 74-93:

```python
def create_logs_router(log_dir: Path) -> web.RouteTableDef:
    """Build and return a RouteTableDef with log access handlers registered.

    Args:
        log_dir: Path to the logs directory.

    Returns:
        RouteTableDef with log handlers registered.

    """
    routes = web.RouteTableDef()

    # --- handlers defined here as nested functions (closure over log_dir) ---

    return routes
```

#### Route registration order (critical)

From RESEARCH.md verified experiment — `/tail` MUST be registered before the glob or the glob swallows it:

```python
# MUST come first — more specific pattern
@routes.get("/api/v1/logs/{tail:.*}/tail")
async def tail_log(request: web.Request) -> web.StreamResponse: ...

# MUST come second — glob catches everything else
@routes.get("/api/v1/logs/{tail:.*}")
async def read_log(request: web.Request) -> web.Response: ...
```

#### Path traversal validation helper

From RESEARCH.md Pattern 2 (verified experiment):

```python
def _validate_path(log_dir: Path, tail: str) -> Path:
    target = (log_dir / tail).resolve()
    if not target.is_relative_to(log_dir.resolve()):
        raise web.HTTPBadRequest(text="Invalid path")
    return target
```

#### Query param parsing pattern (error handling)

Copy from `api/routes/db.py` lines 97-108:

```python
try:
    limit = int(request.rel_url.query.get("limit", 50))
    offset = int(request.rel_url.query.get("offset", 0))
except ValueError:
    raise web.HTTPBadRequest(
        text='{"error": "offset and limit must be integers"}',
        content_type="application/json",
    ) from None
```

For logs, adapt to `offset`/`limit` as optional (None if absent) and use `text/plain` error body:

```python
try:
    raw_offset = request.rel_url.query.get("offset")
    raw_limit = request.rel_url.query.get("limit")
    offset = int(raw_offset) if raw_offset is not None else None
    limit = int(raw_limit) if raw_limit is not None else None
except ValueError:
    raise web.HTTPBadRequest(text="offset and limit must be integers") from None
```

#### SSE StreamResponse setup and disconnect handling

Copy from `api/routes/events.py` lines 29-44 (entire handler body):

```python
resp = web.StreamResponse()
resp.headers["Content-Type"] = "text/event-stream"
resp.headers["Cache-Control"] = "no-cache"
resp.headers["X-Accel-Buffering"] = "no"
await resp.prepare(request)
# ... setup ...
try:
    while True:
        # ... poll loop ...
        pass
finally:
    fh.close()  # always close file handle on disconnect
return resp  # pragma: no cover
```

The `finally` block pattern (line 43 in events.py: `event_bus.unsubscribe(queue)`) maps directly to `fh.close()` for the log tail handler.

#### asyncio.to_thread pattern for blocking I/O

All file I/O must be off the event loop. Pattern from RESEARCH.md Pattern 3:

```python
# Wrap blocking open + seek + read in to_thread
fh, pos, backfill = await asyncio.to_thread(open_and_backfill, log_path, bytes_param)

# Wrap each poll cycle in to_thread; pass args explicitly (no mutable closure capture)
new_data, pos = await asyncio.to_thread(poll, fh, pos, log_path)
```

#### json_response pattern

Copy from `api/routes/db.py` line 147:

```python
return web.json_response(result)
```

For the listing endpoint: `return web.json_response(result)` where `result` is `{"app_logs": [...], "episode_logs": {...}}`.

#### text/plain response with custom headers

From RESEARCH.md LOG-02 code example:

```python
return web.Response(
    body=chunk,
    content_type="text/plain",
    charset="utf-8",
    headers={
        "X-Log-Size": str(total),
        "X-Log-Offset": str(start),
        "X-Log-Limit": str(returned),
    },
)
```

---

### `api/server.py` (modify — add `log_dir` parameter)

**Analog:** `api/server.py` lines 30-66 (existing `create_app` and `serve`)

#### create_app signature change

Add `log_dir: Path` as the last positional parameter (lines 30-36):

```python
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
    config_path: Path,
    log_dir: Path,       # ADD: new last positional parameter
) -> web.Application:
```

#### Route registration — add one line after existing db router (lines 56-65):

```python
app.add_routes(create_db_router(
    config.app.paths.data_dir / "data.db",
    config.app.paths.output_dir,
    config_path,
))
app.add_routes(create_logs_router(log_dir))   # ADD THIS LINE
return app
```

Also add import at top of file (line 14 area):

```python
from api.routes.logs import create_logs_router
```

#### serve() wiring — extract log_dir from config (lines 69-87):

```python
async def serve(host: str, port: int, config: Config, config_path: Path) -> None:
    ...
    app = create_app(event_bus, start_time, run_state, config, config_path, config.app.paths.log_dir)
```

---

### `main.py` (modify — pass log_dir to serve())

**Analog:** `main.py` lines 162-185

`cfg.app.paths.log_dir` is already used at line 177 (`configure_logging(..., log_dir=cfg.app.paths.log_dir, ...)`). The `serve()` call at line 184 needs no change to `main.py` because `log_dir` is now extracted inside `serve()` from `config.app.paths.log_dir` directly. No `main.py` change is required if `serve()` pulls `log_dir` from config internally.

> Note: RESEARCH.md (Pattern 6 and D-14) specifies passing `log_dir` through `serve()` → `create_app()` → `create_logs_router()`. Since `serve()` already receives `config`, it can extract `config.app.paths.log_dir` itself — no `main.py` signature change needed.

---

### `tests/test_api_logs.py` (test, request-response + streaming)

**Analog:** `tests/test_api_db.py` lines 1-79

#### File header and imports pattern

Copy from `tests/test_api_db.py` lines 1-18:

```python
"""Tests for log access endpoints — GET /api/v1/logs, /api/v1/logs/{tail:.*}, /api/v1/logs/{tail:.*}/tail."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app
```

#### App factory helper pattern

Copy from `tests/test_api_db.py` lines 65-73, adapted for `log_dir`:

```python
def _make_app(tmp_path: Path) -> object:
    """Create a test app with a real log_dir on disk."""
    cfg = MagicMock()
    cfg.app.paths.data_dir = tmp_path
    cfg.app.paths.output_dir = tmp_path / "output"
    cfg.app.paths.log_dir = tmp_path / "logs"
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}")
    return create_app(EventBus(), time.monotonic(), RunState(), cfg, config_path, log_dir)
```

Note: all existing `create_app()` call sites across the test suite must also be updated to pass `log_dir` as the 6th argument. Those call sites (in `test_api_db.py`, `test_api_server.py`, etc.) use `MagicMock` configs — pass `tmp_path / "logs"` (no need for the dir to exist for non-log tests).

#### TestClient pattern

Copy from `tests/test_api_db.py` (lines after 79 — standard aiohttp pattern):

```python
async def test_list_logs_empty(self, tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/logs")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"app_logs": [], "episode_logs": {}}
```

#### SSE test pattern

For tail endpoint tests, use the `asyncio.to_thread` / cancellation approach — connect, read the first event (backfill), cancel:

```python
async def test_tail_backfill(self, tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "app.log"
    log_file.write_text("line1\nline2\n")
    app = _make_app(tmp_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/logs/app.log/tail?bytes=100&interval=0.5")
        assert resp.status == 200
        assert "text/event-stream" in resp.headers["Content-Type"]
        # Read first SSE event (backfill)
        chunk = await resp.content.read(1024)
        assert b"line1" in chunk or b"line2" in chunk
```

#### pytest-asyncio config

All tests are async — no decorator needed. From RESEARCH.md: `asyncio_mode = "auto"` is already set in `pyproject.toml`. Test class methods are plain `async def`.

---

## Shared Patterns

### Factory function shape
**Source:** `api/routes/db.py` lines 74-93
**Apply to:** `api/routes/logs.py`

```python
def create_logs_router(log_dir: Path) -> web.RouteTableDef:
    routes = web.RouteTableDef()
    # ... handler definitions ...
    return routes
```

### SSE StreamResponse + finally cleanup
**Source:** `api/routes/events.py` lines 31-44
**Apply to:** `tail_log` handler in `api/routes/logs.py`

```python
resp = web.StreamResponse()
resp.headers["Content-Type"] = "text/event-stream"
resp.headers["Cache-Control"] = "no-cache"
resp.headers["X-Accel-Buffering"] = "no"
await resp.prepare(request)
try:
    while True:
        ...
finally:
    fh.close()
return resp  # pragma: no cover
```

### Query param ValueError handling
**Source:** `api/routes/db.py` lines 97-108
**Apply to:** `read_log` handler (offset/limit params) and `tail_log` handler (bytes/interval params)

```python
try:
    value = int(request.rel_url.query.get("param", default))
except ValueError:
    raise web.HTTPBadRequest(text="param must be an integer") from None
```

### asyncio.to_thread for blocking calls
**Source:** RESEARCH.md (verified experiment), CLAUDE.md constraint
**Apply to:** All file I/O in `api/routes/logs.py` — `open()`, `read()`, `seek()`, `stat()`, `glob()`

```python
result = await asyncio.to_thread(blocking_function, arg1, arg2)
```

### Logging with f-strings
**Source:** CLAUDE.md (hard rule)
**Apply to:** All `logger.*()` calls in new and modified files

```python
logger.info(f"Serving logs from {log_dir}")   # correct
logger.info("Serving logs from %s", log_dir)  # FORBIDDEN
```

---

## No Analog Found

All files have close analogs. No gaps.

---

## Metadata

**Analog search scope:** `api/routes/`, `api/server.py`, `main.py`, `tests/`
**Files scanned:** 5 (db.py, events.py, server.py, main.py, test_api_db.py, test_api_server.py)
**Pattern extraction date:** 2026-05-22
