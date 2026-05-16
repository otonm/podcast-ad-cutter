---
phase: 03-pipeline-control
plan: 02
subsystem: api
tags: [aiohttp, asyncio, pipeline, run-lifecycle, sse, slugify]

# Dependency graph
requires:
  - phase: 03-01
    provides: RunState dataclass, FeedRunCounts, VALID_STAGES, GET /api/v1/status, create_control_router skeleton
provides:
  - Pipeline.stop_event + run_state kwargs with per-episode state updates and graceful-stop loop break
  - POST /api/v1/run — 202/409 with background pipeline task via asyncio.create_task
  - POST /api/v1/run/stop — graceful (stop_event.set) and force (task.cancel) modes, 409 when idle
  - POST /api/v1/feeds/{slug}/run — slug-to-title resolution via slugify, 404 on unknown slug
  - _run_pipeline_task wrapper — CancelledError re-raise contract, reset_to_idle in finally
  - _resolve_slug helper — matches slugify(feed.title) against URL slug
affects:
  - 03-03  # episode control endpoints reuse same 409 gate by reading run_state.state

# Tech tracking
tech-stack:
  added: []
  patterns:
    - asyncio.create_task inside handler with task ref stored on run_state.task
    - 409 gate — synchronous state check before create_task; single-threaded asyncio prevents TOCTOU
    - CancelledError must re-raise after finally reset (wrapper contract)
    - slugify(feed.title) is the canonical feed_slug for URL-to-title resolution

key-files:
  created:
    - tests/test_pipeline_stop.py
  modified:
    - components/pipeline.py
    - api/routes/control.py
    - tests/test_api_control.py

key-decisions:
  - "D-02: asyncio.create_task inside handler; task ref stored on run_state.task"
  - "D-03: 409 gate is synchronous state check before create_task; TOCTOU prevented by single-threaded asyncio"
  - "D-04: CancelledError must re-raise after finally reset — swallowed cancel is an anti-pattern"
  - "D-06: _resolve_slug matches slugify(feed.title) against URL slug, returns feed.title or None"
  - "Pipeline.__init__ new kwargs default to None to preserve existing CLI and test call sites"

patterns-established:
  - "run-lifecycle gate: check run_state.state == 'idle' synchronously before asyncio.create_task"
  - "_run_pipeline_task wrapper: try/except CancelledError with raise, except Exception with logger.exception, finally reset_to_idle"
  - "per-episode run_state updates: set current_episode_guid before, clear in finally; write FeedRunCounts after success and failure"

requirements-completed:
  - STAT-01
  - CTRL-01
  - CTRL-02
  - CTRL-03

# Metrics
duration: 35min
completed: 2026-05-16
---

# Phase 3 Plan 02: Run Lifecycle Endpoints Summary

**REST run-lifecycle vertical slice: POST /run, POST /run/stop (graceful + force), POST /feeds/{slug}/run with 409 gate, CancelledError-safe wrapper, and per-episode RunState updates**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-05-16T00:00:00Z
- **Completed:** 2026-05-16T00:35:00Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments

- Pipeline gains `stop_event` and `run_state` optional kwargs; episode loop checks stop_event after each episode and writes FeedRunCounts on success and failure
- Three new POST endpoints with strict 409 gate; `_run_pipeline_task` wrapper guarantees reset_to_idle in every exit path including CancelledError
- `_resolve_slug` helper maps URL slugs to feed titles via `slugify(feed.title)`; unknown slug returns 404
- 100% coverage on `api/routes/control.py` and `components/pipeline.py`; 846 tests pass

## Task Commits

1. **Task 1: Extend Pipeline with stop_event + run_state hooks** - `546e621` (feat)
2. **Task 2: Add /run, /run/stop, /feeds/{slug}/run handlers + wrapper + slug resolver** - `2fa647b` (feat)

## Files Created/Modified

