# Phase 6: Log Access - Research

**Researched:** 2026-05-22
**Domain:** aiohttp routing, SSE streaming, async file polling, path traversal protection
**Confidence:** HIGH

## Summary

Phase 6 adds three read-only endpoints to the existing aiohttp API: a list endpoint (`GET /api/v1/logs`), a download/paginate endpoint (`GET /api/v1/logs/{tail:.*}`), and an SSE tail endpoint (`GET /api/v1/logs/{tail:.*}/tail`). All three operate on the `logs/` directory already used by the pipeline — top-level `*.log` files and `episodes/<slug>/*.log` files.

The technical domain is well-understood from the existing codebase. Phase 5 established the `create_X_router(deps) -> web.RouteTableDef` factory pattern; `api/routes/events.py` provides the SSE `StreamResponse` + `finally`-block cleanup template; and `asyncio.to_thread` with an open file handle kept between polls is the correct approach for non-blocking file tailing (verified by experiment). All decisions are locked in CONTEXT.md — no alternatives need to be explored.

The single highest-risk implementation detail is **aiohttp route registration order**: `{tail:.*}/tail` MUST be registered before `{tail:.*}` or the glob will capture every URL including the tail suffix. This was verified by live experiment: wrong order causes the tail handler to never be reached.

**Primary recommendation:** Follow the locked decisions in CONTEXT.md exactly. Create `api/routes/logs.py` mirroring `api/routes/db.py` structure, register the `/tail` route before the glob in `create_logs_router()`, and wire `log_dir = config.app.paths.log_dir` through `create_app()` → `serve()`.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**LOG-01: Log Listing**
- D-01: Response is hierarchical JSON: `{"app_logs": [...], "episode_logs": {"<feed-slug>": [...]}}`. App logs = top-level `logs/*.log`. Episode logs = `logs/episodes/<feed-slug>/*.log` grouped by feed slug.
- D-02: Each entry: `filename` (relative path from `logs/` root), `size_bytes` (int), `last_modified` (ISO 8601 UTC).
- D-03: `filename` is passed directly in the URL — no extra encoding.

**LOG-02 + LOG-03: Route Pattern**
- D-04: Routes use aiohttp tail match: `routes.get("/api/v1/logs/{tail:.*}")` and `routes.get("/api/v1/logs/{tail:.*}/tail")` capturing multi-segment paths including slashes.
- D-05: Path traversal: resolve `(log_dir / tail).resolve()`, verify `.is_relative_to(log_dir.resolve())`, return **400** on traversal.

**LOG-02: Log File Content**
- D-06: `Content-Type: text/plain; charset=utf-8`. Raw log text body. No JSON wrapping.
- D-07: Pagination in response headers: `X-Log-Size`, `X-Log-Offset`, `X-Log-Limit`. `?offset=N&limit=N` are byte offsets. No `offset` = full file; no `limit` = to EOF.

**LOG-03: SSE Tail**
- D-08: On connect, send last `?bytes=N` of file as first SSE event (backfill). Default N=8192. If file < N, send whole file.
- D-09: Poll every `?interval=N` seconds. Default 1.0. Clamp to [0.5, 10.0] silently.
- D-10: `asyncio.to_thread` for file polling. Track byte position; read from last position to EOF each poll.
- D-11: Rotation detection: `stat().st_size < last_position` → reopen from byte 0.
- D-12: SSE event `data:` field contains new log lines as plain text. No JSON wrapping.
- D-13: Cancel polling task in `finally` block.

**Dependency Injection**
- D-14: `create_logs_router(log_dir: Path) -> web.RouteTableDef`. `main.py` extracts `config.app.paths.log_dir`, passes to `serve()` → `create_app()` → `create_logs_router()`.

### Claude's Discretion
- Exact aiohttp route registration order (whether `/tail` sub-route needs to be registered before the glob or handled inside the handler via suffix check).
- Whether `asyncio.to_thread` wraps a single `Path.read_bytes()` slice or opens a file handle kept open between polls.
- SSE event `id:` field — omit (EVT-02 Last-Event-ID replay deferred to v2).

