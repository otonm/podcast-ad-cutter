# Phase 3: Pipeline Control - Research

**Researched:** 2026-05-16
**Domain:** aiohttp background task lifecycle, asyncio task control, aiosqlite per-request connections, REST endpoint patterns
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: `POST /api/v1/run` and `POST /api/v1/feeds/{slug}/run` both return **202 Accepted** on success with body `{"status": "started", "started_at": "<ISO timestamp>"}`.
- D-02: Single run at a time — strict 409 gate. If any run is active, ALL trigger endpoints return 409.
- D-03: Slug resolves to feed title from config before constructing Pipeline.
- D-04: Running pipeline task and metadata tracked in `app["run_state"]` — not inside Pipeline itself.
- D-05: `POST /api/v1/run/stop` — graceful by default: set a stop flag that the pipeline checks after each episode completes. Current episode finishes cleanly.
- D-06: `POST /api/v1/run/stop?force=true` — immediate cancel: calls `asyncio.Task.cancel()`.
- D-07: Both stop variants return 409 if no run is active.
- D-08: `GET /api/v1/status` response shape defined (state/started_at/active_feed_slug/current_episode_guid/feeds).
- D-09: Per-feed episode counts come from the shared state object updated by the pipeline as it progresses.
- D-10: `current_episode_guid` updated at each episode start; cleared when run ends or episode completes.
- D-11: Episode control returns 409 if any run is active.
- D-12: Full reset = 'pending' + delete all cached data for the episode.
- D-13: Stage param accepts: download, transcribe, topic, ad-detect, edit; invalid → 422.
- D-14: skip marks permanently skipped in DB.
- D-15: Dedicated short-lived aiosqlite connection per API request for episode control.

### Claude's Discretion
- Exact shared state object shape — a `dataclass` or `TypedDict` stored in `app["run_state"]`; pick whichever is cleaner.
- Route file for control endpoints — `api/routes/control.py` following the Phase 1/2 factory pattern.
- Whether to use `threading.Event`-style flag or `asyncio.Event` for the graceful stop signal — async-native preferred.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STAT-01 | `GET /api/v1/status` returns current state (idle/running/stopping), active feed slug, per-feed episode counts, run start time | RunState dataclass design; `app["run_state"]` access pattern |
| CTRL-01 | `POST /api/v1/run` triggers full pipeline run; 202 on success, 409 if already running | asyncio.create_task, RunState lifecycle, 202/409 response patterns |
| CTRL-02 | `POST /api/v1/run/stop` signals graceful stop; 409 if nothing running; `?force=true` for immediate cancel | asyncio.Event graceful stop, Task.cancel() force stop |
| CTRL-03 | `POST /api/v1/feeds/{slug}/run` triggers pipeline for one specific feed | Slug-to-feed-title resolution, Pipeline(feed_name=...) pattern |
| CTRL-04 | `POST /api/v1/episodes/{guid}/reprocess` resets DB state; optional stage param; 422 for invalid stage | Stage-mapped delete cascade, aiosqlite short-lived connection |
| CTRL-05 | `POST /api/v1/episodes/{guid}/skip` marks episode permanently skipped | New `skipped` column in episodes table via ALTER TABLE |
</phase_requirements>

## Summary

Phase 3 adds six endpoints to the existing aiohttp server: one status query and five control commands. The work divides into three distinct units: (1) a `RunState` dataclass stored in `app["run_state"]` that tracks the active pipeline task, (2) the `api/routes/control.py` router following the established factory pattern, and (3) new methods on `EpisodeStore` for skip/reset plus a schema migration for the `skipped` column.

The pipeline itself needs two additions: a `stop_event: asyncio.Event | None` constructor parameter (for graceful stop), and inline state updates that write current episode GUID and per-feed counters to `app["run_state"]`. The API layer wraps `Pipeline.run()` in a task runner coroutine that manages the RunState lifecycle.

Because `Pipeline` has no existing stop mechanism, the graceful stop is implemented by passing an `asyncio.Event` into the Pipeline and checking `stop_event.is_set()` after each episode loop iteration. Force stop simply calls `Task.cancel()` on the stored task reference.

**Primary recommendation:** Use a `@dataclass` (not TypedDict) for RunState — slots=True gives mypy-clean attribute access and is consistent with `_Stores` in pipeline.py.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Run lifecycle (start/stop/status) | API / Backend | — | Background task management is server-side; no client involvement |
| Run state storage | API / Backend | — | `app["run_state"]` on the aiohttp Application instance |
| Pipeline execution | Orchestration layer (Pipeline) | — | Pipeline.run() is unchanged; task wrapper manages lifecycle |
| Episode state reset | Database layer | API / Backend | EpisodeStore gains new methods; API opens dedicated connection |
| Slug → feed title resolution | API / Backend | Config layer | API reads config to resolve slug; Pipeline receives resolved title |
| Stop signal propagation | API → Pipeline | — | asyncio.Event passed into Pipeline at task creation time |

