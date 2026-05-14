# Phase 1: API Foundation — Research

**Researched:** 2026-05-14
**Domain:** aiohttp web server, asyncio event bus, dual-mode CLI entry
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**EventBus Design**
- D-01: `emit()` accepts a typed event dataclass — `PipelineEvent` with a `type` discriminator field. Type-safe, mypy-checkable, straightforward to serialize to JSON for SSE.
- D-02: Broadcast-all subscription model — every subscriber queue receives every event. One `asyncio.Queue` per connected SSE client.
- D-03: Define the full `PipelineEventType` enum now (all expected types: episode stage transitions, download/encode progress, run-level counters) even though Phase 1 won't emit them.
- D-04: Drop silently when no subscribers — `emit()` is a no-op if the subscriber list is empty. No buffering.

**API Layer Structure**
- D-05: New `api/` top-level package — nothing in `components/` or `utils/` knows about HTTP.
- D-06: `EventBus` lives at `api/event_bus.py` — owned by the API package, passed into `Pipeline` as dependency injection.
- D-07: Routes organized as one file per phase domain: `api/routes/health.py` (Phase 1), `api/routes/events.py` (Phase 2), etc.
- D-08: API server as a factory function `create_app(event_bus: EventBus) -> web.Application` — aiohttp convention, easy to test with `TestClient`.

**main.py Dual-Mode Entry**
- D-09: Extract `serve()` coroutine from `main()` — `main()` dispatches on `args.serve`.
- D-10: In serve mode, pipeline runs only on API request (Phase 3). Phase 1 server idles.
- D-11: Host/port via CLI args only — `--host` (default `0.0.0.0`) and `--port` (default `8080`).

**Health Check Response**
- D-12: Version from `importlib.metadata.version('podcast-ad-cutter')`.
- D-13: Health response shape: `{"status": "ok", "uptime_seconds": 123.4, "version": "0.1.0"}`.
- D-14: Standard error envelope: success returns resource directly; errors return `{"error": "message", "detail": {...}}`.

### Claude's Discretion

- Exact `PipelineEvent` dataclass field names and `PipelineEventType` enum member names.
- `api/__init__.py` contents — minimal; expose only `create_app` at package level.
- Whether `serve()` coroutine lives in `main.py` or is extracted to `api/server.py` — prefer `api/server.py` for testability.

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFRA-01 | Server starts in API mode when `--serve` flag is passed to `main.py`; bare invocation still runs pipeline once and exits | AppRunner + TCPSite pattern, argparse extension, `serve()` coroutine |
| INFRA-02 | `GET /api/v1/health` returns 200 with server uptime and version | `web.json_response`, `time.monotonic()`, `importlib.metadata` / `tomllib` fallback |
</phase_requirements>

---

## Summary

Phase 1 builds the thinnest possible walking skeleton: a dual-mode entry point, an idling aiohttp server, a health check endpoint, an `EventBus` class, and `Pipeline` accepting an optional `EventBus`. No pipeline control, no SSE streaming — just the structural scaffolding every subsequent phase builds on.

`aiohttp` is already a project dependency (version 3.13.5 in the lockfile). The `AppRunner` + `TCPSite` pattern mandated by CLAUDE.md is well-documented and straightforward. `aiohttp.test_utils.TestClient` wraps the factory `create_app()` function cleanly for unit tests. The one non-obvious issue is version resolution: `importlib.metadata.version('podcast-ad-cutter')` fails today because `pyproject.toml` has no `[build-system]` table and the package is not installed as a distribution. The fallback is to read the version directly from `pyproject.toml` using the stdlib `tomllib` module (Python 3.11+).

The `EventBus` is pure asyncio — `asyncio.Queue` instances in a list, no external libraries required. The typed event dataclass pattern (`PipelineEvent` + `PipelineEventType` enum) integrates naturally with the project's Pydantic/dataclass conventions.