### Deferred Ideas (OUT OF SCOPE)
- EVT-02: Last-Event-ID replay for /tail
- Log search/filtering (`?contains=ERROR`)
- Log deletion (`DELETE /api/v1/logs/{filename}`)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| LOG-01 | `GET /api/v1/logs` lists all log files with filename, size_bytes, last_modified | Directory scan pattern verified; `logs/` structure confirmed on disk |
| LOG-02 | `GET /api/v1/logs/{filename}` returns file content; `?offset=N&limit=N` byte-range pagination | `Path.is_relative_to()` verified for traversal; `asyncio.to_thread` for read; headers approach defined |
| LOG-03 | `GET /api/v1/logs/{filename}/tail` SSE stream of new lines as file grows | SSE pattern from `events.py` confirmed; `asyncio.to_thread` file-handle-open-between-polls verified; rotation detection via `st_size < last_pos` verified |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Log file listing | API / Backend | — | Server filesystem access; client has no direct fs access |
| Log file content download | API / Backend | — | Server reads file; returns text/plain body with custom headers |
| SSE log tail streaming | API / Backend | — | Polling is server-side; push model; client receives SSE events |
| Path traversal validation | API / Backend | — | Must be enforced server-side; client input is untrusted |
| Byte-offset pagination | API / Backend | — | Server seeks to offset in file; client only sends query params |

## Standard Stack

No new external packages. Phase uses only what is already installed.

### Core (already in project)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| aiohttp | 3.13.5 | HTTP server, `StreamResponse` for SSE, `RouteTableDef` for routing | Already the project server [VERIFIED: live install] |
| asyncio | stdlib | `asyncio.to_thread` for blocking file I/O off the event loop | stdlib async bridge [VERIFIED: live experiment] |
| pathlib | stdlib | `Path.is_relative_to()` for traversal validation, `glob()` for listing | stdlib path API [VERIFIED: live experiment] |
| datetime | stdlib | ISO 8601 timestamps from `stat().st_mtime` via `datetime.fromtimestamp(..., tz=timezone.utc).isoformat()` | stdlib [ASSUMED] |

### No New Packages Required

No `pip install` or `uv add` steps are needed for this phase.

## Package Legitimacy Audit

**No new packages in this phase.** All dependencies already exist in `pyproject.toml`.

## Architecture Patterns

### System Architecture Diagram

```
Client Request
     |
     v
aiohttp UrlDispatcher
     |
     +-- GET /api/v1/logs           --> list_logs() handler
     |      |
     |      +-- scan log_dir/*.log (asyncio.to_thread)
     |      +-- scan log_dir/episodes/*/*.log (asyncio.to_thread)
     |      +-- return {"app_logs": [...], "episode_logs": {...}}
     |
     +-- GET /api/v1/logs/{tail:.*}/tail   --> tail_log() handler [registered FIRST]
     |      |
     |      +-- validate tail -> Path.is_relative_to(log_dir) -> 400 if bad
     |      +-- open file handle (asyncio.to_thread)
     |      +-- backfill: seek to max(0, size - bytes_param), read -> SSE event
     |      +-- poll loop: read new bytes -> SSE event (asyncio.to_thread, interval clamp)
     |      +-- rotation: st_size < last_pos -> reopen from 0
     |      +-- disconnect: finally block cancels poll task, closes file
     |      +-- return StreamResponse (text/event-stream)
     |
     +-- GET /api/v1/logs/{tail:.*}        --> read_log() handler [registered SECOND]
            |
            +-- validate tail -> Path.is_relative_to(log_dir) -> 400 if bad
            +-- ?offset=N, ?limit=N -> byte seek + read (asyncio.to_thread)
            +-- set X-Log-Size, X-Log-Offset, X-Log-Limit headers
            +-- return Response (text/plain; charset=utf-8)
```

