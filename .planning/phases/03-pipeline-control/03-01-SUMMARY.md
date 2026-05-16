---
phase: 03-pipeline-control
plan: 01
subsystem: api
tags: [aiohttp, asyncio, dataclass, run-state, rest]

requires:
  - phase: 02-sse-events
    provides: EventBus, create_app, serve, factory-pattern routers

provides:
  - RunState dataclass with state/started_at/active_feed_slug/current_episode_guid/task/stop_event/feeds
  - FeedRunCounts dataclass for per-feed episode counters
  - VALID_STAGES constant tuple with 5 valid stage names
  - create_control_router factory returning web.RouteTableDef
  - GET /api/v1/status endpoint returning live RunState as JSON
  - app["run_state"] stored on aiohttp Application
  - serve(host, port, config) accepting Config param

affects: [03-02-run-stop, 03-03-episode-control, future-phase-consumers]

tech-stack:
  added: []
  patterns:
    - "RunState dataclass (slots=True) instantiated inside async def serve() to avoid asyncio loop-binding bugs"
    - "control router factory create_control_router(config, event_bus, run_state) mirrors health/events pattern"
    - "TYPE_CHECKING guard for heavy imports in api/run_state.py and api/routes/control.py"

key-files:
  created:
    - api/run_state.py
    - api/routes/control.py
    - tests/test_api_run_state.py
    - tests/test_api_control.py
  modified:
    - api/server.py
    - main.py
    - tests/test_api_health.py
    - tests/test_api_events.py
    - tests/test_api_server.py
    - tests/test_main.py

key-decisions:
  - "RunState uses @dataclass(slots=True) consistent with _Stores in pipeline.py; instantiated inside async def serve() per RESEARCH Pitfall 1"
  - "datetime imported under TYPE_CHECKING in run_state.py (ruff TC003); slots=True works with from __future__ import annotations"
  - "config and event_bus args kept in create_control_router signature with # noqa: ARG001 — intentional stubs for Plans 02/03"

requirements-completed:
  - STAT-01

duration: 5min
completed: 2026-05-16
---

# Phase 3 Plan 01: RunState Foundation and GET /api/v1/status Summary

**RunState dataclass + GET /api/v1/status endpoint wired into aiohttp app via create_control_router factory**

## Performance

- **Duration:** 5 min
- **Started:** 2026-05-16T16:17:39Z
- **Completed:** 2026-05-16T16:22:07Z
- **Tasks:** 2
- **Files modified:** 8 (4 created, 4 updated)

## Accomplishments

- RunState dataclass (slots=True) with stop_event, task ref, per-feed FeedRunCounts dict, and reset_to_idle() method
- VALID_STAGES constant tuple containing exactly the 5 stage names for downstream validation
- create_control_router factory registered in create_app; GET /api/v1/status returns 200 with live RunState JSON
- serve() updated to accept Config, instantiate RunState() inside the coroutine (loop-safe), and forward both to create_app
- All existing Phase 1/2 tests updated to match new create_app/serve signatures — zero regressions

## Task Commits

1. **Task 1: RunState module** - `798ac62` (feat)
2. **Task 2: Control router + app wiring** - `667546f` (feat)

## New Module Exports

### api/run_state.py

- `VALID_STAGES: tuple[str, ...]` = `("download", "transcribe", "topic", "ad-detect", "edit")`
- `class FeedRunCounts` — `episodes_total: int`, `episodes_done: int`, `episodes_failed: int` (all default 0)
- `class RunState` — `state: str = "idle"`, `started_at: datetime | None`, `active_feed_slug: str | None`, `current_episode_guid: str | None`, `task: asyncio.Task | None`, `stop_event: asyncio.Event`, `feeds: dict[str, FeedRunCounts]`, `reset_to_idle() -> None`

### api/routes/control.py

- `def create_control_router(config: Config, event_bus: EventBus, run_state: RunState) -> web.RouteTableDef`

## Modified Signatures

### api/server.py — create_app (after)