**Primary recommendation:** Scaffold `api/` package, `api/event_bus.py`, `api/routes/health.py`, and `api/server.py` as a coherent vertical slice; wire into `main.py` via `--serve` flag.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Server lifecycle (start/stop/cleanup) | API Layer (`api/server.py`) | CLI Layer (`main.py`) | `AppRunner`/`TCPSite` live in `api/server.py`; `main.py` only dispatches |
| Health check endpoint | API Layer (`api/routes/health.py`) | — | Pure HTTP handler; no business logic |
| Event publication | API Layer (`api/event_bus.py`) | Orchestration Layer (`components/pipeline.py`) | EventBus owned by API; Pipeline receives it by injection |
| Dual-mode dispatch | CLI Layer (`main.py`) | — | argparse owns mode selection; `main()` delegates to `run_pipeline()` or `serve()` |
| Version resolution | API Layer (`api/routes/health.py`) | — | Read at handler level; no config layer involved |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `aiohttp` | 3.13.5 (locked) | HTTP server, routing, JSON responses | Already a project dep; native async; SSE built-in; no new dep |
| `asyncio` (stdlib) | 3.12 | Event bus queues, server keep-alive loop | Single event loop shared by server and pipeline |
| `tomllib` (stdlib) | 3.12 | Read version from `pyproject.toml` | Fallback for version when package lacks distribution metadata |
| `importlib.metadata` (stdlib) | 3.12 | Primary version resolution path | Works once `[build-system]` is added to `pyproject.toml` |
| `time.monotonic()` (stdlib) | 3.12 | Uptime tracking | Monotonic clock, unaffected by system time changes |

[VERIFIED: uv.lock — aiohttp 3.13.5]
[VERIFIED: Bash — `uv run python -c "import aiohttp; print(aiohttp.__version__)"` → 3.13.5]

### Supporting (test)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `aiohttp.test_utils.TestClient` | bundled with aiohttp | Drive HTTP requests against `create_app()` in tests | All route handler tests |
| `aiohttp.test_utils.TestServer` | bundled with aiohttp | Wrap the `web.Application` for in-process testing | Used with `TestClient` |
| `pytest-asyncio` | 0.24+ (locked) | Async test support; `asyncio_mode = "auto"` active | All async tests (no decorator needed) |

[VERIFIED: Context7 /aio-libs/aiohttp — TestClient/TestServer documented]
[VERIFIED: pyproject.toml — pytest-asyncio>=0.24, asyncio_mode = "auto"]

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `tomllib` fallback for version | `importlib.metadata` only | `importlib.metadata` fails without `[build-system]` in `pyproject.toml`; `tomllib` always works |
| `asyncio.Queue` in EventBus | `asyncio.PriorityQueue` | No priority needed; broadcast model is FIFO |
| `RouteTableDef` decorators | `app.router.add_get(path, handler)` | Either works; `add_get` in factory function is more explicit and easier to test |

**Installation:** No new runtime dependencies — `aiohttp` is already in `pyproject.toml`.

---

## Architecture Patterns

### System Architecture Diagram

```
CLI args (--serve / bare)
          │
          ▼
    main.py:main()
    ┌─────────────────────────────────────────────┐
    │  parse_args() → --serve?                    │
    │        │ yes              │ no              │
    │        ▼                 ▼                  │
    │  await serve(cfg)   await run_pipeline(cfg) │
    └─────────────────────────────────────────────┘
               │
               ▼
         api/server.py:serve(cfg, host, port)
         ┌──────────────────────────────────┐
         │  start_time = time.monotonic()   │
         │  event_bus = EventBus()          │
         │  app = create_app(event_bus)     │
         │  AppRunner(app).setup()          │
         │  TCPSite(runner, host, port)     │
         │  .start()                        │
         │  asyncio.Event().wait()  ←───────┼── keeps process alive
         │  runner.cleanup()                │
         └──────────────────────────────────┘
                  │
                  ▼ dependency injection
         api/event_bus.py:EventBus
         ┌──────────────────────────────────┐
         │  _subscribers: list[Queue]       │
         │  subscribe() → Queue             │
         │  unsubscribe(queue)              │
         │  emit(event: PipelineEvent)      │
         └──────────────────────────────────┘
                  │
                  ▼ (Phase 3+: also passed to Pipeline)
         components/pipeline.py:Pipeline
         ┌──────────────────────────────────┐
         │  __init__(cfg, event_bus=None)   │
         │  (Phase 1: EventBus unused here) │
         └──────────────────────────────────┘

GET /api/v1/health
          │
          ▼
    api/routes/health.py
    ┌──────────────────────────────────────────┐
    │  elapsed = time.monotonic() - start_time │
    │  version = _read_version()               │
    │  return json_response({                  │
    │    "status": "ok",                       │
    │    "uptime_seconds": elapsed,            │
    │    "version": version,                   │
    │  })                                      │
    └──────────────────────────────────────────┘
```

