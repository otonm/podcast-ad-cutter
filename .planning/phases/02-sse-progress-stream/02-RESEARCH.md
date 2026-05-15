# Phase 2: SSE Progress Stream - Research

## Summary

Phase 2 wires `emit()` calls into the existing pipeline state machine and exposes `GET /api/v1/events` as a Server-Sent Events endpoint. All seven `PipelineEventType` members were defined in Phase 1 with no new enum members needed; Phase 2 only adds payload shapes, counter fields to `_Stores`, and the SSE route itself. The work splits cleanly into three independent units: pipeline emit instrumentation, `_Stores` counter fields, and the `api/routes/events.py` route — all connected through the already-complete `EventBus`.

---

## Codebase State (Phase 1 Deliverables)

### `api/event_bus.py` — Fully implemented, nothing to change

```python
class PipelineEventType(StrEnum):
    EPISODE_STAGE_CHANGED = "episode.stage_changed"
    DOWNLOAD_PROGRESS    = "episode.download_progress"
    ENCODE_PROGRESS      = "episode.encode_progress"
    RUN_STARTED          = "run.started"
    RUN_COMPLETED        = "run.completed"
    EPISODE_COMPLETED    = "episode.completed"
    EPISODE_FAILED       = "episode.failed"

class EventBus:
    def subscribe(self) -> asyncio.Queue[PipelineEvent]: ...
    def unsubscribe(self, q: asyncio.Queue[PipelineEvent]) -> None: ...
    def emit(self, event: PipelineEvent) -> None: ...  # broadcast-all, list() snapshot
```

`emit()` uses `list(self._subscribers)` snapshot — safe for concurrent unsubscribe. `put_nowait()` is used, meaning it never blocks the pipeline coroutine.

### `api/server.py` — `create_app()` factory, one import to add

```python
def create_app(event_bus: EventBus, start_time: float) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(start_time))
    return app
```

The `EventBus` instance is already stored on `app["event_bus"]` and passed as a parameter — the events router just needs to be registered alongside the health router.

### `api/routes/health.py` — The exact pattern to replicate

```python
def create_health_router(start_time: float) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/health")
    async def health(_request: web.Request) -> web.Response:
        return web.json_response({...})

    return routes
```

`create_events_router(event_bus: EventBus) -> web.RouteTableDef` must follow this pattern exactly.

### `components/pipeline.py` — State machine, `_Stores`, callbacks

**`_Stores` dataclass** (`slots=True`):
```python
@dataclass(slots=True)
class _Stores:
    episode: EpisodeStore
    transcription: TranscriptionStore
    audio_metadata: AudioMetadataStore
    cost: CostTrackingStore
    topic: TopicStore
    ad: AdStore
    transcribed_guids: set[str]
    extracted_guids: set[str]
    ad_detected_guids: set[str]
```
Three new `int` fields must be added: `episodes_done: int`, `episodes_failed: int`, `episodes_total: int`. With `slots=True` there is no `__dict__` — all fields must be declared in the dataclass body; no dynamic attribute assignment allowed.

**`Pipeline.__init__`** already has:
```python
self._event_bus: EventBus | None = event_bus
```
The guard pattern used throughout is `if self._event_bus is not None: self._event_bus.emit(...)`.

**`_on_download_progress(self, guid: str, percent: float)`** — async callback wired to `EpisodeDownloader.download(on_progress=...)`. Currently only logs to stderr/logger.

**`_on_preprocess_progress(self, guid: str, percent: float)`** — same pattern, wired to `AudioPreprocessor.preprocess(on_progress=...)`.

**State machine structure** (`_process_episode_until_final`):
- Guard 1: output file exists → update URL → `return`
- Guard 2: ad detection cached → parse → edit or copy → `return`
- Guard 3: topic extracted → run ad detection → `continue`
- Guard 4: transcript exists → extract topic → `continue`
- Guard 5: audio on disk → probe + preprocess + transcribe → `continue`
- Bottom: no audio → download → `continue`

`feed_slug` is a local variable in the outer `run()` loop that calls `_process_episode_until_final`. The `_process_episode_until_final` signature already receives `feed_slug: str`.

**`main.py` note**: `serve()` creates an `EventBus` but does NOT pass it to `Pipeline` — that wiring is Phase 3's job. Phase 2 only needs the pipeline to have emit calls in place and the SSE route to exist.

---

## Implementation Approach

### 1. Add counter fields to `_Stores`

```python
@dataclass(slots=True)
class _Stores:
    # ... existing fields ...
    episodes_done: int
    episodes_failed: int
    episodes_total: int
```

`_Stores` is constructed once per feed in `Pipeline.run()`. `episodes_total` is the length of the `episodes` list at construction time. `episodes_done` and `episodes_failed` start at `0` and are incremented after each episode outcome (after `EPISODE_COMPLETED` / `EPISODE_FAILED` emit).