### Recommended Project Structure

```
api/
├── routes/
│   ├── db.py           # existing
│   ├── events.py       # existing — SSE template to follow
│   └── logs.py         # NEW — create_logs_router(log_dir: Path)
api/server.py           # add log_dir param to create_app(), wire create_logs_router
main.py                 # pass cfg.app.paths.log_dir to serve()
tests/
└── test_api_logs.py    # NEW — 100% coverage required
```

### Pattern 1: create_logs_router Factory

Identical factory shape to `create_db_router`. Log dir is captured via closure.

```python
# Source: api/routes/db.py (existing project pattern)
def create_logs_router(log_dir: Path) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/logs")
    async def list_logs(request: web.Request) -> web.Response: ...

    # CRITICAL: register /tail BEFORE the glob
    @routes.get("/api/v1/logs/{tail:.*}/tail")
    async def tail_log(request: web.Request) -> web.StreamResponse: ...

    @routes.get("/api/v1/logs/{tail:.*}")
    async def read_log(request: web.Request) -> web.Response: ...

    return routes
```

### Pattern 2: Path Traversal Validation

```python
# Source: verified via live experiment
def _validate_path(log_dir: Path, tail: str) -> Path:
    target = (log_dir / tail).resolve()
    if not target.is_relative_to(log_dir.resolve()):
        raise web.HTTPBadRequest(text="Invalid path")
    return target
```

`Path.resolve()` on both sides collapses `..` sequences before comparison. `is_relative_to()` is available from Python 3.9+; project targets 3.12. [VERIFIED: live experiment]

### Pattern 3: SSE Tail with Open File Handle Between Polls

```python
# Source: verified via live experiment; follows events.py SSE pattern
async def tail_log(request: web.Request) -> web.StreamResponse:
    tail = request.match_info["tail"]
    log_path = _validate_path(log_dir, tail)  # raises 400 on traversal

    bytes_param = int(request.rel_url.query.get("bytes", 8192))
    interval_raw = float(request.rel_url.query.get("interval", 1.0))
    interval = max(0.5, min(10.0, interval_raw))  # clamp silently

    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    def open_and_backfill(path: Path, bytes_back: int):
        fh = open(path, "rb")  # noqa: SIM115 — kept open between polls
        size = path.stat().st_size
        start = max(0, size - bytes_back)
        fh.seek(start)
        data = fh.read()
        return fh, fh.tell(), data

    fh, pos, backfill = await asyncio.to_thread(open_and_backfill, log_path, bytes_param)
    try:
        if backfill:
            await resp.write(f"data: {backfill.decode('utf-8', errors='replace')}\n\n".encode())

        while True:
            await asyncio.sleep(interval)

            def poll(fh, last_pos, path):
                current_size = path.stat().st_size
                if current_size < last_pos:  # rotation detected
                    fh.seek(0)
                    last_pos = 0
                else:
                    fh.seek(last_pos)
                data = fh.read()
                return data, fh.tell()

            new_data, pos = await asyncio.to_thread(poll, fh, pos, log_path)
            if new_data:
                await resp.write(f"data: {new_data.decode('utf-8', errors='replace')}\n\n".encode())
    finally:
        fh.close()
    return resp  # pragma: no cover
```

### Pattern 4: Log Listing

```python
# Source: verified via live experiment using real logs/ directory
def _list_logs(log_dir: Path) -> dict:
    def _entry(f: Path) -> dict:
        stat = f.stat()
        rel = f.relative_to(log_dir)
        return {
            "filename": str(rel),
            "size_bytes": stat.st_size,
            "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }

    app_logs = [_entry(f) for f in sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)]

    episode_logs: dict[str, list] = {}
    episodes_dir = log_dir / "episodes"
    if episodes_dir.exists():
        for feed_dir in sorted(episodes_dir.iterdir()):
            if feed_dir.is_dir():
                slug = feed_dir.name
                episode_logs[slug] = [
                    _entry(f) for f in sorted(feed_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
                ]

    return {"app_logs": app_logs, "episode_logs": episode_logs}
```

