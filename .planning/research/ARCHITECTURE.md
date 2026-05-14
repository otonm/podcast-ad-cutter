# Architecture: In-Process Event Bus + aiohttp REST/SSE Layer

**Context:** Adding aiohttp.web REST + SSE server to the existing async pipeline in the same asyncio event loop.
**Researched:** 2026-05-14
**Confidence:** HIGH (verified against aiohttp official docs, Python stdlib asyncio docs, and multiple implementation sources)

---

## Summary

The pipeline already has progress callbacks on `EpisodeDownloader` and `AudioPreprocessor`. Both accept `on_progress: ProgressCallback | None` and call `await on_progress(guid, percent)`. What is missing is a fan-out layer that delivers those events to zero or more SSE subscribers simultaneously, and a mechanism to fire events for the stage transitions that don't currently expose any callback (transcription, topic extraction, ad detection, audio editing).

The recommended architecture is a thin `EventBus` class that holds a set of per-subscriber `asyncio.Queue` instances. `Pipeline` receives an `EventBus` at construction time (optional, defaulting to a no-op). It emits `ProgressEvent` dataclass instances. The aiohttp `Application` holds a reference to the same `EventBus` instance via `app[EVENT_BUS_KEY]`. SSE handlers subscribe a personal queue to the bus, drain it into the HTTP response stream, and unsubscribe on disconnect. The database gets a long-lived read connection for the API layer, separate from the per-run write connection used by the pipeline.

---

## Component Map

```
main.py
  ├── parses --serve flag
  ├── constructs EventBus()
  ├── constructs Pipeline(config, event_bus=bus)  ← NEW parameter
  └── branches:
       ├── serve mode:
       │     build_app(bus, config, db_path) → aiohttp.web.Application
       │     AppRunner + TCPSite (keeps server alive)
       │     asyncio.create_task(periodic_pipeline_run())
       │     asyncio.get_event_loop().run_forever()
       └── single-run mode (unchanged):
             asyncio.run(pipeline.run())

EventBus  (new file: api/event_bus.py)
  ├── subscribe() → asyncio.Queue[ProgressEvent]
  ├── unsubscribe(queue)
  └── emit(event: ProgressEvent) — puts into every subscriber queue

ProgressEvent  (new file: api/models.py or models/progress.py)
  ├── guid: str
  ├── stage: Literal["download","preprocess","transcribe","topic","ad_detect","edit","done","error"]
  ├── percent: float | None   (None = stage entered, no percentage yet)
  ├── feed_title: str
  └── timestamp: datetime

Pipeline  (components/pipeline.py — modified)
  ├── __init__(config, event_bus: EventBus | None = None)
  ├── _event_bus: EventBus   (no-op bus if None passed)
  └── progress callback methods become: await self._event_bus.emit(ProgressEvent(...))

aiohttp.web.Application  (new package: api/)
  ├── app[EVENT_BUS_KEY]  = EventBus instance
  ├── app[DB_PATH_KEY]    = Path to data.db
  ├── app[CONFIG_KEY]     = Config instance
  ├── cleanup_ctx: read-only DB connection lifecycle
  └── routes:
       GET  /events              → SSE handler (subscribes queue, drains events)
       GET  /events/{guid}       → SSE handler filtered to one episode
       POST /pipeline/run        → trigger pipeline run as asyncio.Task
       POST /pipeline/stop       → cancel running Task
       GET  /feeds               → DB read: feed list
       GET  /episodes            → DB read: episode list with stage/status
       GET  /episodes/{guid}     → DB read: single episode detail
       GET  /transcriptions/{guid}
       GET  /ad-detections/{guid}
       GET  /costs
       GET  /settings            → return current config as JSON
       PATCH /settings           → validate + write config.yaml
       GET  /logs                → list log files
       GET  /logs/{name}         → return log content
       GET  /logs/{name}/tail    → SSE tail

Stores (database/)  — unchanged
  Used by Pipeline during a run (write connection).
  Used by API read handlers via a separate aiosqlite connection opened in cleanup_ctx.
```

---

## Event Bus Design

### Why not asyncio.Condition or bare callbacks

`asyncio.Condition.notify_all()` wakes all waiters but does not buffer events — any subscriber that is not `await condition.wait()`-ing at the exact moment of notify loses the event. That is wrong for SSE, where the subscriber task may be briefly occupied writing a previous chunk.

