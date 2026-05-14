# Technology Stack: REST + SSE API Layer

**Project:** podcast-ad-cutter — Web API milestone
**Researched:** 2026-05-14
**aiohttp version in use:** 3.13.5 (pinned in uv.lock)
**Confidence:** HIGH (verified against aiohttp 3.13.5 official docs + aio-libs source)

---

## Summary

aiohttp 3.13.5 is already pinned in the project. It ships `aiohttp.web` as part of the same package — no new dependency is needed for the HTTP server. The `AppRunner` + `TCPSite` pattern is the correct way to run aiohttp.web inside an existing asyncio event loop, which is exactly the architecture this project needs: both the pipeline coroutine and the API server share the same loop.

SSE is implemented natively via `web.StreamResponse` with `content_type="text/event-stream"`. No third-party SSE library is needed or advisable — `aiohttp-sse` (the official aio-libs add-on) is inactive/unmaintained as of 2025. State sharing between pipeline and API handlers is done through typed `web.AppKey` entries on the `Application` object, combined with per-SSE-client `asyncio.Queue` instances for event fan-out.

For CORS, a lightweight inline middleware is the cleanest approach at this scale. The only external candidate (`aiohttp-middlewares 2.4.0`) adds a dependency for a feature that is 15 lines of code; `aiohttp-cors 0.7.0` (also aio-libs) has not been updated since 2019 and has known route-registration friction. Write the CORS middleware inline.

---

## Recommended Stack

### Core API Framework

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `aiohttp.web` | 3.13.5 (already installed) | HTTP API server + SSE streaming | Already a project dependency; native async; SSE via StreamResponse; runs inside existing event loop with AppRunner/TCPSite — no ASGI stack needed |

No new top-level dependency is required for the server itself.

### CORS

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Inline middleware | — | Set CORS headers for future web UI | aiohttp-cors 0.7.0 is unmaintained (last release 2019); aiohttp-middlewares adds a dep for trivial code; inline is 10–15 lines and under full control |

### Supporting Libraries (already installed, no changes needed)

| Library | Version | Role in API |
|---------|---------|------------|
| `aiosqlite` | ≥0.22.1 | DB viewer endpoints — short-lived read connection per request |
| `pydantic` | ≥2 | Validate and serialize JSON request/response bodies |
| `pyyaml` | ≥6 | Config PATCH endpoint reads/writes config.yaml |

---

## Key Patterns

### 1. Application Setup with `RouteTableDef`

Define routes in each module without importing the app instance. Collect and register at startup.

```python
# api/routes/pipeline.py
from aiohttp import web

routes = web.RouteTableDef()

@routes.get("/api/pipeline/status")
async def get_status(request: web.Request) -> web.Response:
    queue: asyncio.Queue[dict] = request.app[APP_QUEUE_KEY]
    ...
    return web.json_response({"status": "idle"})
```

```python
# api/app.py
from aiohttp import web
from api.routes import pipeline, feeds, settings

def build_app(shared_state: SharedState) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app[SHARED_STATE_KEY] = shared_state
    app.router.add_routes(pipeline.routes)
    app.router.add_routes(feeds.routes)
    app.router.add_routes(settings.routes)
    return app
```

`RouteTableDef` avoids circular imports and keeps route modules decoupled from the app singleton. This is the recommended aiohttp pattern since 2.3 and remains standard in 3.13.

### 2. Running the Server Alongside the Pipeline (`AppRunner` + `TCPSite`)

**Never use `web.run_app()`** in this project. It is blocking and takes over the event loop. Use `AppRunner` + `TCPSite` instead — they are async and integrate naturally with `asyncio.gather`.

```python
# main.py (serve mode)
async def run_server_mode(config: AppConfig, port: int = 8080) -> None:
    shared_state = SharedState()
    app = build_app(shared_state)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"API server listening on http://0.0.0.0:{port}")

    try:
        # Both run concurrently in the same event loop.
        # Pipeline runs once; server stays alive until cancelled.
        await asyncio.gather(
            run_pipeline_forever(config, shared_state),
            asyncio.Event().wait(),   # keep-alive placeholder
        )
    finally:
        await runner.cleanup()
```

`AppRunner.setup()` initializes the application. `TCPSite.start()` begins accepting connections. Both are awaitable and non-blocking. `runner.cleanup()` must be called on shutdown to trigger `on_shutdown` and `on_cleanup` signals.

### 3. `cleanup_ctx` for Background Tasks Tied to App Lifetime

For tasks that must start with the server and stop with it (e.g., a pipeline scheduler loop), use `cleanup_ctx`. It guarantees cleanup runs even if startup raises.

