# Phase 1: API Foundation - Context

**Gathered:** 2026-05-14
**Status:** Ready for planning

<domain>
## Phase Boundary

Deliver the server infrastructure skeleton: `--serve` flag starts an aiohttp.web server (AppRunner + TCPSite) that responds to health checks; `EventBus` class exists with a full event type enum; `Pipeline` accepts an optional `EventBus`; dual-mode entry in `main.py` is clean and testable. No pipeline control endpoints yet — the server idles and waits for API requests.

**Requirements in scope:** INFRA-01, INFRA-02
**Out of scope:** All pipeline control, SSE stream, settings/feed management, DB viewer, log access — those are Phases 2–6.

</domain>

<decisions>
## Implementation Decisions

### EventBus Design
- **D-01:** `emit()` accepts a **typed event dataclass** — `PipelineEvent` (or similar) with a `type` discriminator field. Type-safe, mypy-checkable, straightforward to serialize to JSON for SSE.
- **D-02:** **Broadcast-all** subscription model — every subscriber queue receives every event. One `asyncio.Queue` per connected SSE client. No per-type filtering needed for a single-UI local tool.
- **D-03:** **Define the full `PipelineEventType` enum now** (all expected types: episode stage transitions, download/encode progress, run-level counters) even though Phase 1 won't emit them. Locks the Phase 2 contract.
- **D-04:** **Drop silently** when no subscribers are connected — `emit()` is a no-op if the subscriber list is empty. No buffering. Real-time stream; stale events before connect are useless.

### API Layer Structure
- **D-05:** New **`api/` top-level package** — mirrors `database/`, `components/`, etc. Nothing in `components/` or `utils/` knows about HTTP. Clear layer separation.
- **D-06:** `EventBus` lives at **`api/event_bus.py`** — owned by the API package, passed into `Pipeline` as a dependency injection. Keeps `utils/` generic.
- **D-07:** Routes organized as **one file per phase domain**: `api/routes/health.py` (Phase 1), `api/routes/events.py` (Phase 2), `api/routes/control.py` (Phase 3), etc. Each phase adds its own route module.
- **D-08:** API server as a **factory function `create_app(event_bus: EventBus) -> web.Application`** — aiohttp convention, easy to test with `aiohttp.test_utils.TestClient`, consistent with the project's stateless-class pattern.

### main.py Dual-Mode Entry
- **D-09:** **Extract `serve()` coroutine** from `main()` — `main()` dispatches: `if args.serve: await serve(config, host, port)` else `await run_pipeline(config)`. Each branch is independently testable.
- **D-10:** In serve mode, **pipeline runs only on API request** (Phase 3 introduces `POST /api/v1/run`). Phase 1 server idles after startup.
- **D-11:** Host/port via **CLI args only** — `--host` (default `0.0.0.0`) and `--port` (default `8080`) added to argparse. No `config.yaml` changes in Phase 1.

### Health Check Response
- **D-12:** Version from **`importlib.metadata.version('podcast-ad-cutter')`** — reads from `pyproject.toml` at runtime; no manual sync needed.
- **D-13:** Health response shape: `{"status": "ok", "uptime_seconds": 123.4, "version": "0.1.0"}` — minimal, covers INFRA-02 exactly.
- **D-14:** **Standard error envelope** for all API endpoints: success returns the resource/data directly; errors return `{"error": "message", "detail": {...}}`. Define this convention in Phase 1 so all subsequent phases follow it.

### Claude's Discretion
- Exact `PipelineEvent` dataclass field names and `PipelineEventType` enum member names — follow Python conventions, keep consistent with SSE event names in Phase 2.
- `api/__init__.py` contents — minimal; expose only `create_app` at package level.
- Whether `serve()` coroutine lives in `main.py` or is extracted to `api/server.py` — prefer `api/server.py` for testability.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Requirements & Architecture
- `.planning/ROADMAP.md` — Phase 1 goal, success criteria (INFRA-01, INFRA-02), and phase dependency chain
- `.planning/REQUIREMENTS.md` — Full v1 requirement list; traceability table mapping each requirement to a phase
- `.planning/PROJECT.md` — Core constraints: aiohttp already a dep; same-process server; config isolation rule; AppRunner+TCPSite mandate; SSE disconnect handling in `finally`

### Codebase Architecture
- `.planning/codebase/ARCHITECTURE.md` — Full layer diagram, component responsibilities, config isolation constraint, anti-patterns to avoid
- `.planning/codebase/STACK.md` — Dependency versions, test tooling (pytest-asyncio auto mode, aioresponses), ruff/mypy config
- `.planning/codebase/CONVENTIONS.md` — Coding conventions in use (if it exists; check before planning)

### Key Existing Files (integration points)
- `main.py` — Current entry point; argparse setup; `configure_logging`; `_rotate_logs` — must be modified, not replaced
- `components/pipeline.py` — `Pipeline.__init__` signature; `_Stores` dataclass; `_process_episode_until_final` — EventBus added as optional param here
- `config/config_loader.py` — `AppConfig`, `Config`, `load_config` — must NOT be imported below `Pipeline`

### CLAUDE.md Constraints (hard rules)
- Never share the aiosqlite connection between pipeline and API handlers
- Never use `web.run_app()` — use `AppRunner` + `TCPSite`
- Config writes must be atomic (validate → temp file → `os.replace()`)
- SSE disconnect: always unregister subscriber queue in a `finally` block

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `aiohttp` — already a dependency (`components/feed_downloader.py`, `components/episode_downloader.py`); no new dep needed
- `aioresponses` — already a test dependency for mocking aiohttp; use `aiohttp.test_utils.TestClient` / `aiohttp.test_utils.TestServer` for route handler tests
- `utils/exceptions.py` — `PodcastAdCutterError` base; consider a new `ApiError` subclass for HTTP error responses
- `config/config_loader.py` — `AppConfig` Pydantic model; `Config` wraps it + credentials. Phase 1 doesn't modify this, but `serve()` receives a `Config` instance.

### Established Patterns
- **Stateless classes with constructor injection** — EventBus should follow this pattern (no module-level singletons)
- **`async with`** context managers for every resource — AppRunner teardown should use this pattern
- **`asyncio_mode = "auto"`** in pytest config — async tests work without `@pytest.mark.asyncio`
- **F-strings only for logging** — no `%` operator
- **100% test coverage required** — every new file needs tests; `uv run pytest --cov=.` must pass

### Integration Points
- `main.py:main()` — dispatch point for serve mode; `--serve` flag added here
- `components/pipeline.py:Pipeline.__init__` — `event_bus: EventBus | None = None` parameter added here
- New `api/` package connects to `main.py` (entry) and `components/pipeline.py` (EventBus injection); nothing else in the pipeline layer knows about `api/`

</code_context>

<specifics>
## Specific Ideas

No specific references or examples given during discussion — open to standard aiohttp approaches consistent with the patterns above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 1-API Foundation*
*Context gathered: 2026-05-14*
