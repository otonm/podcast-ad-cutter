# Phase 3: Pipeline Control - Context

**Gathered:** 2026-05-16
**Status:** Ready for planning

<domain>
## Phase Boundary

Expose REST endpoints that let a client start, stop, and inspect pipeline runs at full-run, per-feed, and per-episode granularity. The pipeline runs as a background asyncio.Task within the same server process, sharing the EventBus from Phase 2. Episode control endpoints write to the DB.

**Requirements in scope:** CTRL-01, CTRL-02, CTRL-03, CTRL-04, CTRL-05, STAT-01
**Out of scope:** Settings, feed CRUD, DB viewer, log access — Phases 4–6.

</domain>

<decisions>
## Implementation Decisions

### Run Lifecycle (CTRL-01, CTRL-02, CTRL-03)

- **D-01:** `POST /api/v1/run` and `POST /api/v1/feeds/{slug}/run` both return **202 Accepted** on success with body `{"status": "started", "started_at": "<ISO timestamp>"}`. 202 correctly signals "accepted, processing in background."
- **D-02:** **Single run at a time — strict 409 gate.** If any run is active, ALL trigger endpoints (POST /api/v1/run and POST /api/v1/feeds/{slug}/run) return 409. No concurrent or parallel pipeline tasks.
- **D-03:** `Pipeline` already accepts `feed_name: str | None` (filters by feed title). For CTRL-03, the API handler must resolve the `{slug}` URL param to the matching feed title from config before constructing the Pipeline instance.
- **D-04:** The running pipeline task and its metadata (start time, active feed slug, current episode GUID) are tracked in a shared state object stored in `app` dict — not inside Pipeline itself. This lets the status handler read live state without coupling to Pipeline internals.

### Stop Signal (CTRL-02)

- **D-05:** `POST /api/v1/run/stop` — **graceful by default**: set a stop flag that the pipeline checks after each episode completes. Current episode finishes cleanly; DB state is always consistent.
- **D-06:** `POST /api/v1/run/stop?force=true` — **immediate cancel**: calls `asyncio.Task.cancel()`. CancelledError propagates through the current await (LLM call, download, ffmpeg). That stage is incomplete; the pipeline's existing state machine ensures the next run retries from DB checkpoint. No data corruption.
- **D-07:** Both stop variants return 409 if no run is active.

### Status Endpoint (STAT-01)

- **D-08:** `GET /api/v1/status` response shape:
  ```json
  {
    "state": "idle" | "running" | "stopping",
    "started_at": "<ISO timestamp> | null",
    "active_feed_slug": "<slug> | null",
    "current_episode_guid": "<guid> | null",
    "feeds": {
      "<slug>": {
        "episodes_total": 10,
        "episodes_done": 3,
        "episodes_failed": 0
      }
    }
  }
  ```
  `"stopping"` state = graceful stop requested, pipeline still finishing current episode.
- **D-09:** Per-feed episode counts (`episodes_total`, `episodes_done`, `episodes_failed`) come from the shared state object updated by the pipeline as it progresses. `remaining` is derivable client-side (total - done - failed) and is NOT included explicitly.
- **D-10:** `current_episode_guid` is the GUID of the episode actively being processed. Updated in the shared state object at each episode start; cleared when the run ends or the episode completes.

### Episode Control (CTRL-04, CTRL-05)

- **D-11:** `POST /api/v1/episodes/{guid}/reprocess` and `POST /api/v1/episodes/{guid}/skip` **return 409 if any run is active.** No race between API DB writes and pipeline DB writes.
- **D-12:** `POST /api/v1/episodes/{guid}/reprocess` with no stage param = **full reset to 'pending'**: delete all cached data for the episode (transcript, topics, ad detections, audio metadata) and reset episode state to its initial value. The pipeline picks it up on the next run.
- **D-13:** The stage param accepts any of the 5 valid stage names: `download`, `transcribe`, `topic`, `ad-detect`, `edit`. Resetting from stage X deletes all data for X and downstream stages; upstream data is preserved. Invalid stage → 422.
- **D-14:** `POST /api/v1/episodes/{guid}/skip` marks the episode as permanently skipped in the DB. The pipeline's existing state machine guard checks this flag and skips without processing.

### API DB Access

- **D-15:** Episode control handlers (CTRL-04, CTRL-05) open a **dedicated short-lived aiosqlite connection** per request — never shared with the pipeline's connection (CLAUDE.md mandate). WAL mode allows concurrent reads; writes are safe since the 409 gate prevents concurrent pipeline + API DB writes.