### Pattern 5: Byte-Range Pagination

```python
# Source: derived from D-07 decisions
def _read_slice(path: Path, offset: int | None, limit: int | None) -> tuple[bytes, int, int, int]:
    data = path.read_bytes()
    total = len(data)
    start = offset or 0
    chunk = data[start:start + limit] if limit is not None else data[start:]
    return chunk, total, start, len(chunk)
```

Note: for large files this loads the whole file into memory. For a log viewer context this is acceptable. An `asyncio.to_thread` wrapper is required since `path.read_bytes()` is blocking. [ASSUMED — no explicit size limit in requirements; acceptable for log files]

### Pattern 6: create_app Wiring

```python
# Source: api/server.py (existing pattern — adding log_dir parameter)
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
    config_path: Path,
    log_dir: Path,  # NEW
) -> web.Application:
    ...
    app.add_routes(create_logs_router(log_dir))
    ...
```

`main.py:serve()` extracts `cfg.app.paths.log_dir` and passes it. All existing `create_app()` call sites in tests use `MagicMock` configs — they will need `tmp_path` passed as `log_dir` or a dedicated kwarg.

### Anti-Patterns to Avoid

- **Wrong route order:** Never register `{tail:.*}` (glob) before `{tail:.*}/tail` in the same `RouteTableDef`. Verified: the glob wins and the tail handler is unreachable. [VERIFIED: live experiment]
- **Blocking file I/O on event loop:** Never call `open()`, `read()`, `seek()`, or `Path.stat()` directly in async handlers. Always wrap in `asyncio.to_thread`. [ASSUMED — standard asyncio rule; enforced by CLAUDE.md]
- **New file handle per poll:** Opening and closing a file handle each poll cycle adds unnecessary OS overhead. Keep the handle open between polls, close in `finally`. [VERIFIED: live experiment confirms handle state is preserved between polls]
- **`web.run_app()`:** CLAUDE.md hard constraint — always use `AppRunner` + `TCPSite`. [CITED: CLAUDE.md]
- **Modulo operator in log messages:** CLAUDE.md requires f-strings. [CITED: CLAUDE.md]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Path traversal detection | Custom string checks for `..` | `Path.resolve().is_relative_to()` | String checks miss encoded variants; `resolve()` normalizes all |
| Async file I/O | Custom thread pool | `asyncio.to_thread` | stdlib; zero config; correct event loop integration |
| SSE protocol framing | Custom serializer | `data: {text}\n\n` directly (as in events.py) | SSE is simple; existing pattern in codebase is correct |
| Route dispatch for multi-segment paths | URL decoding + split | `{tail:.*}` aiohttp pattern | aiohttp handles slashes in tail match natively |

## Runtime State Inventory

SKIPPED — this is a greenfield feature addition phase, not a rename/refactor/migration phase. No stored data, live service config, OS-registered state, secrets, or build artifacts reference anything being changed.

## Common Pitfalls

### Pitfall 1: Route Registration Order
**What goes wrong:** `GET /api/v1/logs/foo.log/tail` returns a file read response instead of SSE because the glob pattern `/api/v1/logs/{tail:.*}` matched first, capturing `"foo.log/tail"` as the tail value.
**Why it happens:** aiohttp `UrlDispatcher` is FIFO — first matching route wins. The `{tail:.*}` pattern matches any suffix including `/tail`.
**How to avoid:** Always register `routes.get("/api/v1/logs/{tail:.*}/tail")` before `routes.get("/api/v1/logs/{tail:.*}")` in `create_logs_router()`.
**Warning signs:** `/tail` endpoint returns `text/plain` instead of `text/event-stream`. [VERIFIED: live experiment]