A list of registered callbacks (direct `await cb(event)` per subscriber) blocks emit for as long as the slowest subscriber takes to process, creating head-of-line blocking across all subscribers.

`asyncio.Queue` per subscriber is the right primitive: emit is non-blocking (uses `put_nowait` with a bounded queue and drops on `QueueFull`), each subscriber drains its own queue at its own pace, and slow or disconnected clients don't affect the pipeline.

### EventBus implementation sketch

```python
# api/event_bus.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass(frozen=True)
class ProgressEvent:
    guid: str
    stage: str          # "download" | "preprocess" | "transcribe" | "topic" | "ad_detect" | "edit" | "done" | "error"
    feed_title: str
    percent: float | None = None   # None = stage started, no progress pct available
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

_QUEUE_MAX = 64  # drop oldest events for very slow consumers rather than blocking pipeline

class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[ProgressEvent]] = set()

    def subscribe(self) -> asyncio.Queue[ProgressEvent]:
        q: asyncio.Queue[ProgressEvent] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[ProgressEvent]) -> None:
        self._subscribers.discard(q)

    def emit(self, event: ProgressEvent) -> None:
        """Non-blocking fan-out. Drops event for full queues (slow/lagging subscribers)."""
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow subscriber — drop rather than block pipeline
```

Key design choices:
- `emit` is a plain synchronous method, not `async`. It uses `put_nowait`. This means the pipeline never awaits the bus — zero pipeline latency impact from SSE subscribers.
- `QueueFull` is silently dropped. A bounded queue of 64 events is generous for a typical pipeline that emits at most one event per second per episode. If a subscriber falls 64 events behind it is likely a stale HTTP connection and deserves to skip events.
- No locks needed. All code runs on the same asyncio event loop; `set.add/discard` and `Queue.put_nowait` are safe without locks in a single-threaded event loop.

### How Pipeline emits events

`Pipeline` currently calls `_on_download_progress` and `_on_preprocess_progress` as progress callbacks. These become thin wrappers that emit to the bus:

```python
# In Pipeline.__init__:
self._bus = event_bus if event_bus is not None else EventBus()  # no-op if no subscribers

# Progress callbacks become:
async def _on_download_progress(self, guid: str, percent: float) -> None:
    self._bus.emit(ProgressEvent(guid=guid, stage="download", feed_title=self._current_feed_title, percent=percent))
    # existing stderr tty logic stays

# Stage transitions emit at entry, before the await:
# At the top of each guard block, before the component call:
self._bus.emit(ProgressEvent(guid=episode.guid, stage="transcribe", feed_title=..., percent=None))
_, transcription, ... = await self._transcriptor.transcribe(...)
self._bus.emit(ProgressEvent(guid=episode.guid, stage="transcribe", feed_title=..., percent=1.0))
```

`Pipeline` needs to know `feed_title` at emit time. Store it as `self._current_feed_title: str = ""` and set it at the top of the per-feed loop before processing episodes.

### SSE handler pattern

```python
# api/routes/events.py
async def sse_events_handler(request: web.Request) -> web.StreamResponse:
    bus: EventBus = request.app[EVENT_BUS_KEY]
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                           "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
    await response.prepare(request)
    queue = bus.subscribe()
    try:
        while not request.transport.is_closing():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                data = json.dumps(dataclasses.asdict(event), default=str)
                await response.write(f"data: {data}\n\n".encode())
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")  # SSE comment = keepalive
    finally:
        bus.unsubscribe(queue)
    return response
```

The 15-second keepalive heartbeat prevents proxies and browsers from timing out idle SSE connections. `request.transport.is_closing()` detects client disconnect.

---

## Data Flow

### Event flow (pipeline → SSE client)

```
Pipeline._process_episode_until_final()
  │  (synchronous call)
  ▼
EventBus.emit(ProgressEvent)
  │  put_nowait into each subscriber's asyncio.Queue
  ▼
[asyncio event loop schedules SSE handler coroutine]
  │
  ▼
SSE handler: queue.get() → format "data: {...}\n\n" → response.write()
  │
  ▼
HTTP client (browser/curl) receives SSE event
```