### Claude's Discretion

- Exact shared state object shape — a `dataclass` or `TypedDict` stored in `app["run_state"]` is idiomatic; pick whichever is cleaner.
- Route file for control endpoints — `api/routes/control.py` following the Phase 1/2 factory pattern (`create_control_router(...) -> web.RouteTableDef`).
- Whether to use a `threading.Event`-style flag or `asyncio.Event` for the graceful stop signal — async-native preferred.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Requirements & Architecture
- `.planning/ROADMAP.md` — Phase 3 goal, success criteria (STAT-01, CTRL-01–05), dependency chain
- `.planning/REQUIREMENTS.md` — Full CTRL-01–05 and STAT-01 requirement text; traceability to Phase 3
- `.planning/PROJECT.md` — Core constraints: same-process server, no shared aiosqlite connection, async throughout

### Codebase Architecture
- `.planning/codebase/ARCHITECTURE.md` — Full layer diagram; Pipeline as sole Config owner; `_Stores` dataclass; state machine guard structure; anti-patterns
- `.planning/codebase/STACK.md` — aiohttp version; pytest-asyncio auto mode; aioresponses

### Key Existing Files (integration points)
- `components/pipeline.py` — `Pipeline.__init__` (accepts `feed_name`, `event_bus`); `Pipeline.run()` coroutine; `_Stores` dataclass with `episodes_done`, `episodes_failed`, `episodes_total`
- `api/server.py` — `create_app(event_bus, start_time)` factory; `serve()` coroutine; `app` dict is the right place for shared run state
- `api/routes/health.py` — Factory pattern (`create_health_router`) to follow for `create_control_router`
- `api/routes/events.py` — Phase 2 SSE route; same EventBus that the triggered pipeline will emit to
- `api/event_bus.py` — `EventBus`, `PipelineEvent`, `PipelineEventType` — pipeline emits here; SSE route subscribes here
- `database/connection.py` — `Database` async context manager; open a new instance per API request for episode control handlers
- `database/episode_store.py` — Episode state machine; episode skip/reset operations go through here

### CLAUDE.md Constraints (hard rules)
- Never share the aiosqlite connection between pipeline and API handlers
- Never use `web.run_app()` — use `AppRunner` + `TCPSite`
- F-strings only for logging (no `%` operator)
- 100% test coverage required

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `Pipeline(cfg, feed_name=None, event_bus=None)` — already wired for event emission and per-feed filtering; instantiate with the same EventBus used by the SSE route
- `api/event_bus.py:EventBus` — already subscribed to by the SSE route; triggered pipeline emits to it transparently
- `database/connection.py:Database` — async context manager; open per API request for episode control writes
- `database/episode_store.py` — existing DAO; must expose or gain a `reset_episode(guid, from_stage)` and `skip_episode(guid)` method (or the handler calls the right store methods directly)

### Established Patterns
- **`create_X_router(deps) -> web.RouteTableDef`** — factory pattern from health.py and events.py; control.py follows this exactly
- **`app["key"]`** — aiohttp's app dict is the idiomatic store for server-lifetime shared state; use `app["run_state"]` for the active run tracker
- **`asyncio.create_task(pipeline.run())`** — non-blocking task creation; store the returned Task for cancellation and completion detection
- **`asyncio_mode = "auto"`** in pytest config — async tests need no decorator
- **`if self._event_bus is not None: self._event_bus.emit(...)`** — guard pattern from Phase 2; same idiom for any optional emit in Phase 3

### Integration Points
- `api/server.py:create_app()` — add `create_control_router(event_bus, config)` route registration; pass config so control handlers can construct Pipeline
- `api/server.py:serve()` — create `run_state` dict and store in `app` before starting TCPSite
- `components/pipeline.py:_Stores` — already has `episodes_done`, `episodes_failed`, `episodes_total`; pipeline must update `app["run_state"]` as it progresses (or the runner wrapper does this via task callbacks)
- `main.py` — no changes needed for Phase 3; the pipeline is now triggered via API, not CLI, in serve mode

</code_context>

<specifics>
## Specific Ideas

- Stop endpoint uses query param `?force=true` for immediate cancel — keeps one endpoint, force is opt-in.
- Status response includes `"stopping"` as a third state (graceful stop requested, pipeline still running).
- CTRL-04 stage validation returns 422 (Unprocessable Entity) for unrecognized stage names, not 400.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 3-Pipeline Control*
*Context gathered: 2026-05-16*
