# Phase 2: SSE Progress Stream - Patterns

**Generated:** 2026-05-16
**Phase:** 02-sse-progress-stream

---

## 1. `api/routes/events.py` — NEW: SSE route

**Role:** New file. Exposes `GET /api/v1/events` as a Server-Sent Events stream. Subscribes to `EventBus` on connect, writes events as they arrive, unsubscribes in `finally`.

### Closest analog: `api/routes/health.py`

```python
def create_health_router(start_time: float) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/health")
    async def health(_request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "uptime_seconds": round(time.monotonic() - start_time, 2),
            "version": _read_version(),
        })

    return routes
```

### Pattern to replicate/extend

Follow the `create_X_router(deps) -> web.RouteTableDef` factory exactly. Replace `web.Response` with `web.StreamResponse` (streaming, never buffered). Call `await resp.prepare(request)` before any `write()`. Use `event_bus.subscribe()` on connect and `event_bus.unsubscribe(q)` in a `finally` block (CLAUDE.md mandate). The SSE wire format is `event: {type}\ndata: {json}\n\n` (double newline terminates each event). `await resp.write(data.encode())` takes bytes not str. The `event_bus` parameter is captured in the factory closure — same idiom as `start_time` in the health router.

**Key differences from health router:**
- Return type is `web.StreamResponse` not `web.Response`
- `resp.headers` set before `await resp.prepare(request)` for `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`
- Infinite loop on `await q.get()` — client disconnect raises `asyncio.CancelledError` or `ConnectionResetError`, both caught by `finally`
- Do NOT return inside the try block — the loop runs until cancelled

---

## 2. `api/server.py` — MODIFY: register events router

**Role:** Existing file. Add one import and one `app.add_routes()` call to `create_app()` to register the new SSE route alongside the health route.

### Closest analog: existing `create_health_router` registration

```python
from api.routes.health import create_health_router

def create_app(event_bus: EventBus, start_time: float) -> web.Application:
    app = web.Application()
    app["event_bus"] = event_bus
    app.add_routes(create_health_router(start_time))
    return app
```

### Pattern to replicate/extend

Add `from api.routes.events import create_events_router` to the imports block. Add `app.add_routes(create_events_router(event_bus))` after the health route registration. The `event_bus` is already available as a parameter — pass it directly to the factory. No other changes to this file.

---

## 3. `components/pipeline.py` — MODIFY: `_Stores` counter fields

**Role:** Add three integer counter fields to the `_Stores` dataclass so EPISODE_COMPLETED and EPISODE_FAILED payloads can embed per-feed counters.

### Closest analog: existing `_Stores` dataclass

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

### Pattern to replicate/extend

Add `episodes_done: int`, `episodes_failed: int`, and `episodes_total: int` fields to the dataclass body. With `slots=True` there is no `__dict__` — all fields must be declared in the dataclass body; no dynamic attribute assignment is allowed. Give `episodes_done` and `episodes_failed` defaults of `0`. `episodes_total` has no default and must be passed explicitly at construction. The `_Stores` construction in `Pipeline.run()` uses keyword arguments — add `episodes_total=len(episodes)` at the call site.

---

## 4. `components/pipeline.py` — MODIFY: emit calls

**Role:** Wire `self._event_bus.emit(...)` calls into the run loop and state machine for all seven event types. Add closures in place of direct progress callback references where `feed_slug` must be captured.

### Existing emit guard pattern (from `Pipeline.__init__`)

```python
self._event_bus: EventBus | None = event_bus
```

The guard throughout is:
```python
if self._event_bus is not None:
    self._event_bus.emit(PipelineEvent(
        type=PipelineEventType.EPISODE_STAGE_CHANGED,
        payload={"guid": episode.guid, "stage": "download", "status": "started", "feed_slug": feed_slug},
    ))
```

### Existing progress callbacks (analog for closure pattern)

```python
async def _on_download_progress(self, guid: str, percent: float) -> None:
    if percent == 0.0:
        logger.debug(f"Downloading episode '{guid}' …")
    elif percent == 1.0:
        if sys.stderr.isatty():
            sys.stderr.write("\n")
            sys.stderr.flush()
        logger.debug(f"Episode '{guid}' downloaded.")
    elif sys.stderr.isatty():
        sys.stderr.write(f"\r  Episode '{guid}': {percent:.0%}")
        sys.stderr.flush()
```

Both `_on_download_progress` and `_on_preprocess_progress` only receive `(guid, percent)` — `feed_slug` is not in the signature. Use a closure defined at the call site in `_process_episode_until_final` to capture `feed_slug` from the enclosing scope:

```python
async def _on_dl_progress(guid: str, percent: float) -> None:
    await self._on_download_progress(guid, percent)  # existing log behavior preserved
    if self._event_bus is not None:
        self._event_bus.emit(PipelineEvent(
            type=PipelineEventType.DOWNLOAD_PROGRESS,
            payload={"guid": guid, "feed_slug": feed_slug, "percent": percent},
        ))
raw_path = await self._episode_downloader.download(episode.guid, url, on_progress=_on_dl_progress)
```

**Guard 2 re-download site also needs the closure** — there are two `self._episode_downloader.download(...)` call sites in `_process_episode_until_final`: the bottom (new episode) and Guard 2 (re-download for cached detection). Both must use the closure, not `self._on_download_progress`.