No await between pipeline work and emit. The pipeline never waits for the SSE handler. Fan-out cost is O(N subscribers) synchronous queue puts, which is negligible.

### API read flow (DB viewer endpoints)

```
GET /episodes
  │
  ▼
handler: db = request.app[READ_DB_KEY]   (long-lived read connection, WAL mode)
  │
  ▼
EpisodeStore(db).get_all_episodes()
  │
  ▼
JSON response
```

The read connection is opened once in `cleanup_ctx` and kept alive for the server lifetime. SQLite WAL mode allows concurrent reads while the pipeline holds a write transaction. No locking needed between the API read connection and pipeline's write connection — WAL handles it at the SQLite level.

```python
# api/db_context.py
async def db_read_context(app: web.Application) -> AsyncGenerator[None, None]:
    async with Database(app[DB_PATH_KEY]) as db:
        await db.conn.execute("PRAGMA journal_mode=WAL")
        app[READ_DB_KEY] = db.conn
        yield
        # cleanup_ctx: connection closed here on app shutdown

# In build_app():
app.cleanup_ctx.append(db_read_context)
```

The pipeline's existing `Database` context manager opens its connection inside `Pipeline.run()`. The API read connection is separate. Both can coexist with WAL mode enabled. The pipeline connection also needs WAL enabled (add `PRAGMA journal_mode=WAL` to `Database.__aenter__`).

---

## Dual-Mode Entry Point

### Problem

Current `main.py` calls `asyncio.run(pipeline.run())`, which runs to completion and exits. Server mode must keep running indefinitely while also being able to trigger pipeline runs on demand (via API) and optionally run them on a schedule.

### Solution: branch in main() after arg parsing

```
main()
  ├── parse_args() → args.serve: bool, args.port: int
  ├── load_config()
  ├── bus = EventBus()
  ├── pipeline = Pipeline(config, event_bus=bus)
  │
  ├── if args.serve:
  │     app = build_app(bus=bus, config=config, db_path=db_path)
  │     app[PIPELINE_KEY] = pipeline           # API handlers trigger runs via this
  │     runner = web.AppRunner(app)
  │     await runner.setup()
  │     site = web.TCPSite(runner, host, port)
  │     await site.start()
  │     logger.info(f"API server listening on http://{host}:{port}")
  │     await asyncio.Event().wait()           # sleep forever until SIGINT/SIGTERM
  │     await runner.cleanup()
  │
  └── else (single-run, unchanged):
        await pipeline.run()
```

`asyncio.Event().wait()` with no `.set()` call is the standard idiom for "run forever until cancelled". Signal handlers (`loop.add_signal_handler(SIGTERM, event.set)`) can trigger a clean shutdown.

### Pipeline run lifecycle in server mode

When a POST /pipeline/run arrives:

```python
async def trigger_run_handler(request: web.Request) -> web.Response:
    pipeline: Pipeline = request.app[PIPELINE_KEY]
    run_state: RunState = request.app[RUN_STATE_KEY]

    if run_state.task and not run_state.task.done():
        return web.Response(status=409, text="Pipeline already running")

    async def _run_with_new_db() -> None:
        # Pipeline.run() opens its own Database context per call — correct
        await pipeline.run()

    run_state.task = asyncio.create_task(_run_with_new_db())
    return web.Response(status=202, text="Pipeline run started")
```

`RunState` is a simple dataclass stored in `app[RUN_STATE_KEY]`:

```python
@dataclass
class RunState:
    task: asyncio.Task | None = None
```

For POST /pipeline/stop: `run_state.task.cancel()`. Pipeline catches `asyncio.CancelledError` naturally because all its awaits are cancellable.

---

## Build Order

The components form a dependency chain. Each layer must exist before the one above it.