- `components/pipeline.py` — Added `stop_event: asyncio.Event | None` and `run_state: RunState | None` kwargs; per-episode guid tracking and FeedRunCounts updates; graceful-stop break after episode finally block
- `api/routes/control.py` — Added `_resolve_slug`, `_run_pipeline_task` wrapper, and three POST handlers inside `create_control_router`
- `tests/test_pipeline_stop.py` — New: TestGracefulStop (stop_event halts after first episode, no stop_event processes all), TestRunStateUpdates (guid set/cleared mid-episode, feed counts after success and failure)
- `tests/test_api_control.py` — Expanded: TestStartRun, TestStopRun, TestFeedRun, TestRunStateLifecycle, TestResolveSlug

## Pipeline Constructor Evolution

Original: `Pipeline(config, feed_name, *, event_bus)`

Extended: `Pipeline(config, feed_name, *, event_bus, stop_event, run_state)`

Both new kwargs default to `None`, preserving all existing CLI and test call sites without modification.

## _run_pipeline_task Wrapper Contract

```python
async def _run_pipeline_task(pipeline, run_state):
    try:
        await pipeline.run()
    except asyncio.CancelledError:
        logger.info("Pipeline task cancelled (force stop)")
        raise          # MUST re-raise — swallowed CancelledError breaks asyncio task lifecycle
    except Exception:
        logger.exception("Pipeline run failed")
    finally:
        run_state.reset_to_idle()  # runs on every exit path: success, exception, cancellation
```

The `raise` on `CancelledError` is load-bearing: omitting it would make `task.cancel()` silently succeed without propagating the cancellation signal, leaving asyncio's task scheduler in an inconsistent state.

## Slug Resolution Helper

```python
def _resolve_slug(slug: str, feeds: list) -> str | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None
```

Feeds are already Pydantic-validated at config load time. No SQL or filesystem path uses the slug; mismatch returns 404 rather than raising.

## 409 Gate Reuse (Plan 03 Note)

The 409 gate pattern established here — `if run_state.state != "idle": raise HTTPConflict(...)` — is reused verbatim by episode control endpoints in Plan 03. The gate is correct because asyncio's single-threaded event loop prevents TOCTOU between the state check and `asyncio.create_task`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added episode-failure run_state update test**
- **Found during:** Task 2 (coverage check on components/pipeline.py)
- **Issue:** The `except Exception` branch in the episode loop that updates `run_state.feeds` (line 298) had no test coverage
- **Fix:** Added `test_run_state_feed_counts_update_after_episode_failure` in `tests/test_pipeline_stop.py`
- **Files modified:** `tests/test_pipeline_stop.py`
- **Verification:** 100% coverage on components/pipeline.py confirmed
- **Committed in:** `2fa647b` (Task 2 commit)

**2. [Rule 2 - Missing Critical] Added exception-path test for _run_pipeline_task**
- **Found during:** Task 2 (coverage check on api/routes/control.py)
- **Issue:** `except Exception` branch in `_run_pipeline_task` (lines 38-39) not covered
- **Fix:** Added `test_exception_logs_and_resets_state` in `TestRunStateLifecycle`
- **Files modified:** `tests/test_api_control.py`
- **Verification:** 100% coverage on api/routes/control.py confirmed
- **Committed in:** `2fa647b` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 missing critical — both coverage gaps on exception branches)
**Impact on plan:** Both required for 100% coverage mandate. No scope creep.

## Issues Encountered

- `asyncio` import in `components/pipeline.py` triggered ruff TC003 (move to TYPE_CHECKING block). Since `asyncio.Event` is only used as a type annotation and `from __future__ import annotations` makes all annotations strings, moving `asyncio` to TYPE_CHECKING is correct. Runtime usage (`self._stop_event.is_set()`) calls a method on an already-instantiated object — no runtime import needed.
- Pre-existing uncovered line 3245 in `tests/test_pipeline.py` (`_run_all_patches` dead function) means `uv run pytest --cov=. --cov-fail-under=100` reports 99.98% globally. This is out-of-scope pre-existing dead code. Coverage on all files created/modified by this plan is 100%.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes beyond those documented in the plan's threat model.

## Next Phase Readiness

- Plan 03 (episode control) can import `_resolve_slug` and the 409 gate pattern directly from `api/routes/control.py`
- `RunState` live counters are now populated during runs — `GET /api/v1/status` returns live data
- `Pipeline` CLI behavior unchanged; existing `main.py` call site requires no modification

---
*Phase: 03-pipeline-control*
*Completed: 2026-05-16*