```python
async def pipeline_context(app: web.Application) -> AsyncGenerator[None, None]:
    shared = app[SHARED_STATE_KEY]
    task = asyncio.create_task(pipeline_loop(shared))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

app.cleanup_ctx.append(pipeline_context)
```

Use `cleanup_ctx` (preferred over `on_startup`/`on_shutdown` pairs) because it co-locates setup and teardown in one generator.

### 4. Typed Application State with `web.AppKey`

Do not use string keys. As of aiohttp 3.9+, string keys trigger `NotAppKeyWarning`. Use `web.AppKey` for type-safe, mypy-compatible state.

```python
# api/state.py
from aiohttp import web
from api.shared import SharedState

SHARED_STATE_KEY: web.AppKey[SharedState] = web.AppKey("shared_state", SharedState)
SSE_SUBSCRIBERS_KEY: web.AppKey[set[asyncio.Queue[dict]]] = web.AppKey(
    "sse_subscribers", set
)
```

Access in handlers:

```python
async def handler(request: web.Request) -> web.Response:
    state = request.app[SHARED_STATE_KEY]
    ...
```

### 5. Server-Sent Events (SSE) via `web.StreamResponse`

SSE requires no library. The wire protocol is plain text: each event is `data: <payload>\n\n`. Use `web.StreamResponse` with chunked encoding disabled and the correct content-type.

```python
@routes.get("/api/events")
async def sse_handler(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # disable nginx buffering if proxied
    await response.prepare(request)

    # Register this client's queue.
    subscribers: set[asyncio.Queue[dict]] = request.app[SSE_SUBSCRIBERS_KEY]
    queue: asyncio.Queue[dict] = asyncio.Queue()
    subscribers.add(queue)

    try:
        while True:
            event = await queue.get()
            payload = json.dumps(event)
            await response.write(f"data: {payload}\n\n".encode())
    except (asyncio.CancelledError, ConnectionResetError):
        pass  # client disconnected
    finally:
        subscribers.discard(queue)

    return response
```

**SSE wire format reference:**
- `data: <json>\n\n` — minimal, all clients parse this
- `event: <type>\ndata: <json>\n\n` — named event type (optional; use for multi-stream discriminated union)
- `id: <str>\ndata: <json>\n\n` — allows client `Last-Event-ID` reconnect (optional)

For this project, start with `data:` only. Add named events if the web UI needs to distinguish progress vs. log vs. run-level events.

### 6. Fan-Out Event Bus (Pipeline → SSE Clients)

Pipeline components must not know about the web layer. Use an in-process event bus backed by `asyncio.Queue` fan-out: pipeline emits to a central dispatcher, dispatcher puts into each subscriber's queue.

```python
# api/event_bus.py
import asyncio
from dataclasses import dataclass, field

@dataclass
class EventBus:
    _subscribers: set[asyncio.Queue[dict]] = field(default_factory=set)

    def subscribe(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        self._subscribers.discard(q)

    def emit(self, event: dict) -> None:
        """Fire-and-forget. Drops events for slow clients (queue full)."""
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow client; drop rather than block pipeline
```

The pipeline receives an `EventBus` instance (injected via `Pipeline.__init__`) and calls `bus.emit({"type": "stage_progress", ...})`. The SSE handler subscribes/unsubscribes per connection.

`maxsize=256` on each queue caps memory per slow client. At 256 events, `put_nowait` raises `QueueFull` and the event is dropped for that client — the pipeline is never blocked.

### 7. Inline CORS Middleware

```python
# api/middleware.py
from aiohttp import web

ALLOWED_ORIGINS = {"http://localhost:3000", "http://localhost:5173"}

@web.middleware
async def cors_middleware(
    request: web.Request, handler: web.Handler
) -> web.StreamResponse:
    origin = request.headers.get("Origin", "")

    if request.method == "OPTIONS":
        # Preflight
        response = web.Response()
    else:
        response = await handler(request)

    if origin in ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "3600"

    return response
```

Register at app build time: `web.Application(middlewares=[cors_middleware])`.

In aiohttp 3.x the `@web.middleware` decorator is still required (it becomes optional/no-op in the 4.0 alpha). Keep it for 3.13.5 compatibility.

### 8. Dual-Mode Entry Point (`--serve` flag)

```python
# main.py
async def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.serve:
        await run_server_mode(config, port=args.port)
    else:
        # Original single-run behavior; exits after pipeline completes.
        pipeline = Pipeline(config)
        await pipeline.run()
```

`run_server_mode` starts `AppRunner`, then runs the pipeline coroutine and the server together via `asyncio.gather`. The server stays alive indefinitely; the pipeline coroutine becomes a scheduler loop that re-runs on demand (triggered via API).