```python
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
) -> web.Application:
```

Stores `app["run_state"] = run_state`. Registers control router after health and events routers.

### api/server.py — serve (after)

```python
async def serve(host: str, port: int, config: Config) -> None:
```

Creates `run_state = RunState()` inside the coroutine body before calling `create_app`.

## GET /api/v1/status Response Shape

```json
{
  "state": "idle",
  "started_at": null,
  "active_feed_slug": null,
  "current_episode_guid": null,
  "feeds": {}
}
```

When running: `started_at` is ISO 8601 string, `feeds` maps slug → `{"episodes_total": N, "episodes_done": N, "episodes_failed": N}`.

## Downstream Consumer Notes

- **Plan 02** attaches `POST /api/v1/run`, `POST /api/v1/run/stop`, and `POST /api/v1/feeds/{slug}/run` to the same router returned by `create_control_router`. It mutates `run_state.state`, `run_state.started_at`, `run_state.active_feed_slug`, and `run_state.task` when starting/stopping runs.
- **Plan 03** attaches `POST /api/v1/episodes/{guid}/reprocess` and `POST /api/v1/episodes/{guid}/skip` to the control router. It opens a dedicated short-lived aiosqlite connection per request (D-15) rather than sharing the pipeline's connection.
- Both plans receive `run_state` via the closure captured in `create_control_router` — no additional wiring needed in `create_app`.

## Decisions Made

- datetime imported under TYPE_CHECKING in run_state.py (ruff TC003 rule); `from __future__ import annotations` makes all annotations string literals at runtime so slots=True dataclass works correctly.
- config and event_bus kept in create_control_router signature with `# noqa: ARG001` — these are intentional forward stubs consumed by Plans 02 and 03.
- RunState instantiated inside `async def serve()` (not at module level) to guarantee asyncio.Event() has a running event loop when created (per RESEARCH Pitfall 1 / CLAUDE.md constraint).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Ruff lint errors in new files required fixes before tests could be committed**
- **Found during:** Task 1 (RunState module)
- **Issue:** Ruff flagged TC003 (datetime not in TYPE_CHECKING), D101/D102 (missing docstrings), ARG001 (unused args), I001 (import sort) across new files
- **Fix:** Moved datetime import under TYPE_CHECKING, added class/method docstrings, sorted imports in test file, added # noqa: ARG001 to intentional stub params in create_control_router
- **Files modified:** api/run_state.py, api/routes/control.py, tests/test_api_run_state.py
- **Verification:** `uv run ruff check .` exits 0
- **Committed in:** 798ac62, 667546f

---

**Total deviations:** 1 auto-fixed (Rule 1 - lint)
**Impact on plan:** Lint fixes were required for correctness per CLAUDE.md. No scope creep.

## Issues Encountered

None — existing test_api_health.py, test_api_events.py, test_api_server.py, and test_main.py needed signature updates due to the create_app/serve parameter additions, which is expected and documented in the plan.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- RunState substrate complete; Plans 02 and 03 can import and mutate RunState directly
- Control router factory registered and reachable via TestClient
- All 828 tests pass; 100% coverage on api/run_state.py and api/routes/control.py
- Ready for Plan 02: run/stop endpoints

---
*Phase: 03-pipeline-control*
*Completed: 2026-05-16*

## Self-Check: PASSED

- [x] `api/run_state.py` exists: `[ -f api/run_state.py ]` → FOUND
- [x] `api/routes/control.py` exists: `[ -f api/routes/control.py ]` → FOUND
- [x] `tests/test_api_run_state.py` exists: `[ -f tests/test_api_run_state.py ]` → FOUND
- [x] `tests/test_api_control.py` exists: `[ -f tests/test_api_control.py ]` → FOUND
- [x] Commit `798ac62` exists in git log → FOUND
- [x] Commit `667546f` exists in git log → FOUND
- [x] `uv run pytest -x` → 828 passed
- [x] `uv run ruff check .` → All checks passed