### Pitfall 2: create_app Signature Change Breaks Existing Tests
**What goes wrong:** Adding `log_dir: Path` to `create_app()` breaks all 30+ existing test files that call `create_app()` without the new argument.
**Why it happens:** All test files use `create_app(bus, time.monotonic(), RunState(), _make_config(), tmp_path / "config.yaml")` positionally.
**How to avoid:** Add `log_dir: Path` as the last positional parameter (matching D-14 intent), or use `log_dir: Path = Path("logs")` as a default. Update all existing call sites. The planner must allocate a task for this migration.
**Warning signs:** `TypeError: create_app() missing 1 required positional argument: 'log_dir'` across all test files. [VERIFIED: reading all existing test files]

### Pitfall 3: SSE Disconnect Not Cleaning Up File Handle
**What goes wrong:** Client disconnects; file handle stays open indefinitely; polling loop continues consuming CPU.
**Why it happens:** `aiohttp` cancels the handler coroutine on disconnect; without a `finally` block, cleanup code after the loop body never runs.
**How to avoid:** Always `fh.close()` inside `try/finally`, following the `events.py` pattern of unsubscribing in `finally`. [CITED: CONTEXT.md D-13, events.py]

### Pitfall 4: asyncio.to_thread Closure Over Mutable State
**What goes wrong:** The `poll` inner function references `fh` and `pos` from the enclosing scope; if called concurrently the state can be corrupted.
**Why it happens:** `asyncio.to_thread` submits a blocking function to a thread; if `poll` captures the wrong snapshot of `pos`, seeks will be wrong.
**How to avoid:** Pass `fh`, `pos`, and `log_path` as explicit arguments to the lambda/function submitted to `asyncio.to_thread`. Return new values instead of mutating captured variables. [ASSUMED — standard thread safety practice]

### Pitfall 5: Path Traversal via Encoded Slashes
**What goes wrong:** A request to `/api/v1/logs/%2F%2Fetc%2Fpasswd` bypasses a naive string check.
**Why it happens:** aiohttp URL-decodes the path before matching; the `tail` match_info value may already be decoded.
**How to avoid:** Use `Path(log_dir / tail).resolve()` then `.is_relative_to()`. The `resolve()` call normalizes the path on the OS level regardless of encoding. [VERIFIED: `..` traversal caught by live experiment; encoded traversal prevented by same mechanism]

## Code Examples

Verified patterns from official sources and live experiments:

### LOG-01: List Endpoint Full Shape
```python
# Source: verified via live experiment with real logs/ directory
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from aiohttp import web

def _list_logs_sync(log_dir: Path) -> dict:
    def entry(f: Path) -> dict:
        s = f.stat()
        return {
            "filename": str(f.relative_to(log_dir)),
            "size_bytes": s.st_size,
            "last_modified": datetime.fromtimestamp(s.st_mtime, tz=timezone.utc).isoformat(),
        }
    app_logs = [entry(f) for f in sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)]
    episode_logs: dict[str, list] = {}
    episodes_dir = log_dir / "episodes"
    if episodes_dir.exists():
        for feed_dir in sorted(episodes_dir.iterdir()):
            if feed_dir.is_dir():
                episode_logs[feed_dir.name] = [
                    entry(f) for f in sorted(feed_dir.glob("*.log"), key=lambda p: p.stat().st_mtime)
                ]
    return {"app_logs": app_logs, "episode_logs": episode_logs}

@routes.get("/api/v1/logs")
async def list_logs(request: web.Request) -> web.Response:
    result = await asyncio.to_thread(_list_logs_sync, log_dir)
    return web.json_response(result)
```