## Standard Stack

### Core (already installed, no new deps needed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| aiohttp | 3.13.5 [VERIFIED: uv run] | HTTP server, request/response, HTTP exception classes | Already the project's API server |
| aiosqlite | 0.22.1 [VERIFIED: uv run] | Async SQLite for per-request DB connections in episode control handlers | Already the project's DB driver |
| asyncio (stdlib) | Python 3.12 [VERIFIED: uv run] | `asyncio.Event` for graceful stop; `asyncio.create_task` for background pipeline | Standard library |
| dataclasses (stdlib) | Python 3.12 | `RunState` shared state object | Consistent with `_Stores` pattern in pipeline.py |

### No New Dependencies

Phase 3 requires zero new package installs. All required primitives exist in the current stack.

**Version verification:** [VERIFIED: uv run python -c "import aiohttp; print(aiohttp.__version__)"] → 3.13.5

## Package Legitimacy Audit

No new packages are installed in this phase. This section is N/A.

## Architecture Patterns

### System Architecture Diagram

```
POST /api/v1/run
POST /api/v1/feeds/{slug}/run
        │
        ▼
[control.py handler]
   ─ read app["run_state"]
   ─ 409 if state != "idle"
   ─ resolve slug → feed_title (CTRL-03 only)
   ─ create stop_event = asyncio.Event()
   ─ create Pipeline(config, feed_name, event_bus, stop_event)
   ─ task = asyncio.create_task(_run_pipeline_task(pipeline, run_state))
   ─ update run_state: state="running", started_at=now, task=task
   ─ return 202
        │
        ▼
[_run_pipeline_task coroutine] (background)
   ─ update run_state.current_episode_guid per episode
   ─ update run_state.feeds[slug] counters per episode
   ─ try: await pipeline.run()
   ─ finally: reset run_state to idle

GET /api/v1/status
   ─ read app["run_state"]
   ─ return JSON snapshot

POST /api/v1/run/stop
   ─ 409 if state == "idle"
   ─ graceful: stop_event.set(), state="stopping"
   ─ force: task.cancel(), state="idle"

POST /api/v1/episodes/{guid}/reprocess
POST /api/v1/episodes/{guid}/skip
   ─ 409 if state != "idle"
   ─ async with Database(db_path) as db: (dedicated connection, D-15)
   ─ call EpisodeStore / delete from stores
```

### Recommended Project Structure

```
api/
├── routes/
│   ├── health.py       # existing
│   ├── events.py       # existing (Phase 2)
│   └── control.py      # new: all 6 endpoints in this phase
├── run_state.py        # new: RunState dataclass + VALID_STAGES constant
├── server.py           # modified: register control router, init run_state
├── event_bus.py        # existing (unchanged)
components/
└── pipeline.py         # modified: stop_event param + run_state updates
database/
└── episode_store.py    # modified: skip_episode, reset_episode methods
```

### Pattern 1: RunState Dataclass

**What:** Shared mutable state object stored in `app["run_state"]` and passed by reference to all handlers.
**When to use:** Server-lifetime state that multiple handlers read/write concurrently (but writes are gated by the 409 check).

```python
# Source: dataclass pattern from components/pipeline.py _Stores; asyncio.Event verified via uv run
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime

VALID_STAGES: tuple[str, ...] = ("download", "transcribe", "topic", "ad-detect", "edit")

@dataclass
class FeedRunCounts:
    episodes_total: int = 0
    episodes_done: int = 0
    episodes_failed: int = 0

@dataclass
class RunState:
    state: str = "idle"                    # "idle" | "running" | "stopping"
    started_at: datetime | None = None
    active_feed_slug: str | None = None    # set for per-feed runs (CTRL-03)
    current_episode_guid: str | None = None
    task: asyncio.Task | None = None       # the running Pipeline task
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    feeds: dict[str, FeedRunCounts] = field(default_factory=dict)
```

**Important:** `asyncio.Event` requires a running event loop. The `RunState` must be instantiated inside `serve()` (or `create_app()`) after the loop is running — not at module level.

### Pattern 2: Control Router Factory

