# Phase 2: SSE Progress Stream - Context

**Gathered:** 2026-05-15
**Status:** Ready for planning

<domain>
## Phase Boundary

Wire `emit()` calls into the pipeline state machine and expose `GET /api/v1/events` as an SSE endpoint. Connected clients receive live stage-transition events (with start/completed status), download/encode progress percentages, and run-level counters while the pipeline runs. Multiple concurrent clients each receive the full independent event stream.

**Requirements in scope:** EVT-01
**Out of scope:** Pipeline control endpoints (Phase 3), all other REST endpoints — Phase 2 is the event stream only.

</domain>

<decisions>
## Implementation Decisions

### Stage Signal Timing
- **D-01:** `EPISODE_STAGE_CHANGED` fires **twice per stage**: `status: "started"` before the component call, `status: "completed"` after the DB write. Emit via `self._event_bus.emit()` if `self._event_bus is not None` — existing guard pattern from Phase 1.
- **D-02:** Both signals use the **same event type** (`EPISODE_STAGE_CHANGED`) differentiated by a `status` field in the payload. No new enum member needed for this distinction.

### Event Payload Schema (all fields required)
- **D-03:** `EPISODE_STAGE_CHANGED` payload: `{"guid": "...", "stage": "download", "status": "started"|"completed", "feed_slug": "..."}`. Stage name string values: `"download"`, `"preprocess"`, `"transcribe"`, `"topic"`, `"ad-detect"`, `"edit"`.
- **D-04:** `DOWNLOAD_PROGRESS` payload: `{"guid": "...", "feed_slug": "...", "percent": 0.75}`. Replaces/extends the existing `_on_download_progress` stderr behavior — emit this event AND keep the log line.
- **D-05:** `ENCODE_PROGRESS` payload: `{"guid": "...", "feed_slug": "...", "percent": 0.75}`. Same pattern as D-04 for `_on_preprocess_progress`.
- **D-06:** `EPISODE_COMPLETED` payload: `{"guid": "...", "feed_slug": "...", "outcome": "edited"|"copied"|"skipped", "feed_done": 3, "feed_failed": 0, "feed_total": 10}`.
- **D-07:** `EPISODE_FAILED` payload: `{"guid": "...", "feed_slug": "...", "error": "short error message", "feed_done": 3, "feed_failed": 1, "feed_total": 10}`.
- **D-08:** `RUN_STARTED` payload: `{"feeds": ["slug-a", "slug-b"], "total_episodes": 42}`. `total_episodes` is the sum of episodes to process across all feeds.
- **D-09:** `RUN_COMPLETED` payload: `{"feeds": ["slug-a", "slug-b"]}`.

### Run-Level Counter Tracking
- **D-10:** Counters (`done`, `failed`, `total`) are **embedded in EPISODE_COMPLETED and EPISODE_FAILED payloads** — no standalone counter event type; no new enum member needed.
- **D-11:** Pipeline tracks counters using **two new int fields on `_Stores`**: `episodes_done: int = 0` and `episodes_failed: int = 0`. `episodes_total` is determined at feed-start (count of episodes to process) and passed when constructing `_Stores`. Incremented after each episode outcome; read when emitting EPISODE_COMPLETED / EPISODE_FAILED.

### SSE Idle Connect Behavior
- **D-12:** **Silent wait** — no event sent on connect when no pipeline run is active. Phase 3's `GET /api/v1/status` endpoint is the correct way to determine idle vs. running state. SSE stream is events-only, not state.

### SSE Route Structure
- **D-13:** SSE route goes in `api/routes/events.py` with a `create_events_router(event_bus: EventBus) -> web.RouteTableDef` factory — mirrors the Phase 1 `create_health_router` pattern exactly. Registered in `create_app()` in `api/server.py`.
- **D-14:** SSE handler: subscribe on connect, iterate `await queue.get()`, write `event: {type}\ndata: {json}\n\n`, unsubscribe in `finally` block (CLAUDE.md mandate).
- **D-15:** SSE response headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Use `web.StreamResponse` (not `web.Response`).