**RUN_STARTED / RUN_COMPLETED** are emitted in `Pipeline.run()` around the outer feed loop. `RUN_STARTED` requires `total_episodes` which is the sum of `len(episodes)` across all feeds — this means either a two-pass approach (count all episode lists before the loop body) or accumulating. Emit `RUN_STARTED` before processing begins with the full count, emit `RUN_COMPLETED` after all feeds finish.

**EPISODE_COMPLETED / EPISODE_FAILED** are emitted in the `try/except` block wrapping `_process_episode_until_final` in `Pipeline.run()`. On success: `stores.episodes_done += 1` then emit. On exception: `stores.episodes_failed += 1` then emit.

**EPISODE_STAGE_CHANGED** is emitted twice per guard action inside `_process_episode_until_final`: `status: "started"` before the component call, `status: "completed"` after the DB write. Guards 1 and 2 that terminate with `return` without entering a new stage do NOT emit stage events.

`emit()` is synchronous (`put_nowait`) — never `await` it.

---

## 5. `tests/test_api_events.py` — NEW: SSE route tests

**Role:** New test file. Tests the SSE route for status code, headers, event delivery, multiple subscribers, and disconnect cleanup.

### Closest analog: `tests/test_api_health.py`

```python
from aiohttp.test_utils import TestClient, TestServer
from api.event_bus import EventBus
from api.server import create_app

class TestHealthEndpoint:
    async def test_health_returns_200(self) -> None:
        app = create_app(EventBus(), time.monotonic())
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/api/v1/health")
            assert resp.status == 200
```

### Pattern to replicate/extend

Use `TestClient(TestServer(app))` as the async context manager. For SSE tests use `async with client.get("/api/v1/events") as resp:` — the response is a streaming context. Read chunks via `resp.content.read(N)` after emitting a known event. Tests must always emit at least one event before asserting content — `await q.get()` blocks forever if no event arrives and the test will hang.

**Disconnect/cleanup test:** exit the `async with client.get(...)` block then assert `len(bus._subscribers) == 0` — the `finally` block in the handler fires when the connection closes.

**Hanging test mitigation:** where reading is needed but a clean disconnect is the goal, exit the context manager without reading — aiohttp will cancel the handler task, firing `finally`.

**Coverage requirements:** must cover the `finally` branch (disconnect path), the `event_bus is not None` guard branches (both with `None` and with a real bus), and the multiple-subscriber path.

---

## 6. `tests/test_pipeline.py` — MODIFY: emit tests and `_Stores` construction

**Role:** Extend existing tests to pass the three new `_Stores` counter fields and add new tests for emit behavior.

### Existing `_Stores` construction site in `Pipeline.run()`

```python
stores = _Stores(
    episode=store,
    transcription=t_store,
    audio_metadata=AudioMetadataStore(db.conn),
    cost=CostTrackingStore(db.conn),
    topic=topic_store,
    ad=ad_store,
    transcribed_guids=await t_store.get_transcribed_guids(),
    extracted_guids=await topic_store.get_extracted_guids(),
    ad_detected_guids=ad_detected_guids,
)
```

All three new fields must be added here. Existing tests that call `Pipeline.run()` will get `TypeError` if the construction call is not updated — audit ALL `pipeline.run()` call sites in `test_pipeline.py`.

### Existing Pipeline construction pattern (for emit tests)

```python
pipeline = Pipeline(config)
await pipeline.run()
```

For emit tests, inject a real or mock `EventBus`:

```python
from unittest.mock import MagicMock
from api.event_bus import EventBus, PipelineEventType

mock_bus = MagicMock(spec=EventBus)
pipeline = Pipeline(config, event_bus=mock_bus)
await pipeline.run()
call_types = [c.args[0].type for c in mock_bus.emit.call_args_list]
assert PipelineEventType.RUN_STARTED in call_types
```

For progress callback emit tests, call `_on_download_progress` or `_on_preprocess_progress` directly — same pattern as existing `test_on_download_progress_*` tests. Inject a mock bus and assert `emit` was called with correct type and payload fields.

**Existing `_wire_branch_mocks` helper** wires all component mocks and is reused across branch tests. New emit tests can reuse this helper and add `event_bus=mock_bus` to the `Pipeline(config, event_bus=mock_bus)` call.

**`event_bus=None` coverage:** existing pipeline tests (no `event_bus` argument) already exercise the `if self._event_bus is not None:` guard's false branch — no change needed for those. New tests with `event_bus=mock_bus` cover the true branch.

---

## Cross-cutting notes

- `asyncio_mode = "auto"` in pytest config — async tests run without `@pytest.mark.asyncio` decorator.
- `from __future__ import annotations` header required on all new files (matches project style).
- `logger = logging.getLogger(__name__)` in every new module.
- F-strings only for log messages — no `%` operator (CLAUDE.md).
- `emit()` is synchronous — never `await` it inside async callbacks.
- `slots=True` on `_Stores` means no dynamic attribute assignment; all new fields must be in the dataclass body.
- The `api/routes/` directory already exists (`api/routes/health.py`) — `events.py` goes there directly.