**What:** `create_control_router(config, event_bus, run_state) -> web.RouteTableDef` — identical factory pattern to `create_health_router` and `create_events_router`.
**When to use:** Every new route group in this server follows this pattern.

```python
# Source: api/routes/health.py and api/routes/events.py patterns [VERIFIED: codebase]
from __future__ import annotations
from aiohttp import web
from api.run_state import RunState

def create_control_router(config, event_bus, run_state: RunState) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/status")
    async def status(_request: web.Request) -> web.Response:
        return web.json_response(_run_state_to_dict(run_state))

    @routes.post("/api/v1/run")
    async def start_run(request: web.Request) -> web.Response:
        if run_state.state != "idle":
            raise web.HTTPConflict(
                text='{"error": "a run is already active"}',
                content_type="application/json",
            )
        # ... start task, update run_state
        return web.json_response({"status": "started", "started_at": ...}, status=202)

    return routes
```

### Pattern 3: Background Task Lifecycle Wrapper

**What:** A coroutine `_run_pipeline_task` that wraps `pipeline.run()` and manages RunState transitions.
**When to use:** Whenever you need to track the start, progress, and completion of an asyncio background task.

```python
# Source: asyncio.create_task pattern [VERIFIED: asyncio stdlib docs]; aiohttp background tasks [CITED: docs.aiohttp.org/en/stable/web_advanced.html#background-tasks]
async def _run_pipeline_task(pipeline: Pipeline, run_state: RunState) -> None:
    try:
        await pipeline.run()
    except asyncio.CancelledError:
        logger.info("Pipeline task cancelled (force stop)")
        raise           # must re-raise CancelledError
    except Exception:
        logger.exception("Pipeline task failed")
    finally:
        # Always reset state, even on exception or cancellation
        run_state.state = "idle"
        run_state.started_at = None
        run_state.active_feed_slug = None
        run_state.current_episode_guid = None
        run_state.task = None
        run_state.stop_event.clear()
        run_state.feeds.clear()
```

**Critical:** `CancelledError` must be re-raised after logging. Swallowing it breaks asyncio task cancellation semantics. [VERIFIED: asyncio stdlib behavior via uv run]

### Pattern 4: Graceful Stop via asyncio.Event

**What:** Pipeline checks `stop_event.is_set()` after each episode loop iteration and breaks cleanly.
**When to use:** When you want the current unit of work (episode) to complete before halting.

```python
# Source: asyncio.Event [VERIFIED: uv run python -c "import asyncio; e=asyncio.Event(); e.set(); print(e.is_set())"]
# Addition to Pipeline.__init__:
def __init__(self, config, feed_name=None, *, event_bus=None, stop_event=None):
    ...
    self._stop_event: asyncio.Event | None = stop_event

# Addition to Pipeline.run() — after the `for episode in episodes:` block completes one episode:
for episode in episodes:
    ...  # existing episode processing
    if self._stop_event is not None and self._stop_event.is_set():
        logger.info("Graceful stop requested — halting after current episode")
        break
```