### Claude's Discretion
- Exact Python field name for `episodes_total` on `_Stores` — pick a name consistent with `episodes_done` / `episodes_failed`.
- Whether `_on_download_progress` and `_on_preprocess_progress` call `emit()` directly or delegate to a shared helper — pick whichever avoids code duplication.
- The `feed_slug` lookup in progress callbacks — `_Stores` already groups by feed; pass `feed_slug` down to the callback signature or capture via closure.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Requirements & Architecture
- `.planning/ROADMAP.md` — Phase 2 goal, success criteria (EVT-01), phase dependency chain
- `.planning/REQUIREMENTS.md` — EVT-01 full text; v2 items EVT-02 (Last-Event-ID replay) and EVT-03 (SSE heartbeat) are explicitly deferred — do NOT implement them in Phase 2
- `.planning/PROJECT.md` — Core constraints: same-process server; EventBus in-process; SSE disconnect handling in `finally`

### Codebase Architecture
- `.planning/codebase/ARCHITECTURE.md` — Full layer diagram; state machine guard order; `_Stores` dataclass structure; anti-pattern: never add stages outside `_process_episode_until_final`
- `.planning/codebase/STACK.md` — aiohttp version; pytest-asyncio auto mode; aioresponses for mocking

### Key Existing Files (integration points)
- `api/event_bus.py` — `EventBus`, `PipelineEvent`, `PipelineEventType` — the full enum defined in Phase 1; Phase 2 extends payload shapes, does NOT add new enum members
- `api/server.py` — `create_app(event_bus, start_time)` factory; must add `create_events_router` import and route registration here
- `api/routes/health.py` — Pattern to follow for `create_events_router` factory in `api/routes/events.py`
- `components/pipeline.py` — `_process_episode_until_final` (where emit calls go); `_Stores` dataclass (where counter fields go); `_on_download_progress` / `_on_preprocess_progress` (where DOWNLOAD_PROGRESS / ENCODE_PROGRESS emit goes); `self._event_bus` is the injected bus

### CLAUDE.md Constraints (hard rules)
- Never share the aiosqlite connection between pipeline and API handlers
- SSE disconnect: always unregister subscriber queue in a `finally` block
- F-strings only for logging (no `%` operator)
- 100% test coverage required — every new file needs tests

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `api/event_bus.py:EventBus` — fully implemented; subscribe/unsubscribe/emit ready; Phase 2 just calls `emit()`
- `api/event_bus.py:PipelineEventType` — enum with all 7 types; no new members needed for Phase 2 decisions
- `components/pipeline.py:_on_download_progress` — existing async callback `(guid, percent)` wired into `EpisodeDownloader.download()`; extend to also emit DOWNLOAD_PROGRESS
- `components/pipeline.py:_on_preprocess_progress` — same pattern; extend to emit ENCODE_PROGRESS
- `aiohttp.web.StreamResponse` — built-in SSE support via `write()` with `\n\n` framing; no extra library needed

### Established Patterns
- **`create_X_router(deps) -> web.RouteTableDef`** — factory pattern from `api/routes/health.py`; `create_events_router(event_bus: EventBus)` follows this exactly
- **`if self._event_bus is not None: self._event_bus.emit(...)`** — standard guard for optional EventBus throughout pipeline; emit calls follow this idiom
- **`asyncio_mode = "auto"`** in pytest config — async tests run without `@pytest.mark.asyncio`
- **`aiohttp.test_utils.TestClient`** — use for SSE route tests; `aioresponses` for any outbound HTTP mocking

### Integration Points
- `api/server.py:create_app()` — add `app.add_routes(create_events_router(event_bus))` alongside the existing health route
- `components/pipeline.py:_process_episode_until_final()` — emit calls inserted at top of each guard action (started) and after DB write (completed); do NOT restructure the state machine
- `components/pipeline.py:_Stores` — add `episodes_done: int` and `episodes_failed: int` (and `episodes_total: int`) fields; dataclass uses `slots=True`
- `main.py:serve()` — currently creates EventBus but does NOT pass it to Pipeline; Phase 3 wires Pipeline; Phase 2 only needs the SSE route to exist and the Pipeline emit calls to be in place

</code_context>

<specifics>
## Specific Ideas

No specific references or examples given during discussion — open to standard aiohttp SSE approaches consistent with the patterns above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 2-SSE Progress Stream*
*Context gathered: 2026-05-15*