### 2. Pipeline emit instrumentation

**RUN_STARTED / RUN_COMPLETED** — emitted in `Pipeline.run()` around the outer feed loop, not inside `_process_episode_until_final`. `total_episodes` is the sum of `len(episodes)` for each feed. `feeds` is the list of feed slugs.

**EPISODE_STAGE_CHANGED** — two emits per stage (started/completed), inside `_process_episode_until_final`. Each guard action emits `started` before the component call and `completed` after the DB write. Stage name string values: `"download"`, `"preprocess"`, `"transcribe"`, `"topic"`, `"ad-detect"`, `"edit"`. Guards 1 and 2 that just return (no state transition to a new stage) do NOT emit stage events — they are terminal exits, not stage starts.

Pattern inside each guard:
```python
if self._event_bus is not None:
    self._event_bus.emit(PipelineEvent(
        type=PipelineEventType.EPISODE_STAGE_CHANGED,
        payload={"guid": episode.guid, "stage": "download", "status": "started", "feed_slug": feed_slug},
    ))
# ... component call ...
# ... DB write ...
if self._event_bus is not None:
    self._event_bus.emit(PipelineEvent(
        type=PipelineEventType.EPISODE_STAGE_CHANGED,
        payload={"guid": episode.guid, "stage": "download", "status": "completed", "feed_slug": feed_slug},
    ))
```

**DOWNLOAD_PROGRESS / ENCODE_PROGRESS** — emitted in `_on_download_progress` and `_on_preprocess_progress`. The challenge: these callbacks only receive `(guid, percent)` — `feed_slug` must be captured via closure or passed through. The cleanest approach is a closure: when calling `download()` in the state machine, pass a lambda or nested async function that closes over `feed_slug` and `episode.guid`, then calls both the existing log logic and the emit. Alternatively, store `feed_slug` as a temporary instance attribute or restructure the callbacks. The closure-per-call approach avoids modifying the callback signature and is consistent with how closures already appear elsewhere.

Example closure pattern (avoids modifying `_on_download_progress` signature and keeps existing log logic intact):
```python
async def _on_dl_progress(guid: str, percent: float) -> None:
    await self._on_download_progress(guid, percent)  # existing logs
    if self._event_bus is not None:
        self._event_bus.emit(PipelineEvent(
            type=PipelineEventType.DOWNLOAD_PROGRESS,
            payload={"guid": guid, "feed_slug": feed_slug, "percent": percent},
        ))
raw_path = await self._episode_downloader.download(episode.guid, url, on_progress=_on_dl_progress)
```

**EPISODE_COMPLETED / EPISODE_FAILED** — emitted after each episode, in the episode loop inside `Pipeline.run()` (the `try/except/finally` block that calls `_process_episode_until_final`). On success, `stores.episodes_done += 1` then emit. On exception, `stores.episodes_failed += 1` then emit. Counter values from `stores` at time of emit.

### 3. `api/routes/events.py` — SSE handler

```python
from __future__ import annotations
import json
import logging
from aiohttp import web
from api.event_bus import EventBus

logger = logging.getLogger(__name__)

def create_events_router(event_bus: EventBus) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/events")
    async def events(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        await resp.prepare(request)

        q = event_bus.subscribe()
        try:
            while True:
                event = await q.get()
                data = f"event: {event.type}\ndata: {json.dumps(event.payload)}\n\n"
                await resp.write(data.encode())
        finally:
            event_bus.unsubscribe(q)

        return resp

    return routes
```

Key details:
- `web.StreamResponse` not `web.Response` — streaming, not buffered.
- `await resp.prepare(request)` must be called before any `write()`.
- `await resp.write(bytes)` — takes bytes not str.
- SSE wire format: `event: {type}\ndata: {json}\n\n` (double newline terminates each event).
- `unsubscribe()` in `finally` block — mandatory per CLAUDE.md.
- `ConnectionResetError` / `asyncio.CancelledError` on client disconnect will propagate out of `await q.get()` or `await resp.write()` — the `finally` block fires regardless, ensuring cleanup.

### 4. Register route in `create_app()`

```python
from api.routes.events import create_events_router

def create_app(event_bus: EventBus, start_time: float) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(start_time))
    app.add_routes(create_events_router(event_bus))  # add this
    return app
```

---

## Integration Points