### LOG-02: Download with Byte Pagination
```python
# Source: derived from D-06, D-07 decisions; asyncio.to_thread pattern from live experiment
@routes.get("/api/v1/logs/{tail:.*}")
async def read_log(request: web.Request) -> web.Response:
    tail = request.match_info["tail"]
    log_path = _validate_path(log_dir, tail)  # raises HTTPBadRequest on traversal
    if not await asyncio.to_thread(log_path.exists):
        raise web.HTTPNotFound()

    try:
        raw_offset = request.rel_url.query.get("offset")
        raw_limit = request.rel_url.query.get("limit")
        offset = int(raw_offset) if raw_offset is not None else None
        limit = int(raw_limit) if raw_limit is not None else None
    except ValueError:
        raise web.HTTPBadRequest(text="offset and limit must be integers") from None

    def read_slice(path: Path) -> tuple[bytes, int, int, int]:
        data = path.read_bytes()
        total = len(data)
        start = offset or 0
        chunk = data[start : start + limit] if limit is not None else data[start:]
        return chunk, total, start, len(chunk)

    chunk, total, start, returned = await asyncio.to_thread(read_slice, log_path)

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

### Route Registration Order (critical)
```python
# Source: verified by live experiment — wrong order swallows /tail requests
routes = web.RouteTableDef()

# MUST come first — more specific pattern
@routes.get("/api/v1/logs/{tail:.*}/tail")
async def tail_log(request: web.Request) -> web.StreamResponse: ...

# MUST come second — glob catches everything else
@routes.get("/api/v1/logs/{tail:.*}")
async def read_log(request: web.Request) -> web.Response: ...
```

### SSE Tail Disconnect Pattern
```python
# Source: events.py (existing project pattern) adapted for file polling
resp = web.StreamResponse()
resp.headers["Content-Type"] = "text/event-stream"
resp.headers["Cache-Control"] = "no-cache"
resp.headers["X-Accel-Buffering"] = "no"
await resp.prepare(request)

fh = await asyncio.to_thread(open, log_path, "rb")
try:
    # ... backfill + poll loop ...
    pass
finally:
    await asyncio.to_thread(fh.close)
return resp  # pragma: no cover
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `inotify`/OS file-watch events | `asyncio.to_thread` stat+read polling | N/A — decision in CONTEXT.md D-10 | Simpler; no OS-specific deps; sufficient for local log tailing |
| WebSocket for live tail | SSE (one-way push) | N/A — project standard | Matches existing events.py SSE architecture |
| Log files as JSON | `text/plain` raw content | N/A — decision in CONTEXT.md D-06 | Simpler; clients can display directly |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Large log files loaded fully into memory for byte-pagination (read_bytes()) is acceptable | Code Examples (LOG-02) | If log files grow to GB scale, handler will OOM; mitigate by adding streaming read or size cap |
| A2 | `asyncio.to_thread(fh.close)` is the correct way to close a file handle opened in a thread | SSE Tail code example | Closing an OS file handle is fast enough to do synchronously; `fh.close()` in finally without to_thread is also acceptable |
| A3 | `log_dir` may not exist at server start (no logs written yet) — listing should return empty lists, not 404 | Pattern 4 (Log Listing) | If listing raises FileNotFoundError on missing log_dir, endpoint breaks before any pipeline run |

## Open Questions

1. **`create_app()` signature migration**
   - What we know: All existing test files call `create_app()` without `log_dir`. There are ~8 test files that call `create_app()` directly.
   - What's unclear: Should `log_dir` be a required positional parameter or default to `Path("logs")`?
   - Recommendation: Make `log_dir` a required parameter (matches `create_db_router` pattern per D-14). Update all call sites — grep shows they all use `MagicMock` configs anyway, so `tmp_path` works for tests.

2. **`GET /api/v1/logs` with no logs directory**
   - What we know: `log_dir` may not exist if the server is started but no pipeline run has been executed.
   - Recommendation: Return `{"app_logs": [], "episode_logs": {}}` when `log_dir` does not exist — mirror how `events.py` handles idle state gracefully.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| aiohttp | HTTP server and SSE | ✓ | 3.13.5 | — |
| Python asyncio | asyncio.to_thread | ✓ | stdlib (3.12) | — |
| pathlib | Path traversal, glob | ✓ | stdlib (3.12) | — |
| pytest-asyncio | async test execution | ✓ | ≥0.24 | — |

