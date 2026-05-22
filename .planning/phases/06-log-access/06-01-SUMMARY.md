---
phase: 06-log-access
plan: 01
subsystem: api
tags: [aiohttp, python, dependency-injection, routing]

# Dependency graph
requires:
  - phase: 05-database-viewer
    provides: create_db_router factory pattern that create_logs_router mirrors
provides:
  - api/routes/logs.py with create_logs_router(log_dir: Path) skeleton factory
  - create_app() extended with log_dir: Path as the 6th required positional parameter
  - serve() wired to pass config.app.paths.log_dir to create_app()
affects: [06-log-access plan 02, 06-log-access plan 03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "create_logs_router(log_dir) factory skeleton following create_db_router pattern"
    - "log_dir injected as required positional arg to create_app() — no optional params"

key-files:
  created:
    - api/routes/logs.py
  modified:
    - api/server.py
    - tests/test_api_control.py
    - tests/test_api_events.py
    - tests/test_api_feeds.py
    - tests/test_api_health.py
    - tests/test_api_db.py
    - tests/test_api_settings.py

key-decisions:
  - "log_dir added as the last positional (required) parameter to create_app(), not keyword-only — matches create_db_router arg style"
  - "logs.py uses noqa: TC003 to keep Path imported at runtime (not TYPE_CHECKING) — required by plan spec for use in function signature"
  - "Skeleton router returns empty RouteTableDef — handlers deferred to plans 02 and 03"
  - "Test call sites use tmp_path / 'logs' which does not need to exist — skeleton router never reads it"

patterns-established:
  - "create_logs_router(log_dir: Path) -> web.RouteTableDef: factory pattern established, handlers to be added in plans 02/03"

requirements-completed: [LOG-01, LOG-02, LOG-03]

# Metrics
duration: 4min
completed: 2026-05-22
---

# Phase 6 Plan 01: Log Access DI Seam Summary

**log_dir dependency-injection seam added to create_app() with skeleton create_logs_router factory; all 6 test files updated to pass the new required arg; 938 tests green at 100% coverage**

## Performance

- **Duration:** 4 min
- **Started:** 2026-05-22T15:31:05Z
- **Completed:** 2026-05-22T15:35:00Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments
- Created `api/routes/logs.py` with `create_logs_router(log_dir: Path) -> web.RouteTableDef` skeleton factory (no handlers — deferred to plans 02/03)
- Extended `create_app()` with `log_dir: Path` as the 6th required positional parameter and wired `app.add_routes(create_logs_router(log_dir))`
- Updated `serve()` to pass `config.app.paths.log_dir` to `create_app()`
- Updated all 40+ `create_app()` call sites across 6 test files — full suite green (938 tests, 100% coverage, ruff clean)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add log_dir parameter to create_app and register skeleton logs router** - `9cf36b7` (feat)
2. **Task 2: Update all existing create_app() call sites to pass log_dir** - `9df591d` (feat)

## Files Created/Modified
- `api/routes/logs.py` - New file: create_logs_router(log_dir) skeleton factory
- `api/server.py` - Added log_dir param, import, route registration, serve() wiring
- `tests/test_api_health.py` - create_app() call sites extended with log_dir
- `tests/test_api_events.py` - create_app() call sites extended with log_dir
- `tests/test_api_control.py` - create_app() call sites extended with log_dir
- `tests/test_api_feeds.py` - create_app() call sites extended with log_dir
- `tests/test_api_db.py` - create_app() call sites extended with log_dir
- `tests/test_api_settings.py` - create_app() call sites extended with log_dir

## Decisions Made
- `Path` is kept as a runtime import in `logs.py` (not under `TYPE_CHECKING`) per plan spec — required for the function signature. Ruff TC003 suppressed with `# noqa: TC003`.
- `noqa: ARG001` applied to `create_logs_router` signature — `log_dir` is intentionally unused in the skeleton; plans 02/03 will use it.
- Test call sites broken across multiple lines to satisfy ruff E501 (120-char limit) introduced by the new arg.

## Deviations from Plan

None - plan executed exactly as written. The ruff E501 line-length violations were created by this plan's own changes (not pre-existing), so fixing them is a Rule 3 (blocking) auto-fix within the same task scope.

## Issues Encountered
- Ruff flagged TC003 (Path import), ARG001 (unused log_dir), and E501 (line length) after the initial implementation. All resolved with noqa annotations and multi-line formatting.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `create_logs_router(log_dir)` contract established; plans 02 and 03 can now add handlers to the skeleton without touching `create_app()` or `serve()`
- No blockers

---
*Phase: 06-log-access*
*Completed: 2026-05-22*
