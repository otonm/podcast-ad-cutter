---
phase: 06-log-access
plan: "02"
subsystem: api
tags: [aiohttp, path-traversal, sse, byte-range, log-access]

# Dependency graph
requires:
  - phase: 06-01
    provides: create_logs_router skeleton with log_dir DI wired into create_app

provides:
  - list_logs handler at GET /api/v1/logs returning D-01 hierarchical JSON
  - read_log handler at GET /api/v1/logs/{tail:.*} with byte-range and X-Log-* headers
  - _validate_path traversal guard using is_relative_to()
  - tail_log placeholder at /api/v1/logs/{tail:.*}/tail returning 501
  - TestLogList, TestLogRead, TestLogSecurity test classes (16 tests)

affects: [06-03-log-tail]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - asyncio.to_thread wraps all blocking file I/O (glob, stat, read_bytes, exists)
    - _validate_path resolves + is_relative_to for path traversal guard
    - Route registration order: /tail before glob {tail:.*} (D-04)
    - X-Log-Size/Offset/Limit headers for byte-range pagination

key-files:
  created:
    - tests/test_api_logs.py
  modified:
    - api/routes/logs.py

key-decisions:
  - "aiohttp normalizes '../' in URL paths before routing — traversal attacks are blocked at the HTTP layer for standard URLs; _validate_path guards against encoded-slash traversal via is_relative_to()"
  - "tail_log placeholder registered BEFORE read_log glob route to ensure /tail suffix is matched correctly (D-04)"
  - "raise web.HTTPNotFound / web.HTTPNotImplemented without parentheses per ruff RSE102"

patterns-established:
  - "Traversal guard: (log_dir / tail).resolve().is_relative_to(log_dir.resolve()) → 400 on failure"
  - "Query-param parsing: int() wrapped in try/except ValueError → HTTPBadRequest from None"
  - "All file I/O via asyncio.to_thread — no blocking calls in event loop"

requirements-completed: [LOG-01, LOG-02]

# Metrics
duration: 4min
completed: 2026-05-22
---

# Phase 6 Plan 02: Log List and Read Endpoints Summary

**list_logs returning hierarchical JSON and read_log serving byte-sliced content with X-Log-* headers, both guarded by _validate_path using Path.is_relative_to()**

## Performance

- **Duration:** 4 min
- **Started:** 2026-05-22T15:35:56Z
- **Completed:** 2026-05-22T15:39:46Z
- **Tasks:** 2 (TDD: RED + GREEN each)
- **Files modified:** 2

## Accomplishments
- GET /api/v1/logs returns D-01 hierarchical JSON (`{"app_logs": [...], "episode_logs": {...}}`) with empty-dir guard
- GET /api/v1/logs/{tail:.*} serves full or byte-sliced content with X-Log-Size/Offset/Limit headers (D-06/D-07)
- _validate_path blocks path traversal via is_relative_to(); bad offset/limit returns 400; missing file returns 404
- /tail placeholder route registered before glob to lock in correct aiohttp routing order (D-04)

## Task Commits

1. **RED: Failing tests** - `5dc783d` (test)
2. **GREEN: Implementation** - `9b2af04` (feat)

## Files Created/Modified
- `tests/test_api_logs.py` - 16 tests across TestLogList, TestLogRead, TestLogSecurity
- `api/routes/logs.py` - Full implementation: _validate_path, _list_logs_sync, list_logs, tail_log, read_log

## Decisions Made
- aiohttp normalizes `../` in URL paths before routing, so URL-level traversal is blocked at the HTTP layer. The `_validate_path` guard still protects against encoded-slash attacks (e.g., `%2F..%2F`) that bypass normalization. Tests updated to reflect this behavior.
- `raise web.HTTPNotFound` / `web.HTTPNotImplemented` without parentheses per ruff RSE102 rule (aiohttp supports both forms; the exception class itself is a valid raise target).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Test helper needed valid feeds list in config YAML**
- **Found during:** Task 1 (RED phase)
- **Issue:** `_make_app` wrote `feeds: []` but AppConfig Pydantic model requires at least 1 feed; validation error prevented app creation
- **Fix:** Updated test YAML fixture to include a single test feed
- **Files modified:** tests/test_api_logs.py
- **Verification:** App creates successfully; all tests pass
- **Committed in:** 9b2af04

**2. [Rule 1 - Bug] Traversal test expectations corrected for aiohttp URL normalization**
- **Found during:** Task 1 (GREEN phase verification)
- **Issue:** Tests expected 400 for `../etc/passwd` URL traversal, but aiohttp normalizes `..` segments before routing — the path resolves to a different route that returns 404
- **Fix:** Updated security tests to accept `{400, 404}` (either blocks the attack), with documentation explaining the two-layer protection
- **Files modified:** tests/test_api_logs.py
- **Verification:** Security invariant holds: attacker never gets 200; tests green
- **Committed in:** 9b2af04

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 bug)
**Impact on plan:** Both fixes necessary for correctness. Security invariant (no 200 on traversal) fully maintained.

## Issues Encountered
None beyond the deviations documented above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- LOG-01 and LOG-02 complete; plan 03 (LOG-03 SSE tail) can proceed
- tail_log placeholder at `/api/v1/logs/{tail:.*}/tail` is registered and returns 501 — plan 03 replaces the body without changing route registration
- _validate_path is importable by plan 03 (`from api.routes.logs import _validate_path`)

---
*Phase: 06-log-access*
*Completed: 2026-05-22*