| What | Where | How |
|------|-------|-----|
| `_Stores` counter fields | `components/pipeline.py` | Add `episodes_done: int = 0`, `episodes_failed: int = 0`, `episodes_total: int` to the `@dataclass(slots=True)` body |
| `episodes_total` construction | `Pipeline.run()`, `_Stores(...)` call | Pass `episodes_total=len(episodes)` when constructing `_Stores` |
| RUN_STARTED emit | `Pipeline.run()` | Before outer feed loop, after `episodes` lists are counted |
| RUN_COMPLETED emit | `Pipeline.run()` | After all feeds complete |
| Stage emits | `_process_episode_until_final()` | One `started` + one `completed` per guard action |
| EPISODE_COMPLETED / FAILED emit | `Pipeline.run()` episode try/except | After `_process_episode_until_final` returns or raises |
| DOWNLOAD_PROGRESS emit | `_on_download_progress` or closure | Keep existing log behavior; add emit if event_bus is not None |
| ENCODE_PROGRESS emit | `_on_preprocess_progress` or closure | Same pattern |
| SSE route | `api/routes/events.py` | New file, `create_events_router(event_bus)` factory |
| Route registration | `api/server.py` `create_app()` | Add `app.add_routes(create_events_router(event_bus))` |

---

## Test Strategy

### `tests/test_api_events.py` — SSE route tests

Use the same `TestClient(TestServer(app))` pattern established in `test_api_health.py`. However, testing a streaming SSE handler with `TestClient` requires reading the response incrementally or writing a known number of events before disconnecting.

**Pattern for SSE tests:**

```python
from aiohttp.test_utils import TestClient, TestServer
from api.event_bus import EventBus, PipelineEvent, PipelineEventType
from api.server import create_app

async def test_sse_returns_200_with_event_stream_content_type() -> None:
    bus = EventBus()
    app = create_app(bus, 0.0)
    async with TestClient(TestServer(app)) as client:
        async with client.get("/api/v1/events") as resp:
            assert resp.status == 200
            assert "text/event-stream" in resp.headers["Content-Type"]
```

For testing that events are delivered:
```python
async def test_sse_delivers_event_payload() -> None:
    import asyncio, json
    bus = EventBus()
    app = create_app(bus, 0.0)
    async with TestClient(TestServer(app)) as client:
        async with client.get("/api/v1/events") as resp:
            event = PipelineEvent(type=PipelineEventType.RUN_STARTED, payload={"feeds": ["slug-a"]})
            bus.emit(event)
            chunk = await resp.content.read(1024)
            text = chunk.decode()
            assert "run.started" in text
            assert "slug-a" in text
```

For disconnect cleanup test — verify unsubscribe is called:
```python
async def test_sse_unsubscribes_on_disconnect() -> None:
    bus = EventBus()
    app = create_app(bus, 0.0)
    async with TestClient(TestServer(app)) as client:
        async with client.get("/api/v1/events"):
            pass  # context exit closes connection
    assert len(bus._subscribers) == 0
```

**Headers tests**: verify `Cache-Control: no-cache` and `X-Accel-Buffering: no` are set.

**Multiple clients test**: two concurrent `client.get("/api/v1/events")` contexts; emit one event; both should receive it (verified via `_subscribers` count or by reading chunks from both).

### `tests/test_pipeline.py` additions — emit call verification

The existing test file uses extensive mock patching. For emit tests, pass a real or mock `EventBus`:

```python
async def test_pipeline_emits_run_started_with_event_bus() -> None:
    bus = EventBus()
    q = bus.subscribe()
    config = ... # existing _branch_config helper
    pipeline = Pipeline(config, event_bus=bus)
    # mock all components as existing tests do
    await pipeline.run()
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e.type for e in events]
    assert PipelineEventType.RUN_STARTED in types
    assert PipelineEventType.RUN_COMPLETED in types
```

Alternatively, use a `MagicMock` for `EventBus` and assert `emit.call_args_list`:
```python
mock_bus = MagicMock(spec=EventBus)
pipeline = Pipeline(config, event_bus=mock_bus)
await pipeline.run()
call_types = [c.args[0].type for c in mock_bus.emit.call_args_list]
assert PipelineEventType.EPISODE_STAGE_CHANGED in call_types
```

**`_Stores` counter field tests**: verify `episodes_done` increments after a successful episode and `episodes_failed` after an exception. Check EPISODE_COMPLETED payload contains correct `feed_done`, `feed_failed`, `feed_total`.

**Progress callback tests**: call `_on_download_progress` directly with a mock event_bus attached; verify `emit` is called with the correct payload type and `percent` value.

### 100% coverage requirements

Every new line must be covered:
- `api/routes/events.py`: happy-path SSE connection, headers, payload format, multiple subscribers, disconnect cleanup (`finally` branch).
- `components/pipeline.py` additions: all new emit calls (with `event_bus=None` to cover the guard branch, and with a real/mock bus to cover the emit branch); counter increments for both success and failure paths; `episodes_total` in `_Stores` construction.