**Placement:** The `if stop_event.is_set()` check goes inside the outer `for episode in episodes` loop, at the end of each iteration (after the episode's try/finally). This guarantees the DB is always consistent.

### Pattern 5: Slug → Feed Title Resolution

**What:** Given a URL slug like `my-show`, find the matching `FeedConfig.title` in config.
**When to use:** CTRL-03 `/feeds/{slug}/run` handler.

```python
# Source: pipeline.py uses slugify(feed.title) for output paths [VERIFIED: codebase]
# slugify is already imported in pipeline.py; config is passed to control router
from slugify import slugify

def _resolve_slug(slug: str, feeds: list[FeedConfig]) -> str | None:
    """Return feed title matching slug, or None if not found."""
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None
```

**Note:** This uses the same `python-slugify` library already installed and used in `pipeline.py`. No new import or dep.

### Pattern 6: Per-Request Database Connection (Episode Control)

**What:** Open a fresh `Database` context manager per handler call for episode control writes.
**When to use:** CTRL-04 and CTRL-05 handlers (D-15 mandate from CLAUDE.md).

```python
# Source: database/connection.py [VERIFIED: codebase]; CLAUDE.md constraint
from database.connection import Database

@routes.post("/api/v1/episodes/{guid}/reprocess")
async def reprocess(request: web.Request) -> web.Response:
    if run_state.state != "idle":
        raise web.HTTPConflict(...)
    guid = request.match_info["guid"]
    stage = request.rel_url.query.get("stage")
    if stage is not None and stage not in VALID_STAGES:
        raise web.HTTPUnprocessableEntity(
            text=f'{{"error": "invalid stage: {stage}"}}',
            content_type="application/json",
        )
    db_path = config.app.paths.data_dir / "data.db"
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.reset_episode(guid, from_stage=stage)
    return web.json_response({"status": "reset", "guid": guid})
```

### Pattern 7: HTTP Status Codes (all verified)

| Status | aiohttp Class | Use case |
|--------|--------------|----------|
| 202 | `web.json_response({...}, status=202)` | Run started |
| 409 | `raise web.HTTPConflict(text=json_str, content_type="application/json")` | Already running / nothing running |
| 422 | `raise web.HTTPUnprocessableEntity(text=json_str, content_type="application/json")` | Invalid stage param |
| 200 | `web.json_response({...})` | Status, skip, reset success |

[VERIFIED: uv run python -c "from aiohttp import web; print(web.HTTPConflict.status_code, web.HTTPAccepted.status_code, web.HTTPUnprocessableEntity.status_code)"]

### Anti-Patterns to Avoid

- **Swallowing CancelledError:** After `task.cancel()`, the wrapper coroutine receives `asyncio.CancelledError`. It must re-raise after logging — swallowing it prevents the task from registering as cancelled.
- **Shared DB connection between pipeline and API:** CLAUDE.md mandates dedicated connections per request. The pipeline already opens its own via `async with Database(...)` inside `Pipeline.run()` — episode control handlers must open a separate independent instance.
- **Checking `run_state.task.done()` for state:** Don't use `task.done()` as the authoritative state indicator — the `finally` block in the wrapper is the right place to flip state back to `idle`. Using `task.done()` requires handling the race between task completion and status queries.
- **Creating RunState at module level:** `asyncio.Event()` requires a running event loop. Instantiate RunState inside `serve()` (before the `await runner.setup()` call but inside an `async def`), or inside `create_app()` called from within an async context.
- **Calling `web.run_app()`:** CLAUDE.md mandate — always use `AppRunner + TCPSite`. Already followed in `server.py`.
- **Force stop without re-raising CancelledError:** If the pipeline is mid-LLM call or mid-ffmpeg subprocess, `task.cancel()` injects `CancelledError` at the next `await`. The pipeline's existing `except Exception` catches `BaseException` subtypes correctly — `CancelledError` is a `BaseException` (Python 3.8+) so it propagates past the episode-level `except Exception` block correctly.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP 202/409/422 responses | Custom response builder with status codes | `web.json_response(status=202)` and `raise web.HTTPConflict(...)` | aiohttp has all needed status classes verified present [VERIFIED] |
| Stop signaling between coroutines | Threading.Event or shared boolean | `asyncio.Event` | Native asyncio, no threading concerns, `.set()` / `.is_set()` / `.clear()` API [VERIFIED] |
| Background task management | Custom executor or thread | `asyncio.create_task()` | Same event loop, no IPC, cancellable [VERIFIED] |
| Slug generation | Custom URL-safe string builder | `python-slugify` (already installed) | Same library used in `pipeline.py` for consistency |
| Schema migration for `skipped` column | New migration framework | `ALTER TABLE episodes ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0` with `contextlib.suppress(OperationalError)` | Same idempotent pattern already used in `database/connection.py` for `length` and `source_url` columns |

## Common Pitfalls

### Pitfall 1: asyncio.Event Created Outside Async Context

**What goes wrong:** `RunState()` instantiated at module level or in `create_app()` called synchronously — `asyncio.Event()` on Python 3.10+ works without a running loop, but in Python 3.12 strict mode, creating an Event in a non-async context and then using it in a different loop can cause subtle bugs.
**Why it happens:** `create_app()` is a sync function called from test setup and from `serve()`.
**How to avoid:** Instantiate `RunState` inside `serve()` (which is `async def`), then pass it to `create_app()`. Tests that call `create_app()` directly must construct RunState inside an `async` fixture.
**Warning signs:** `DeprecationWarning: There is no current event loop` during test setup.

### Pitfall 2: CancelledError Swallowed in Wrapper

**What goes wrong:** The `_run_pipeline_task` wrapper catches `asyncio.CancelledError` and doesn't re-raise it. The task appears done but `task.cancelled()` returns False, and the finally block may run in unexpected order.
**Why it happens:** Generic `except Exception` catches `CancelledError` in Python < 3.8; in 3.12 it's `BaseException` and won't be caught by bare `except Exception` — but explicit `except asyncio.CancelledError` must still re-raise.
**How to avoid:** Always `raise` after logging in `except asyncio.CancelledError` branch.
**Warning signs:** `task.cancelled()` returns False after `task.cancel()`.

### Pitfall 3: Force Stop Leaves RunState as "running"

**What goes wrong:** `task.cancel()` is called but RunState is not reset — subsequent status checks return `"running"` indefinitely.
**Why it happens:** Forgetting that `task.cancel()` is async and the task won't reset RunState until it resumes and the CancelledError propagates to the `finally` block.
**How to avoid:** After `task.cancel()`, the `finally` block in `_run_pipeline_task` resets the state. Do NOT manually reset RunState in the stop handler — let the finally block do it. Set `run_state.state = "stopping"` in the force-stop handler (optional) if you want immediate status feedback.
**Warning signs:** Status endpoint returns `"running"` after force stop.

### Pitfall 4: Slug Resolution Returns None → 404 vs 400

**What goes wrong:** `/feeds/nonexistent-slug/run` handler finds no matching feed and returns the wrong error code.
**Why it happens:** Both "not found" and "bad slug" are plausible error responses.
**How to avoid:** Return 404 (HTTPNotFound) when the slug doesn't match any feed — this is semantically a resource-not-found error, matching REST conventions.
**Warning signs:** Client receives 500 instead of 404.

### Pitfall 5: Episode GUID Not Found in DB

**What goes wrong:** `/episodes/{guid}/reprocess` or `/episodes/{guid}/skip` receives a GUID that doesn't exist in the episodes table — DELETE/UPDATE executes without error but affects 0 rows.
**Why it happens:** SQLite silently succeeds on UPDATE/DELETE with no matching rows.
**How to avoid:** After the UPDATE/DELETE, check `cursor.rowcount` (or execute a SELECT first). Return 404 if the episode is not found.
**Warning signs:** Handler always returns 200 even for bogus GUIDs.

### Pitfall 6: Stage Reset Cascade Order

**What goes wrong:** Resetting from stage `transcribe` deletes transcript but not topic/ad-detect data, leaving orphaned downstream records.
**Why it happens:** Each stage has its own table; deleting upstream without deleting downstream violates the pipeline's state machine logic (Guard 3 fires on leftover topic data, skipping transcription).
**How to avoid:** Stage cascade order (delete from all downstream tables): `download` → delete audio_metadata + transcript + topic + ad_detection. `transcribe` → delete transcript + topic + ad_detection. `topic` → delete topic + ad_detection. `ad-detect` → delete ad_detection. `edit` → no DB data to delete (it's on disk only — output file removal is out of scope).
**Warning signs:** Next pipeline run skips re-transcription despite `reprocess` being called.

### Pitfall 7: DB WAL Mode Not Enabled for Per-Request Connections

**What goes wrong:** Episode control writes block the pipeline's concurrent DB reads, or vice versa.
**Why it happens:** Default SQLite journal mode is DELETE (exclusive lock on write). WAL mode allows one writer + multiple readers.
**How to avoid:** The existing `Database` context manager doesn't set WAL mode explicitly — SQLite WAL is enabled project-wide or relies on the default. Check whether WAL needs to be set in `Database.__aenter__`. However, since D-11 requires the 409 gate (no concurrent pipeline + API writes), this is a belt-and-suspenders concern — the 409 gate already prevents simultaneous writes.

## Code Examples

### RunState Initialization in serve()

```python
# Source: asyncio.Event API [VERIFIED: stdlib]; app dict pattern [CITED: docs.aiohttp.org/en/stable/web_advanced.html]
async def serve(host: str, port: int) -> None:
    start_time = time.monotonic()
    event_bus = EventBus()
    run_state = RunState()              # must be inside async def
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

### Task Creation and Status Reset

```python
# Source: asyncio.create_task [VERIFIED: stdlib]; CancelledError re-raise pattern [VERIFIED: stdlib]
async def _run_pipeline_task(
    pipeline: Pipeline,
    run_state: RunState,
) -> None:
    try:
        await pipeline.run()
    except asyncio.CancelledError:
        logger.info("Pipeline task cancelled (force stop requested)")
        raise
    except Exception:
        logger.exception("Pipeline run failed")
    finally:
        run_state.state = "idle"
        run_state.started_at = None
        run_state.active_feed_slug = None
        run_state.current_episode_guid = None
        run_state.task = None
        run_state.stop_event.clear()
        run_state.feeds.clear()
```

### Episode Reset: Stage Cascade

```python
# Source: database schema [VERIFIED: database/connection.py]; cascade logic derived from pipeline guards [VERIFIED: pipeline.py]
STAGE_CASCADE: dict[str, list[str]] = {
    "download":   ["episode_audio_metadata", "transcriptions", "transcription_segments", "topic_extractions", "ad_segments", "ad_detection_runs"],
    "transcribe": ["transcriptions", "transcription_segments", "topic_extractions", "ad_segments", "ad_detection_runs"],
    "topic":      ["topic_extractions", "ad_segments", "ad_detection_runs"],
    "ad-detect":  ["ad_segments", "ad_detection_runs"],
    "edit":       [],   # edit stage is disk-only; reset has no DB effect
}

async def reset_episode(self, guid: str, *, from_stage: str | None = None) -> bool:
    """Reset episode for reprocessing. Returns False if GUID not found."""
    tables = STAGE_CASCADE.get(from_stage, list(STAGE_CASCADE["download"])) if from_stage else list(STAGE_CASCADE["download"])
    for table in tables:
        await self._conn.execute(f"DELETE FROM {table} WHERE guid = ?", (guid,))  # noqa: S608
    # Also reset URL to source_url for full reset (download stage)
    if from_stage in (None, "download"):
        result = await self._conn.execute(
            "UPDATE episodes SET url = source_url WHERE guid = ?", (guid,)
        )
    else:
        result = await self._conn.execute(
            "SELECT id FROM episodes WHERE guid = ?", (guid,)
        )
    await self._conn.commit()
    return result.rowcount > 0  # type: ignore[return-value]
```

### Episode Skip: Schema Migration

```python
# Source: idempotent ALTER TABLE pattern [VERIFIED: database/connection.py lines 160-167]
# In Database.__aenter__, alongside existing column migrations:
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0"
    )
```

```python
# EpisodeStore.skip_episode:
async def skip_episode(self, guid: str) -> bool:
    """Mark episode as permanently skipped. Returns False if GUID not found."""
    result = await self._conn.execute(
        "UPDATE episodes SET skipped = 1 WHERE guid = ?", (guid,)
    )
    await self._conn.commit()
    return result.rowcount > 0
```

### Pipeline Graceful Stop Integration

```python
# Source: pipeline.py for-loop structure [VERIFIED: codebase]; asyncio.Event [VERIFIED: uv run]
# In Pipeline.__init__, add:
self._stop_event: asyncio.Event | None = stop_event

# In Pipeline.run(), inside `for episode in episodes:` loop, after the episode's try/finally:
for episode in episodes:
    ...  # existing try/finally block (log open → _process_episode_until_final → log close)
    if self._stop_event is not None and self._stop_event.is_set():
        logger.info(f"Graceful stop requested — halting after episode '{episode.guid}'")
        break
```

### Pipeline State Updates for Status Endpoint

The pipeline needs to write to `run_state` as it progresses. This can be done two ways:

**Option A — Pass run_state into Pipeline:** Cleanest but adds API coupling to pipeline.
**Option B — Wrapper task reads pipeline events from EventBus:** More decoupled but requires event subscription logic.
**Option C — Add callback params to Pipeline:** `on_episode_start(guid)`, `on_episode_done(feed_slug, done, failed, total)`.

**Recommendation (Claude's Discretion):** Option A (pass RunState into Pipeline). The pipeline already accepts `event_bus` (an API concept) so this coupling precedent exists. The alternative (EventBus subscription in wrapper) requires parsing EventBus events — duplicating logic.

```python
# In Pipeline.__init__:
self._run_state: RunState | None = run_state

# In Pipeline.run(), inside the episode loop, before _process_episode_until_final:
if self._run_state is not None:
    self._run_state.current_episode_guid = episode.guid

# After episode completes (existing stores.episodes_done += 1 block):
if self._run_state is not None:
    self._run_state.current_episode_guid = None
    self._run_state.feeds[feed_slug] = FeedRunCounts(
        episodes_total=stores.episodes_total,
        episodes_done=stores.episodes_done,
        episodes_failed=stores.episodes_failed,
    )
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `asyncio.get_event_loop().run_until_complete()` | `asyncio.run()` + `create_task()` | Python 3.10+ | Creates and manages its own loop; tasks created within it are properly scoped |
| `threading.Event` for cross-coroutine signaling | `asyncio.Event` | Python 3.4+ | No thread overhead; `await event.wait()` yields cooperatively |
| aiohttp `web.run_app()` | `AppRunner + TCPSite` | aiohttp 3.x | Non-blocking; allows sharing the event loop with other tasks |

**Deprecated/outdated:**
- `asyncio.coroutine` decorator: removed in Python 3.11; use `async def` (already the project standard)
- `loop` parameter to `asyncio.Event()`: removed in Python 3.10; Event uses the running loop automatically

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The `skipped` column does not yet exist in the episodes table | Schema migration | If it already exists, the `ALTER TABLE` with `contextlib.suppress` is harmless — no risk |
| A2 | Resetting the `edit` stage requires no DB changes (output file deletion is out of scope) | Stage cascade table | If edit stage stores something in DB, the reset won't clean it — investigate before implementing |
| A3 | Pipeline.run() with an active `stop_event` will check it between episodes (not mid-episode) | Graceful stop pattern | If the loop structure changes, the check placement may need updating — low risk given we control pipeline.py |

## Open Questions

1. **RunState coupling to Pipeline: Option A vs C**
   - What we know: Pipeline already takes `event_bus` (an API-layer object), establishing precedent for API coupling.
   - What's unclear: Whether passing `RunState` directly into Pipeline violates the "Pipeline is the sole orchestrator" principle more than `EventBus` does.
   - Recommendation: Use Option A (pass RunState) — it's the simplest and most readable. Claude's Discretion applies here.

2. **episode_store.reset_episode SQL injection risk**
   - What we know: Table names can't be parameterized in SQLite — must interpolate.
   - What's unclear: Whether ruff's S608 rule (possible SQL injection) will flag this.
   - Recommendation: Use a closed `STAGE_CASCADE` dict as the whitelist — table names only come from that dict, never from user input. Add a `# noqa: S608` comment.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | All | ✓ | 3.12.x | — |
| aiohttp | API server | ✓ | 3.13.5 | — |
| aiosqlite | Episode control DB | ✓ | 0.22.1 | — |
| python-slugify | Slug resolution | ✓ | installed (used in pipeline.py) | — |
| asyncio | Stop signal, task management | ✓ | stdlib | — |

**Missing dependencies with no fallback:** None
**Missing dependencies with fallback:** None

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 0.24 |
| Config file | pyproject.toml (`asyncio_mode = "auto"`) |
| Quick run command | `uv run pytest tests/test_api_control.py -x` |
| Full suite command | `uv run pytest --cov=.` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STAT-01 | GET /status returns idle state on fresh app | unit | `uv run pytest tests/test_api_control.py::TestStatus -x` | ❌ Wave 0 |
| STAT-01 | GET /status returns running state when task active | unit | `uv run pytest tests/test_api_control.py::TestStatus::test_status_running -x` | ❌ Wave 0 |
| STAT-01 | GET /status returns stopping state after graceful stop | unit | `uv run pytest tests/test_api_control.py::TestStatus::test_status_stopping -x` | ❌ Wave 0 |
| CTRL-01 | POST /run returns 202 when idle | unit | `uv run pytest tests/test_api_control.py::TestStartRun -x` | ❌ Wave 0 |
| CTRL-01 | POST /run returns 409 when running | unit | `uv run pytest tests/test_api_control.py::TestStartRun::test_run_409_when_active -x` | ❌ Wave 0 |
| CTRL-02 | POST /run/stop graceful sets stop_event | unit | `uv run pytest tests/test_api_control.py::TestStopRun -x` | ❌ Wave 0 |
| CTRL-02 | POST /run/stop?force=true cancels task | unit | `uv run pytest tests/test_api_control.py::TestStopRun::test_force_stop -x` | ❌ Wave 0 |
| CTRL-02 | POST /run/stop returns 409 when idle | unit | `uv run pytest tests/test_api_control.py::TestStopRun::test_stop_409_when_idle -x` | ❌ Wave 0 |
| CTRL-03 | POST /feeds/{slug}/run resolves slug to feed title | unit | `uv run pytest tests/test_api_control.py::TestFeedRun -x` | ❌ Wave 0 |
| CTRL-03 | POST /feeds/unknown/run returns 404 | unit | `uv run pytest tests/test_api_control.py::TestFeedRun::test_slug_not_found -x` | ❌ Wave 0 |
| CTRL-04 | POST /episodes/{guid}/reprocess full reset deletes all cached data | unit | `uv run pytest tests/test_api_control.py::TestReprocess -x` | ❌ Wave 0 |
| CTRL-04 | POST /episodes/{guid}/reprocess?stage=transcribe cascades correctly | unit | `uv run pytest tests/test_api_control.py::TestReprocess::test_stage_cascade -x` | ❌ Wave 0 |
| CTRL-04 | POST /episodes/{guid}/reprocess?stage=invalid returns 422 | unit | `uv run pytest tests/test_api_control.py::TestReprocess::test_invalid_stage -x` | ❌ Wave 0 |
| CTRL-04 | POST /episodes/{guid}/reprocess returns 409 when run active | unit | `uv run pytest tests/test_api_control.py::TestReprocess::test_409_when_active -x` | ❌ Wave 0 |
| CTRL-04 | POST /episodes/unknown-guid/reprocess returns 404 | unit | `uv run pytest tests/test_api_control.py::TestReprocess::test_guid_not_found -x` | ❌ Wave 0 |
| CTRL-05 | POST /episodes/{guid}/skip marks skipped in DB | unit | `uv run pytest tests/test_api_control.py::TestSkipEpisode -x` | ❌ Wave 0 |
| CTRL-05 | POST /episodes/{guid}/skip returns 409 when run active | unit | `uv run pytest tests/test_api_control.py::TestSkipEpisode::test_409_when_active -x` | ❌ Wave 0 |
| CTRL-05 | Pipeline skips episodes with skipped=1 | unit | `uv run pytest tests/test_pipeline_stop.py -x` | ❌ Wave 0 |
| CTRL-01+D-04 | RunState resets to idle after task completes | unit | `uv run pytest tests/test_api_control.py::TestRunStateLifecycle -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_api_control.py tests/test_episode_store.py -x`
- **Per wave merge:** `uv run pytest --cov=.`
- **Phase gate:** Full suite green at 100% coverage before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_api_control.py` — all STAT-01 + CTRL-01 through CTRL-05 tests
- [ ] `tests/test_pipeline_stop.py` — graceful stop, force stop, per-episode state updates
- [ ] `api/run_state.py` — new module (RunState dataclass, FeedRunCounts, VALID_STAGES)
- [ ] `api/routes/control.py` — new module

## Security Domain

> security_enforcement: not set in config → treated as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth in v1 (SEC-01 deferred to v2 per REQUIREMENTS.md) |
| V3 Session Management | no | Stateless REST; no sessions |
| V4 Access Control | no | Local network only; v1 has no auth |
| V5 Input Validation | yes | Stage param validated against closed VALID_STAGES tuple; GUID from URL path (no free-form SQL) |
| V6 Cryptography | no | No crypto in this phase |

### Known Threat Patterns for this Stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via table name interpolation | Tampering | STAGE_CASCADE whitelist dict — table name never comes from user input; `# noqa: S608` with comment |
| Task leak on server restart | Denial of Service | `finally` block in wrapper always resets RunState; AppRunner cleanup cancels all tasks |
| Concurrent pipeline trigger race | Tampering | 409 gate is synchronous check on RunState.state before task creation — single-threaded asyncio prevents TOCTOU |

## Sources

### Primary (HIGH confidence)
- [VERIFIED: uv run] aiohttp 3.13.5 — HTTP exception classes (HTTPConflict, HTTPAccepted, HTTPUnprocessableEntity), `web.json_response(status=N)`
- [VERIFIED: uv run] aiosqlite 0.22.1 — per-request connection pattern
- [VERIFIED: uv run] asyncio stdlib — `asyncio.Event` API (set/is_set/clear/wait), `asyncio.create_task`, `Task.cancel()`, `Task.cancelled()`
- [VERIFIED: codebase] `database/connection.py` — idempotent ALTER TABLE pattern with `contextlib.suppress`
- [VERIFIED: codebase] `components/pipeline.py` — `_Stores` dataclass, episode for-loop structure, stop_event injection point
- [VERIFIED: codebase] `api/routes/health.py`, `api/routes/events.py` — factory pattern to replicate
- [VERIFIED: codebase] `api/server.py` — `create_app`, `serve`, `AppRunner + TCPSite` pattern

### Secondary (MEDIUM confidence)
- [CITED: docs.aiohttp.org/en/stable/web_advanced.html#background-tasks] aiohttp background tasks — `asyncio.create_task` pattern; cleanup_ctx; warning about awaiting tasks
- [CITED: docs.aiohttp.org/en/stable/web_advanced.html#application-s-config] aiohttp app dict pattern — `app["key"]` for shared state

### Tertiary (LOW confidence)
None — all claims verified through code inspection or stdlib.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries verified via `uv run`, no new packages needed
- Architecture: HIGH — patterns derived directly from existing codebase (health.py, events.py, pipeline.py)
- Pitfalls: HIGH — all pitfalls traced to concrete code paths in the project
- Test map: HIGH — all test classes and methods derived from requirements and code structure

**Research date:** 2026-05-16
**Valid until:** 2026-06-16 (stable stack; no fast-moving dependencies)