| Step | What to build | Why first |
|------|--------------|-----------|
| 1 | `models/progress.py`: `ProgressEvent` dataclass | No deps; used by EventBus and Pipeline |
| 2 | `api/event_bus.py`: `EventBus` class | Depends on `ProgressEvent` only; can be unit-tested in isolation |
| 3 | Modify `Pipeline.__init__` to accept `event_bus` | Keeps existing tests green (default = no-op bus); adds emit calls in callbacks and state machine guards |
| 4 | `api/__init__.py`, `api/app.py`: `build_app()` factory | Needs EventBus, Config, db_path; returns `web.Application` with routes and cleanup_ctx |
| 5 | `api/routes/events.py`: SSE handler | Needs EventBus and aiohttp; first visible integration test |
| 6 | Modify `main.py`: `--serve` flag + dual-mode branch | Needs Pipeline (step 3) + build_app (step 4) |
| 7 | `api/routes/pipeline.py`: run/stop control endpoints | Needs RunState, Pipeline, EventBus |
| 8 | `api/routes/db_views.py`: read-only DB endpoints | Needs read DB connection from cleanup_ctx; WAL must be enabled |
| 9 | `api/routes/settings.py`: GET/PATCH config | Needs Config model; PATCH writes config.yaml |
| 10 | `api/routes/logs.py`: log list/content/tail SSE | Needs log dir from Config |
| 11 | WAL mode on pipeline connection (`Database.__aenter__`) | Required before step 8 is production-safe; can be done alongside step 4 |

**Critical path:** Steps 1 → 2 → 3 are the foundation. Nothing else works without the EventBus and its integration with Pipeline. Steps 4–6 unlock the runnable server. Steps 7–10 are individually shippable features that can be built and tested in any order after step 6 is done.

---

## Key Constraints and Pitfalls

**Do not pass EventBus into components below Pipeline.** The constraint "no component below Pipeline imports from config/" has a parallel here: no component below Pipeline should know about `EventBus`. `Pipeline` owns the bus reference and emits from its own methods. Components continue to call their `on_progress` callback; `Pipeline`'s callback implementations emit to the bus.

**Pipeline.run() opens its own Database connection every call.** This is by design (existing `async with Database(...)` in `run()`). In server mode, multiple sequential runs are fine. Concurrent runs are not safe (two writes to the same SQLite file with the same write connection semantics). The `RunState.task` guard (409 on concurrent POST /pipeline/run) enforces this.

**WAL mode must be enabled on both connections.** The pipeline write connection and the API read connection must both issue `PRAGMA journal_mode=WAL`. WAL is sticky per database file once set, so enabling it on first open is sufficient for subsequent opens, but being explicit on both connections is cleaner and documents the intent.

**aiohttp AppKey for shared state, not string keys.** Use `web.AppKey("event_bus", EventBus)` instead of string keys `app["event_bus"]`. AppKey is typed and avoids accidental key collisions between app components. This is the current aiohttp recommendation (verified against aiohttp 3.13 docs).

**SSE keepalive is required.** Without the 15-second SSE comment heartbeat, nginx/CloudFlare/browser proxies will close idle connections after 60-120 seconds. The pipeline can be silent for minutes during LLM calls.

**`build_app()` is a factory function, not a module-level singleton.** The `web.Application` is constructed fresh each time `build_app()` is called. This makes the application testable: tests can call `build_app()` with a mock `EventBus` and an in-memory SQLite path.

---

## Sources

- [aiohttp Web Server Advanced (cleanup_ctx, AppKey, app[] patterns)](https://docs.aiohttp.org/en/stable/web_advanced.html) — HIGH confidence
- [aiohttp-sse README — sse_response context manager](https://github.com/aio-libs/aiohttp-sse/blob/master/README.rst) — HIGH confidence
- [Python asyncio.Queue docs — put_nowait, QueueFull](https://docs.python.org/3/library/asyncio-queue.html) — HIGH confidence
- [SQLite WAL mode — concurrent reads during writes](https://sqlite.org/wal.html) — HIGH confidence
- [asyncio-multisubscriber-queue — per-subscriber queue fan-out pattern](https://github.com/smithk86/asyncio-multisubscriber-queue) — MEDIUM confidence (library reference, core pattern is stdlib)
- [Asyncio pubsub patterns — fan-out with per-subscriber queues](https://codepr.github.io/posts/asyncio-pubsub/) — MEDIUM confidence
- [aiohttp AppRunner + TCPSite programmatic lifecycle](https://docs.aiohttp.org/en/stable/web_advanced.html) — HIGH confidence
- [Backpressure with bounded queues and put_nowait drop strategy](https://tech-champion.com/programming/python-programming/manage-async-i-o-backpressure-using-bounded-queues-and-timeouts/) — MEDIUM confidence