The `if self._event_bus is not None:` guard means every emit line needs two test scenarios: one with `event_bus=None` (existing pipeline tests already cover this) and one with a bus injected.

---

## Validation Architecture

End-to-end verification (manual, not automated in this phase):
1. Start server with `uv run python main.py --serve`.
2. In a second terminal: `curl -N http://localhost:8080/api/v1/events`.
3. In a third terminal: trigger pipeline (CLI mode or Phase 3's `POST /api/v1/run`).
4. Observe SSE events streaming to the curl client.

Automated verification via tests:
- `uv run pytest tests/test_api_events.py tests/test_pipeline.py -v`
- `uv run pytest --cov=. --cov-report=term-missing` — confirm 100%
- `uv run ruff check` — confirm no lint errors

---

## Risks & Landmines

### 1. `slots=True` and new `_Stores` fields — ordering matters
With `slots=True`, all fields must be declared in the dataclass. The three new fields (`episodes_done`, `episodes_failed`, `episodes_total`) must have defaults or be positional in the right order at construction. The existing `_Stores` construction in `Pipeline.run()` uses keyword arguments — add the three new fields with defaults (`episodes_done: int = 0`, `episodes_failed: int = 0`) but `episodes_total` must be passed explicitly since it's computed. Check that the construction call in `run()` is updated — if not, Python will raise a `TypeError` at runtime, not caught by ruff/mypy alone.

### 2. SSE client disconnect handling — two failure points
Client disconnect can raise at two points: `await q.get()` (if the event loop is cancelled) or `await resp.write(...)` (if the TCP connection is gone, raises `ConnectionResetError`). The `finally: event_bus.unsubscribe(q)` handles both, but the test must explicitly verify the `finally` path fires. Without this test, coverage will miss the branch.

### 3. `asyncio.CancelledError` vs `ConnectionResetError`
In aiohttp, when a client disconnects mid-stream, aiohttp raises `asyncio.CancelledError` on the handler coroutine (the task is cancelled). `ConnectionResetError` may also appear depending on the OS and proxy. Both are non-fatal — `finally` handles cleanup. Do NOT catch `asyncio.CancelledError` inside the loop; let it propagate to the `finally`.

### 4. `queue.get()` blocks forever when no events arrive
The SSE handler does `await q.get()` which blocks until an event arrives. In tests, if no event is emitted, the test will hang. Use `asyncio.wait_for(q.get(), timeout=1.0)` in tests, or emit an event before reading. Plan tests to always emit at least one event before asserting.

### 5. Progress callback `feed_slug` capture
`_on_download_progress(self, guid, percent)` does not receive `feed_slug`. The closure approach (defining an inner async function per download call site that captures `feed_slug` from the enclosing scope) is correct. However, there are TWO download call sites in `_process_episode_until_final`: one at the bottom (new episode) and one inside Guard 2 (re-download for cached detection). Both must emit the correct `feed_slug`. Easy to miss the Guard 2 re-download path.

### 6. RUN_STARTED `total_episodes` computation timing
`total_episodes` (sum of episodes across all feeds) must be computed before the outer feed loop starts, but `episodes` lists are built inside the feed loop. Either do a two-pass approach (count first, then process) or accumulate. The simpler approach: emit `RUN_STARTED` with `total_episodes` computed as the sum of `len(episodes)` per feed — but this requires all feeds' episode lists to be known first, which requires moving the `RUN_STARTED` emit after all `get_episodes_for_feed()` calls. Alternatively, emit `RUN_STARTED` before the feed loop with just the feed slugs and defer `total_episodes` to a separate initial pass, or restructure. The decision context (D-08) says `total_episodes` is required — so a pre-count pass or a two-loop design is needed.

### 7. `emit()` is synchronous but callbacks are async
`EventBus.emit()` uses `put_nowait()` — synchronous. The pipeline's `_on_download_progress` is `async`. Emit calls inside async callbacks are fine (just call `self._event_bus.emit(...)` synchronously within the async function). No `await` needed for emit — this is by design. Do not accidentally `await` an emit call.

### 8. Backpressure — unbounded queue
`asyncio.Queue()` (no maxsize) is unbounded. If a slow SSE client causes the queue to grow, memory use increases. For Phase 2 (single-user local tool), this is acceptable. Do not add maxsize — it would cause `put_nowait()` to raise `asyncio.QueueFull` in the pipeline, breaking the run. Document as known limitation.

### 9. Test isolation for `_Stores` with slots
`_Stores` with `slots=True` cannot have attributes added dynamically. If existing tests mock `_Stores` or construct it without the new fields, they will fail with `TypeError`. Audit all `_Stores` construction sites in `test_pipeline.py` and update them to pass the three new counter fields.

---

## RESEARCH COMPLETE