### Recommended Project Structure

```
api/
├── __init__.py          # exposes create_app only
├── event_bus.py         # EventBus class + PipelineEvent dataclass + PipelineEventType enum
├── server.py            # serve() coroutine — AppRunner + TCPSite lifecycle
└── routes/
    ├── __init__.py
    └── health.py        # GET /api/v1/health handler + create_health_router()

tests/
└── test_api_*.py        # one test file per api/ module
    test_api_event_bus.py
    test_api_health.py
    test_api_server.py
```

### Pattern 1: Application Factory

**What:** `create_app(event_bus)` builds and returns a configured `web.Application`. No side effects, no network binding.
**When to use:** Always. Factory makes the app testable with `TestClient` without binding to a port.

```python
# Source: https://github.com/aio-libs/aiohttp/blob/master/docs/testing.md
from aiohttp import web
from api.event_bus import EventBus
from api.routes.health import create_health_router

def create_app(event_bus: EventBus) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(event_bus))
    return app
```

### Pattern 2: AppRunner + TCPSite (non-blocking serve)

**What:** Start the server without `web.run_app()` so the coroutine stays in the asyncio event loop.
**When to use:** Always in server mode — `web.run_app()` is forbidden (blocks the loop).

```python
# Source: https://github.com/aio-libs/aiohttp/blob/master/docs/web_reference.md
import asyncio
from aiohttp import web

async def serve(app: web.Application, host: str, port: int) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    # Keep alive until cancelled (e.g., KeyboardInterrupt propagated from main())
    stop_event = asyncio.Event()
    await stop_event.wait()   # blocks until set or task cancelled
    await runner.cleanup()
```

### Pattern 3: TestClient for Route Tests

**What:** Wrap `create_app()` in `TestClient(TestServer(app))` — no real port binding, full HTTP stack exercised.
**When to use:** All handler tests.

```python
# Source: https://github.com/aio-libs/aiohttp/blob/master/docs/testing.md
from aiohttp.test_utils import TestClient, TestServer
from api.server import create_app
from api.event_bus import EventBus

async def test_health_returns_200() -> None:
    app = create_app(EventBus())
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "version" in data
```

### Pattern 4: EventBus — asyncio.Queue broadcast

**What:** Each subscriber gets its own `asyncio.Queue`; `emit()` iterates and `put_nowait()`s. `subscribe()` returns the queue; `unsubscribe()` removes it (SSE `finally` block calls this).
**When to use:** Always — no alternative for Phase 1.

```python
# [ASSUMED] — pattern derived from Python asyncio stdlib docs and project conventions
import asyncio
from dataclasses import dataclass
from enum import StrEnum

class PipelineEventType(StrEnum):
    # Stage transitions
    EPISODE_STAGE_CHANGED = "episode.stage_changed"
    # Progress
    DOWNLOAD_PROGRESS = "episode.download_progress"
    ENCODE_PROGRESS = "episode.encode_progress"
    # Run-level counters
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    EPISODE_COMPLETED = "episode.completed"
    EPISODE_FAILED = "episode.failed"

@dataclass
class PipelineEvent:
    type: PipelineEventType
    payload: dict  # serializable; exact shape defined per event type in Phase 2

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
        for q in self._subscribers:
            q.put_nowait(event)
```

### Pattern 5: Version Resolution

**What:** Try `importlib.metadata` first; fall back to reading `pyproject.toml` with `tomllib`.
**Why:** The project currently has no `[build-system]` table in `pyproject.toml`, so `importlib.metadata.version('podcast-ad-cutter')` raises `PackageNotFoundError` in the current uv virtual environment.

```python
# [VERIFIED: Bash probe — PackageNotFoundError confirmed without [build-system]]
import importlib.metadata
import tomllib
from pathlib import Path

def _read_version() -> str:
    try:
        return importlib.metadata.version("podcast-ad-cutter")
    except importlib.metadata.PackageNotFoundError:
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        return data["project"]["version"]
```

[VERIFIED: Bash — `tomllib.load(open('pyproject.toml','rb'))['project']['version']` returns `'0.1'`]

### Anti-Patterns to Avoid