---

## Version Notes

| Fact | Detail | Confidence |
|------|--------|------------|
| aiohttp version | 3.13.5 (pinned in uv.lock) | HIGH — read directly from uv.lock |
| `web.AppKey` introduced | ~3.9; triggers `NotAppKeyWarning` if string keys used in 3.9+ | HIGH — verified via multiple official doc versions |
| `@web.middleware` decorator | Required in 3.x; no-op/deprecated in 4.0 alpha | HIGH — confirmed via changelog + 4.0 alpha docs |
| `aiohttp-sse` (aio-libs) | 2.2.0, last release mid-2024, marked inactive by Snyk | MEDIUM — Snyk health report + PyPI |
| `aiohttp-cors` | 0.7.0, last release 2019, route-registration friction | MEDIUM — PyPI + GitHub issue history |
| `aiohttp-middlewares` | 2.4.0, active, but adds dep for trivial code | MEDIUM — PyPI |
| SSE via `StreamResponse` | Stable and unchanged pattern since aiohttp 2.x | HIGH — official docs, multiple versions consistent |
| `AppRunner` + `TCPSite` | Stable since aiohttp 3.0; recommended for event loop integration | HIGH — official docs 3.8–3.13 consistent |

---

## What NOT to Do

### Do not use `web.run_app()`
It is a blocking convenience wrapper. It calls `asyncio.run()` internally, which creates a new event loop and blocks the calling thread. There is no way to run the pipeline alongside it without threads or subprocesses. Use `AppRunner` + `TCPSite` instead.

### Do not use `aiohttp-sse`
The `aiohttp-sse` library (aio-libs/aiohttp-sse) is inactive as of 2025. `web.StreamResponse` with `text/event-stream` is all you need. Adding a library for 10 lines of protocol framing creates a maintenance liability.

### Do not use `aiohttp-cors`
Last release 2019. It requires registering each route individually with the cors config object — incompatible with the `RouteTableDef` decorator pattern unless you wrap every route at registration time. Write the inline middleware instead.

### Do not use string keys for `app[...]`
`app["my_key"] = value` triggers `NotAppKeyWarning` in aiohttp 3.9+. mypy cannot type-check string-keyed app state. Use `web.AppKey` for every state slot.

### Do not block the event loop in handlers
`aiosqlite` is async — use it. File I/O for log tailing must use `asyncio.to_thread` or `aiofiles`. Any synchronous call that takes more than ~1ms will stall all SSE streams and in-flight requests.

### Do not share a single `asyncio.Queue` across all SSE clients
A single queue serializes consumption — one slow client starves all others. Give each SSE connection its own queue and use the fan-out `EventBus.emit()` pattern to copy events into each.

### Do not use `@web.middleware` decorator in aiohttp 4.x
If the project ever upgrades to aiohttp 4.x, remove the `@web.middleware` decorator — it will be a no-op and may be removed entirely. For 3.13.5 it is required.

### Do not call `asyncio.create_task` from non-async context
The event bus `emit()` method is synchronous (intentionally, for calling from pipeline without awaiting). It uses `put_nowait()` — never `await queue.put()` — to avoid blocking. Pipeline code should call `emit()` not `await emit()`.

---

## Sources

- [aiohttp 3.13.5 — Web Server Advanced (AppRunner, TCPSite, cleanup_ctx)](https://docs.aiohttp.org/en/stable/web_advanced.html)
- [aiohttp 3.13.5 — Web Server Quickstart (RouteTableDef)](https://docs.aiohttp.org/en/stable/web_quickstart.html)
- [aiohttp 3.13.5 — Server Reference (AppKey)](https://docs.aiohttp.org/en/stable/web_reference.html)
- [aiohttp 3.13.5 — Changelog](https://docs.aiohttp.org/en/stable/changes.html)
- [aiohttp-sse — GitHub (aio-libs/aiohttp-sse)](https://github.com/aio-libs/aiohttp-sse)
- [aiohttp-sse — PyPI health (Snyk Advisor)](https://snyk.io/advisor/python/aiohttp-sse)
- [aiohttp-cors — GitHub (aio-libs/aiohttp-cors)](https://github.com/aio-libs/aiohttp-cors)
- [aiohttp-middlewares 2.4.0 — PyPI](https://pypi.org/project/aiohttp-middlewares/)
- [aiohttp-middlewares — CORS middleware source](https://aiohttp-middlewares.readthedocs.io/en/stable/_modules/aiohttp_middlewares/cors.html)
- [MDN — Using server-sent events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)
