---
phase: 04-config-feed-management
plan: 02
subsystem: api
tags: [feeds, crud, aiohttp, pydantic, sqlite, tdd]
dependency_graph:
  requires: [04-01]
  provides: [FEED-01, FEED-02, FEED-03, FEED-04]
  affects: [api/server.py, config/config_loader.py]
tech_stack:
  added: []
  patterns: [per-request Database context, tempfile+os.replace atomic write, slugify slug resolution]
key_files:
  created:
    - api/routes/feeds.py
    - tests/test_api_feeds.py
  modified:
    - api/server.py
    - config/config_loader.py
decisions:
  - "FeedConfig.enabled defaults to True — POST bodies that omit it get the safe default"
  - "FeedConfig.episodes_to_keep defaults to 10 — consistent with plan intent for POST bodies"
  - "FeedConfig gets model_config=ConfigDict(extra='forbid') — rejects unknown keys with 422"
  - "create_feeds_router carries noqa C901/PLR0915 — all four CRUD handlers live in one factory per project pattern"
metrics:
  duration: ~15m
  completed: 2026-05-17
  tasks: 3
  files_changed: 4
---

# Phase 04 Plan 02: Feeds API Summary

Feed CRUD API shipping GET/POST/PATCH/DELETE /api/v1/feeds with DB-backed episode counts, Pydantic validation, slug resolution, and atomic config writes.

## Requirements Satisfied

| ID | Description | Status |
|----|-------------|--------|
| FEED-01 | GET /api/v1/feeds with slug, title, url, enabled, episodes_to_keep, episode_count | Done |
| FEED-02 | POST /api/v1/feeds — validate, duplicate guard, atomic append | Done |
| FEED-03 | PATCH /api/v1/feeds/{slug} — partial merge, title excluded, 404 on unknown | Done |
| FEED-04 | DELETE /api/v1/feeds/{slug} — atomic remove, 404 unknown, 422 last feed | Done |

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Write failing tests for feeds API | c2cf648 | tests/test_api_feeds.py |
| 1-3 (GREEN) | Implement feeds API | 437bb06 | api/routes/feeds.py, api/server.py, config/config_loader.py, tests/test_api_feeds.py |

## TDD Gate Compliance

- RED commit: c2cf648 — failing tests for all four FEED requirements (19 tests, all failing at import)
- GREEN commit: 437bb06 — implementation passes all 19 tests; full suite 904 tests green

## Coverage

```
api/routes/feeds.py       96      0   100%
api/routes/settings.py    52      0   100%
TOTAL                  10147      0   100%
```

100% coverage for both new route modules and overall project.

## Key Implementation Details

**GET /api/v1/feeds** — Opens one `async with Database(db_path)` context per request (not per feed), runs `SELECT COUNT(*) FROM episodes WHERE podcast = ?` with parameterized placeholder for each feed title (T-04-11 SQL injection mitigation).

**POST /api/v1/feeds** — Duplicate title check before `FeedConfig.model_validate` prevents title collisions (T-04-08). Returns 201 with the created feed.

**PATCH /api/v1/feeds/{slug}** — `payload.pop("title", None)` strips title from every PATCH body (D-11/T-04-09). Shallow merge `{**existing, **payload}` then re-validates through `FeedConfig.model_validate` — Pitfall 5 handled correctly.

**DELETE /api/v1/feeds/{slug}** — Constructs new `AppConfig` via `model_validate` so `min_length=1` on `feeds` fires as 422 when deleting the last feed (T-04-10). Returns 204 No Content with no body.

All write handlers use `_write_config_sync` (tempfile in same dir + `os.replace`) via `asyncio.to_thread` (T-04-13).

## Deviations from Plan

### Auto-added Missing Functionality

**1. [Rule 2 - Missing defaults] Added FeedConfig.enabled=True default and episodes_to_keep=10 default**
- Found during: Task 1 implementation
- Issue: Plan required POST to default `enabled=True` and `episodes_to_keep` to model default, but FeedConfig had `enabled: bool` (required, no default) and `episodes_to_keep: int = Field(ge=1)` (no default value, only constraint)
- Fix: Added `enabled: bool = True` and `episodes_to_keep: int = Field(default=10, ge=1)` to FeedConfig
- Files modified: config/config_loader.py
- Commit: 437bb06

**2. [Rule 2 - Missing validation] Added FeedConfig model_config=ConfigDict(extra="forbid")**
- Found during: Task 2 — POST extra key test required 422 for unknown fields
- Issue: FeedConfig lacked `extra="forbid"` while AppConfig already had it; plan explicitly required this check
- Fix: Added `model_config = ConfigDict(extra="forbid")` to FeedConfig class body
- Files modified: config/config_loader.py
- Commit: 437bb06

## Known Stubs

None — all endpoints are fully wired with real config/DB access.

## Threat Surface Scan

No new network endpoints beyond those specified in the plan's threat model. All T-04-07 through T-04-14 mitigations implemented as designed.

## Self-Check: PASSED

- api/routes/feeds.py: exists, 96 lines, 100% coverage
- tests/test_api_feeds.py: exists, 19 tests, all pass
- api/server.py: create_feeds_router imported and registered
- config/config_loader.py: FeedConfig updated with defaults and extra="forbid"
- Commits c2cf648 and 437bb06: present in git log
- Full suite: 904 passed, 0 failed
- Coverage: 100% overall
- Ruff: all checks passed