- **`web.run_app()`:** Blocks the asyncio event loop. CLAUDE.md mandates `AppRunner` + `TCPSite` instead.
- **Module-level EventBus singleton:** Contradicts the project's stateless-class-with-constructor-injection pattern. EventBus must be instantiated in `serve()` and passed down.
- **Importing `Config` inside `api/`:** Violates config isolation. `api/server.py:serve()` receives a `Config` instance from `main.py` and extracts what it needs (host, port) before passing to `create_app`.
- **Sharing aiosqlite connection between pipeline and API handlers:** CLAUDE.md hard rule — API needs its own read-only connection in later phases; establish the pattern now by not passing the pipeline's DB connection to any API handler.
- **`asyncio.sleep(3600)` keep-alive loop:** Wastes CPU on context switches. Use `asyncio.Event().wait()` or task cancellation instead.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP routing + response | Custom socket server | `aiohttp.web` (already dep) | Handles keepalive, chunked encoding, content-type negotiation |
| In-process test HTTP client | `urllib` / raw sockets | `aiohttp.test_utils.TestClient` | Full stack test without binding a real port; supported by pytest-asyncio auto mode |
| JSON serialization | `json.dumps()` in handlers | `web.json_response(data)` | Sets `Content-Type: application/json`, handles encoding |

**Key insight:** `aiohttp` already provides every primitive needed for this phase. The only custom code is the `EventBus` broadcast logic, which is ~15 lines of asyncio stdlib.

---

## Common Pitfalls

### Pitfall 1: `importlib.metadata.version()` PackageNotFoundError

**What goes wrong:** Health handler raises `PackageNotFoundError` at request time; server returns 500.
**Why it happens:** `pyproject.toml` has no `[build-system]` table, so `uv` manages the venv without installing the project as a proper distribution.
**How to avoid:** Use the dual-strategy `_read_version()` function (Pattern 5 above). Long term: add `[build-system]` to `pyproject.toml` if the project ever needs packaging.
**Warning signs:** `PackageNotFoundError: No package metadata was found for podcast-ad-cutter` in logs.

[VERIFIED: Bash probe — confirmed PackageNotFoundError in current environment]

### Pitfall 2: `web.run_app()` blocks the asyncio loop

**What goes wrong:** Server starts but `main()` never returns; KeyboardInterrupt handling breaks; future pipeline integration impossible.
**Why it happens:** `web.run_app()` calls `loop.run_forever()` internally.
**How to avoid:** Always use `AppRunner.setup()` + `TCPSite.start()` + an awaitable keep-alive.
**Warning signs:** `main()` does not exit cleanly on Ctrl+C; cannot `await` anything after server start.

[CITED: CLAUDE.md — "Never use `web.run_app()` — use `AppRunner` + `TCPSite` instead"]

### Pitfall 3: EventBus list mutation during iteration

**What goes wrong:** `RuntimeError: list changed size during iteration` if `unsubscribe()` is called while `emit()` is iterating.
**Why it happens:** SSE disconnect (Phase 2) can trigger `unsubscribe()` concurrently with an `emit()` call.
**How to avoid:** Iterate over a snapshot: `for q in list(self._subscribers):`. Do this now even though Phase 1 has no SSE clients.
**Warning signs:** `RuntimeError` in `emit()` during concurrent tests.

[ASSUMED]

### Pitfall 4: `asyncio_mode = "auto"` interaction with `TestClient`

**What goes wrong:** Tests using `async with TestClient(...)` work in isolation but produce unexpected behaviour when mixed with sync fixtures.
**Why it happens:** pytest-asyncio `auto` mode wraps every async test in its own event loop by default.
**How to avoid:** Keep all test functions `async def`; use `async with TestClient(TestServer(app)) as client:` pattern (context manager form). Do not call `asyncio.run()` inside tests.
**Warning signs:** `ScopeMismatch` or `RuntimeError: Event loop is closed` in test output.

[VERIFIED: Context7 /aio-libs/aiohttp — TestClient context manager pattern confirmed]

### Pitfall 5: `Pipeline` signature breakage

**What goes wrong:** Existing 759 tests that construct `Pipeline(cfg, feed_name=...)` fail because the new `event_bus` parameter changes the signature.
**Why it happens:** Adding a required positional parameter to `Pipeline.__init__`.
**How to avoid:** Add `event_bus: EventBus | None = None` as a keyword-only parameter with a default of `None`. Existing call sites require zero changes.
**Warning signs:** Test failures across `test_pipeline.py` and any test that imports Pipeline.

