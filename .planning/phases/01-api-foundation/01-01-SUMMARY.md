---
phase: 01-api-foundation
plan: "01"
subsystem: api
tags: [aiohttp, event-bus, health-check, dual-mode, tdd]
dependency_graph:
  requires: []
  provides:
    - api/event_bus.py:EventBus
    - api/server.py:create_app
    - api/server.py:serve
    - api/routes/health.py:create_health_router
    - GET /api/v1/health
  affects:
    - main.py
    - components/pipeline.py
tech_stack:
  added: []
  patterns:
    - AppRunner + TCPSite server lifecycle (aiohttp; no web.run_app)
    - asyncio.Queue broadcast event bus (snapshot iteration for concurrency safety)
    - Application factory pattern (create_app for TestClient testability)
    - TYPE_CHECKING-guarded import to avoid circular imports
    - StrEnum for event type discrimination
key_files:
  created:
    - api/__init__.py
    - api/event_bus.py
    - api/server.py
    - api/routes/__init__.py
    - api/routes/health.py
    - tests/test_api_event_bus.py
    - tests/test_api_health.py
    - tests/test_api_server.py
  modified:
    - main.py
    - components/pipeline.py
    - tests/test_main.py
decisions:
  - EventBus uses asyncio.Queue broadcast with snapshot iteration for concurrency safety
  - health handler uses importlib.metadata with tomllib fallback for version resolution
  - serve() uses asyncio.Event().wait() for idle keep-alive (no busy loop)
  - EventBus import in pipeline.py guarded by TYPE_CHECKING to avoid circular import at runtime
  - 0.0.0.0 default binding accepted per T-01-01 threat disposition (operator firewall responsibility)
metrics:
  duration: "~15 minutes"
  completed: "2026-05-14"
  tasks_completed: 3
  files_modified: 10
---

# Phase 1 Plan 1: API Foundation Walking Skeleton Summary

**One-liner:** aiohttp walking skeleton with AppRunner+TCPSite server, asyncio.Queue broadcast EventBus, GET /api/v1/health endpoint, dual-mode main.py dispatch, and optional EventBus injection into Pipeline.

## What Was Built

The thinnest possible end-to-end slice for the Web API milestone:

- **`api/` package** (5 files): EventBus, server factory, health route — all HTTP concerns isolated in the `api/` layer
- **EventBus** (`api/event_bus.py`): `PipelineEventType` StrEnum with all 7 members locked now (D-03); `PipelineEvent` dataclass with `type` discriminator and `payload: dict`; broadcast-all subscription model with one `asyncio.Queue` per subscriber; snapshot iteration in `emit()` for concurrency safety
- **Server factory** (`api/server.py`): `create_app(event_bus, start_time)` builds a testable `web.Application`; `serve(host, port)` coroutine starts AppRunner+TCPSite and idles via `asyncio.Event().wait()` — never calls `web.run_app()`
- **Health endpoint** (`api/routes/health.py`): `GET /api/v1/health` returns `{"status": "ok", "uptime_seconds": <float>, "version": <str>}`; version resolved via `importlib.metadata` with `tomllib` fallback
- **Dual-mode entry** (`main.py`): `--serve`, `--host`, `--port` CLI args added; `if args.serve:` dispatches to `serve()` and returns; bare invocation falls through to existing pipeline path
- **Pipeline EventBus injection** (`components/pipeline.py`): keyword-only `event_bus: EventBus | None = None` param added; import guarded by `TYPE_CHECKING` to avoid circular import; stored as `self._event_bus` for Phase 3 wiring

## Test Results

- **783 tests pass** (759 existing + 24 new), 100% coverage
- `uv run ruff check .` — clean, zero errors
- `grep -c 'run_app' api/server.py` — returns 0

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 (RED) | edfe92a | test(01-01): RED — API skeleton scaffolds and failing test suite |
| 2 (GREEN) | c23e125 | feat(01-01): GREEN — implement EventBus, health route, and server factory |
| 3 (WIRE) | 541e594 | feat(01-01): wire dual-mode entry in main.py and optional EventBus in Pipeline |

## TDD Gate Compliance

- **RED gate:** `test(01-01)` commit exists (edfe92a) — 17 failing API tests confirmed
- **GREEN gate:** `feat(01-01)` commit exists (c23e125) — all 22 API tests pass
- **REFACTOR gate:** Not needed — implementations were clean on first pass

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] ruff lint fixes applied during Task 3**

- **Found during:** Task 3 post-implementation ruff check
- **Issues:**
  - `ARG001`: Unused `request` param in health handler → renamed to `_request`
  - `S104`: `0.0.0.0` default binding → added `# noqa: S104` with threat model reference (T-01-01 accepted disposition)
  - `PT011`: `pytest.raises(ValueError)` too broad → added `match="x not in list"`
  - `I001`: Import sort order in 3 test files → fixed via `ruff --fix`
- **Fix:** Applied inline during Task 3 before final commit
- **Files modified:** `api/routes/health.py`, `main.py`, `tests/test_api_event_bus.py`, `tests/test_api_health.py`, `tests/test_api_server.py`
- **Commit:** 541e594

**2. [Rule 2 - Clarity] Removed `run_app` from server.py docstrings**

- **Found during:** Task 3 acceptance criteria check (`grep -c 'run_app' api/server.py` returned 2)
- **Issue:** Docstrings explaining the prohibition on `web.run_app()` contained the string itself
- **Fix:** Rewrote docstrings to describe the pattern used rather than the forbidden alternative
- **Files modified:** `api/server.py`
- **Commit:** 541e594

## Known Stubs

None — all implemented. Phase 1 intentionally stores `self._event_bus` without wiring it to pipeline stages; this is by design (D-10: pipeline control comes in Phase 3).

## Threat Flags

No new security surface beyond what was defined in the plan's threat model.

## Self-Check: PASSED

- `api/__init__.py` — EXISTS
- `api/event_bus.py` — EXISTS
- `api/server.py` — EXISTS
- `api/routes/__init__.py` — EXISTS
- `api/routes/health.py` — EXISTS
- `tests/test_api_event_bus.py` — EXISTS
- `tests/test_api_health.py` — EXISTS
- `tests/test_api_server.py` — EXISTS
- Commits edfe92a, c23e125, 541e594 — FOUND in git log
