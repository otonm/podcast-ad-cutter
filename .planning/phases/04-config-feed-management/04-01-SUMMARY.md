---
phase: 04-config-feed-management
plan: 01
subsystem: api
tags: [aiohttp, pydantic, yaml, settings, config, atomic-write]

# Dependency graph
requires:
  - phase: 03-pipeline-api-control
    provides: api/server.py create_app/serve, EventBus, RunState, existing test suite

provides:
  - GET /api/v1/settings — reads AppConfig fields from disk plus credential presence dict
  - PATCH /api/v1/settings — deep-merges, validates via Pydantic, atomic YAML write
  - AppConfig.model_config = ConfigDict(extra="forbid") — rejects unknown top-level keys
  - config_path: Path threaded through create_app/serve — prerequisite for plan 02 (feeds)

affects: [04-02-feeds, any phase that calls create_app or serve]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Settings router factory: create_settings_router(config_path) returning RouteTableDef"
    - "Re-read-from-disk on every GET — no in-memory cache, always fresh"
    - "Atomic config write: NamedTemporaryFile(dir=config_path.parent) + os.replace()"
    - "model_dump(mode=json) for all Path-containing Pydantic models before serialization"
    - "Credential presence dict: getattr(Credentials(), field) -> 'set'/'not set'"

key-files:
  created:
    - api/routes/settings.py
    - tests/test_api_settings.py
  modified:
    - config/config_loader.py
    - api/server.py
    - main.py
    - tests/test_config_loader.py
    - tests/test_api_health.py
    - tests/test_api_control.py
    - tests/test_api_events.py
    - tests/test_api_server.py
    - tests/test_main.py

key-decisions:
  - "config_path is a positional required arg (no default) in create_app/serve to surface call-site coverage gaps"
  - "PATCH strips 'feeds' key before merge — settings endpoint cannot mutate feed list (D-06)"
  - "os.replace used for POSIX-atomic config write; PTH105 ruff rule suppressed with noqa comment"
  - "Credential values never returned; only boolean presence as 'set'/'not set'"

patterns-established:
  - "Settings router: create_settings_router(config_path) factory pattern mirrors existing route factories"
  - "All config reads re-load from disk on every request (no stale in-memory state)"

requirements-completed: [STAT-02, STAT-03]

# Metrics
duration: 25min
completed: 2026-05-17
---

# Phase 4 Plan 01: Settings API Summary

**Settings read/write API with credential presence redaction, deep-merge PATCH, and atomic YAML writes; AppConfig hardened to reject unknown keys**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-17T13:00:00Z
- **Completed:** 2026-05-17T13:25:00Z
- **Tasks:** 3
- **Files modified:** 10

## Accomplishments

- AppConfig now rejects unknown top-level keys via ConfigDict(extra="forbid")
- GET /api/v1/settings re-reads config.yaml on every request and returns all AppConfig fields plus credential presence dict (never raw values)
- PATCH /api/v1/settings deep-merges payload, strips feeds key, validates with Pydantic, writes atomically via temp file + os.replace
- config_path threaded through create_app and serve as required positional arg, unblocking plan 02 (feeds management)
- All existing tests updated to pass the new config_path arg; 885 tests green; 100% coverage

## Task Commits

1. **Task 1: Harden AppConfig and thread config_path through serve/create_app** - `fc9a9e0` (feat)
2. **Task 2: GET /api/v1/settings with credentials presence redaction** - `6af3b93` (feat)
3. **Task 3: PATCH /api/v1/settings deep merge, validate, atomic write** - `317b804` (feat)

## Files Created/Modified

- `api/routes/settings.py` - Settings router with GET (credential redaction) and PATCH (deep-merge + atomic write)
- `tests/test_api_settings.py` - TestGetSettings (4 tests) and TestPatchSettings (7 tests)
- `config/config_loader.py` - Added ConfigDict(extra="forbid") to AppConfig
- `api/server.py` - Added config_path: Path param to create_app/serve; registered settings router
- `main.py` - Updated serve() call to pass args.config
- `tests/test_api_health.py` - Updated create_app call sites to pass tmp_path/"config.yaml"
- `tests/test_api_control.py` - Updated all create_app call sites
- `tests/test_api_events.py` - Updated all create_app call sites
- `tests/test_api_server.py` - Updated all serve() call sites
- `tests/test_main.py` - Updated serve() assertion to include config_path arg

## Decisions Made

- `config_path` is positional (no default) to guarantee the compiler catches any missing call site updates
- PATCH strips `feeds` key silently (plan D-06) — feed CRUD belongs to the feeds endpoint (plan 02)
- `os.replace` kept over `Path.replace()` per plan spec; PTH105 ruff warning suppressed with inline noqa comment (acceptance criteria requires `grep -n 'os.replace'` to match)
- `model_dump(mode="json")` used everywhere to safely serialize `pathlib.Path` fields

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] test_main.py serve assertion needed config_path arg**
- **Found during:** Task 1 (test call-site updates)
- **Issue:** `test_api_server.py` tests were updated per plan, but `tests/test_main.py::TestMain::test_serve_flag_calls_serve_and_not_pipeline` also asserts the `serve()` call signature — plan listed the test file to update but not this specific assertion
- **Fix:** Updated `mock_serve.assert_awaited_once_with("0.0.0.0", 8080, mock_cfg)` to include `Path("config.yaml")`
- **Files modified:** tests/test_main.py
- **Verification:** test_main.py full suite passes
- **Committed in:** fc9a9e0 (Task 1 commit)

**2. [Rule 1 - Bug] TC003 ruff rules required Path import in TYPE_CHECKING block**
- **Found during:** Task 1 (ruff check)
- **Issue:** `from pathlib import Path` in api/server.py triggers TC003 because `from __future__ import annotations` makes type annotations lazy
- **Fix:** Moved `from pathlib import Path` into `if TYPE_CHECKING:` block in both api/server.py and api/routes/settings.py
- **Files modified:** api/server.py, api/routes/settings.py
- **Verification:** `uv run ruff check .` passes
- **Committed in:** fc9a9e0, 317b804

**3. [Rule 1 - Bug] os.replace atomicity test assertion unpacking**
- **Found during:** Task 3 (test implementation)
- **Issue:** Initial test used a `replace_calls` list with `call(src, dst)` objects; unpacking `replace_calls[0][0]` returned empty tuple
- **Fix:** Switched to `MagicMock(wraps=os.replace)` and `mock_replace.call_args[0]` for correct arg extraction
- **Files modified:** tests/test_api_settings.py
- **Verification:** test_patch_os_replace_called_once passes
- **Committed in:** 317b804

---

**Total deviations:** 3 auto-fixed (1 blocking call-site, 2 Rule 1 bugs)
**Impact on plan:** All auto-fixes necessary for correctness. No scope creep.

## Issues Encountered

- Ruff import-sorting auto-fixed two inline imports in test_api_settings.py (yaml and MagicMock inside test methods) — resolved with `--fix` flag

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Plan 02 (feeds management) is unblocked: `config_path` is threaded through `create_app`, and `app["config_path"]` is available to all route handlers
- AppConfig rejects unknown keys — feed CRUD via PATCH /api/v1/feeds can safely rely on schema validation
- All existing tests green; test suite at 885 passing

---
*Phase: 04-config-feed-management*
*Completed: 2026-05-17*