[VERIFIED: Bash — existing pipeline tests use `Pipeline(cfg, feed_name=...)` pattern]

---

## Code Examples

### Minimal health route module

```python
# api/routes/health.py
# Source pattern: https://github.com/aio-libs/aiohttp/blob/master/docs/web_quickstart.md
import importlib.metadata
import time
import tomllib
from pathlib import Path

from aiohttp import web


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

### Serve coroutine (api/server.py)

```python
# api/server.py
# Source: https://github.com/aio-libs/aiohttp/blob/master/docs/web_advanced.md
import asyncio
import time
from aiohttp import web
from api.event_bus import EventBus
from api.routes.health import create_health_router


def create_app(event_bus: EventBus, start_time: float) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(start_time))
    return app


async def serve(host: str, port: int) -> None:
    start_time = time.monotonic()
    event_bus = EventBus()
    app = create_app(event_bus, start_time)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    # Wait indefinitely; cancelled by KeyboardInterrupt in main()
    await asyncio.Event().wait()
    await runner.cleanup()
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `web.run_app()` for server start | `AppRunner` + `TCPSite` | aiohttp v3 | Allows server + pipeline to share the same asyncio loop |
| `asyncio.get_event_loop()` | `asyncio.run()` in `__main__` | Python 3.10 | Already correct in existing `main.py` |

**Deprecated/outdated:**
- `web.run_app()`: Blocks the caller; incompatible with same-process pipeline sharing. Do not use.
- `asyncio.get_event_loop()` calls: Deprecated in 3.10+; project already uses `asyncio.run()` correctly.

---

## Runtime State Inventory

Step 2.5: SKIPPED — this is a greenfield phase adding new files; no existing runtime state embeds names being changed.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| aiohttp | HTTP server | ✓ | 3.13.5 | — |
| aiohttp.test_utils | Route tests | ✓ | bundled | — |
| pytest-asyncio | Async tests | ✓ | 0.24+ | — |
| Python tomllib | Version fallback | ✓ | stdlib 3.11+ | — |
| Python importlib.metadata | Primary version | ✓ (stdlib) | 3.12 | tomllib fallback |

[VERIFIED: Bash — `uv run python -c "import aiohttp; print(aiohttp.__version__)"` → 3.13.5]
[VERIFIED: Bash — `uv run pytest -x --tb=no -q` → 759 passed (baseline green)]