No missing dependencies.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 0.24+ |
| Config file | `pyproject.toml` (`asyncio_mode = "auto"`) |
| Quick run command | `uv run pytest tests/test_api_logs.py -x` |
| Full suite command | `uv run pytest --cov=. && uv run ruff` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LOG-01 | GET /api/v1/logs returns hierarchical JSON | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-01 | app_logs contains top-level .log files | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-01 | episode_logs grouped by feed slug | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-01 | empty response when no logs exist | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-02 | GET /api/v1/logs/{filename} returns full content | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-02 | ?offset=N&limit=N returns correct byte slice | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-02 | X-Log-Size, X-Log-Offset, X-Log-Limit headers set | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-02 | Content-Type is text/plain; charset=utf-8 | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-02 | path traversal returns 400 | unit | `uv run pytest tests/test_api_logs.py::TestLogSecurity -x` | ❌ Wave 0 |
| LOG-02 | missing file returns 404 | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-03 | GET /api/v1/logs/{filename}/tail returns text/event-stream | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | backfill: last N bytes sent as first SSE event | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | new content appended to file appears as SSE event | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | rotation detected (st_size < last_pos) restarts from 0 | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | path traversal on tail endpoint returns 400 | unit | `uv run pytest tests/test_api_logs.py::TestLogSecurity -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_api_logs.py tests/test_api_server.py -x`
- **Per wave merge:** `uv run pytest --cov=. && uv run ruff`
- **Phase gate:** Full suite green + 100% coverage before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_api_logs.py` — all LOG-01, LOG-02, LOG-03 tests
- [ ] `api/routes/logs.py` — module to create

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | N/A (v1 no auth per SEC-01 deferred) |
| V3 Session Management | no | N/A |
| V4 Access Control | no | N/A (v1 no auth) |
| V5 Input Validation | yes | `Path.resolve().is_relative_to()` for path traversal; integer validation for offset/limit/bytes/interval |
| V6 Cryptography | no | N/A (read-only, no secrets) |

### Known Threat Patterns for File-Serving Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal (`../`) | Information Disclosure | `(log_dir / tail).resolve().is_relative_to(log_dir.resolve())` — return 400 |
| Large file DoS (huge ?limit) | DoS | No explicit size cap in requirements; [ASSUMED] log files are bounded by rotation config |
| Symlink escape | Information Disclosure | `Path.resolve()` follows symlinks — a symlink inside `logs/` pointing outside will bypass the check. Not addressed in requirements (local tool, trusted operator). |

## Sources

### Primary (HIGH confidence)
- Live experiment: aiohttp 3.13.5 route order verification (`{tail:.*}/tail` before `{tail:.*}`)
- Live experiment: `asyncio.to_thread` with open file handle between polls — state preserved correctly
- Live experiment: `Path.is_relative_to()` traversal detection — catches `..` after `resolve()`
- Live experiment: actual `logs/` directory structure — top-level `.log` files + `episodes/<slug>/*.log`
- `api/routes/events.py` — SSE pattern (existing project code)
- `api/routes/db.py` — factory pattern, `create_db_router(deps)` shape
- `api/server.py` — `create_app()` signature and wiring
- `main.py` — `cfg.app.paths.log_dir` as the config key for log directory
- `config/config_loader.py` — `PathsConfig.log_dir: Path` confirmed

### Secondary (MEDIUM confidence)
- aiohttp official docs (via WebFetch): FIFO route matching confirmed
- CONTEXT.md decisions D-01 through D-14: all locked decisions from discuss-phase

### Tertiary (LOW confidence)
- None

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH — no new packages; all existing
- Architecture: HIGH — patterns verified by live experiment + existing codebase
- Pitfalls: HIGH — route order bug verified experimentally; others from code reading
- Security: HIGH — traversal protection verified experimentally

**Research date:** 2026-05-22
**Valid until:** 2026-06-22 (stable stack; aiohttp 3.x API is stable)