**Missing dependencies with no fallback:** None.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2+ with pytest-asyncio 0.24+ |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — `asyncio_mode = "auto"` |
| Quick run command | `uv run pytest tests/test_api_*.py -x` |
| Full suite command | `uv run pytest --cov=. -x` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| INFRA-01 | `--serve` starts server; bare invocation still runs pipeline | unit | `uv run pytest tests/test_main.py -x` | ✅ (existing, needs --serve branch) |
| INFRA-01 | `serve()` sets up AppRunner + TCPSite and keeps alive | unit | `uv run pytest tests/test_api_server.py -x` | ❌ Wave 0 |
| INFRA-02 | `GET /api/v1/health` returns 200 + correct JSON shape | unit | `uv run pytest tests/test_api_health.py -x` | ❌ Wave 0 |
| INFRA-02 | Version field non-empty, uptime_seconds is a float | unit | `uv run pytest tests/test_api_health.py::test_health_version -x` | ❌ Wave 0 |
| D-01/D-02 | EventBus subscribe/unsubscribe/emit broadcast | unit | `uv run pytest tests/test_api_event_bus.py -x` | ❌ Wave 0 |
| D-03 | PipelineEventType enum has all required members | unit | `uv run pytest tests/test_api_event_bus.py::test_event_types -x` | ❌ Wave 0 |
| D-04 | emit() with no subscribers is a no-op (no error) | unit | `uv run pytest tests/test_api_event_bus.py::test_emit_no_subscribers -x` | ❌ Wave 0 |
| Pipeline compat | Pipeline(cfg) still works without event_bus arg | regression | `uv run pytest tests/test_pipeline.py -x` | ✅ (existing — will catch breakage) |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_api_*.py tests/test_main.py tests/test_pipeline.py -x`
- **Per wave merge:** `uv run pytest --cov=. -x`
- **Phase gate:** Full suite green + 100% coverage before `/gsd-verify-work`

### Wave 0 Gaps

- [ ] `tests/test_api_event_bus.py` — covers D-01, D-02, D-03, D-04
- [ ] `tests/test_api_health.py` — covers INFRA-02
- [ ] `tests/test_api_server.py` — covers INFRA-01 serve() lifecycle

*(Existing `tests/test_main.py` must be extended with `--serve` dispatch tests.)*

---

## Security Domain

> `security_enforcement` not set in config.json — treating as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | No auth in v1 (locked decision) |
| V3 Session Management | No | Stateless API |
| V4 Access Control | No | Local network only; v2 concern |
| V5 Input Validation | Minimal | Health endpoint has no inputs |
| V6 Cryptography | No | No secrets transmitted |

### Known Threat Patterns for aiohttp / local API

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Unintentional public binding | Information disclosure | Default host `0.0.0.0` — document that this exposes the port on all interfaces; operator responsibility for firewall |
| Path traversal in future log endpoints | Tampering | Not applicable in Phase 1; flag for Phase 6 (LOG-01/LOG-02) |

> Phase 1 exposes only a read-only health endpoint with no inputs. ASVS surface is minimal.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | EventBus list mutation pitfall (iterate over snapshot) | Common Pitfalls #3 | If aiohttp serializes all coroutine execution around event bus calls, the race may never manifest — but the snapshot pattern is still correct practice |
| A2 | `PipelineEventType` enum member names and `PipelineEvent.payload: dict` field | Code Examples / EventBus pattern | If Phase 2 SSE serialization requires a different shape, the enum/dataclass will need updating |

---

## Open Questions

1. **`asyncio.Event().wait()` vs. task cancellation for server keep-alive**
   - What we know: `await asyncio.Event().wait()` with no setter will block forever. The server exits when the enclosing task is cancelled (e.g., `KeyboardInterrupt` → `asyncio.run()` cancels all tasks).
   - What's unclear: Whether a more explicit shutdown signal (e.g., `asyncio.Event` set by a signal handler) is needed for clean teardown in Docker.
   - Recommendation: For Phase 1, simple cancellation is sufficient. Add `handle_signals=True` to `AppRunner` if SIGTERM handling is needed in Docker (that's a Phase 6 concern).

2. **`[build-system]` table in pyproject.toml**
   - What we know: Without it, `importlib.metadata.version()` fails. The `tomllib` fallback works today.
   - What's unclear: Whether the project intends to add a build backend (e.g., hatchling) for packaging.
   - Recommendation: Add `tomllib` fallback now; note that adding `[build-system]` in future will make `importlib.metadata` the working primary path.

---

## Sources

### Primary (HIGH confidence)

- Context7 `/aio-libs/aiohttp` — AppRunner, TCPSite, TestClient, TestServer, `web.json_response`, `RouteTableDef`, application signals
- Bash probe — aiohttp 3.13.5 version confirmed in project venv
- Bash probe — 759 existing tests pass (green baseline confirmed)
- Bash probe — `importlib.metadata.PackageNotFoundError` confirmed; `tomllib` fallback verified
- Project files — `main.py`, `components/pipeline.py`, `pyproject.toml`, `tests/test_main.py`, `tests/test_pipeline.py`

### Secondary (MEDIUM confidence)

- `.planning/codebase/ARCHITECTURE.md` — layer diagram, component responsibilities, anti-patterns
- `.planning/codebase/STACK.md` — dependency versions, test tooling config
- `.planning/phases/01-api-foundation/01-CONTEXT.md` — all locked decisions

### Tertiary (LOW confidence)

- A1, A2 in Assumptions Log — EventBus iteration safety and event shape (not verified against a running SSE stream)

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — aiohttp version verified in lockfile and venv; test tooling confirmed
- Architecture: HIGH — patterns verified against Context7 official aiohttp docs; integration points confirmed by reading actual source files
- Pitfalls: HIGH (P1, P2, P4, P5) / LOW (P3 — assumed, no concurrency test run)
- Version resolution: HIGH — PackageNotFoundError confirmed by Bash probe; tomllib fallback verified

**Research date:** 2026-05-14
**Valid until:** 2026-06-14 (aiohttp is a stable library; test infra is locked via uv.lock)
