---
phase: 05-database-viewer
plan: "02"
subsystem: api
tags: [aiohttp, aiosqlite, rest-api, read-only, pagination, tdd]
dependency_graph:
  requires: [05-01]
  provides: [GET /api/v1/db/episodes, GET /api/v1/db/transcriptions/{guid}, GET /api/v1/db/ads/{guid}, GET /api/v1/db/costs]
  affects: [api/routes/db.py, api/server.py, tests/test_api_db.py]
tech_stack:
  added: []
  patterns: [create_X_router factory, per-request Database context manager, slug reverse-lookup, filesystem glob for complete state]
key_files:
  created:
    - api/routes/db.py
    - tests/test_api_db.py (extended from 18 to 22 tests)
  modified:
    - api/server.py
decisions:
  - create_db_router receives config_path as third parameter (Pitfall 2 in RESEARCH.md — needed for ?feed slug reverse-lookup)
  - No create_app signature change — config.app.paths.output_dir already available (design_notes deviation from CONTEXT.md discretion note)
  - Filesystem complete-check applied to ALL non-skipped DB states (pending/downloaded/transcribed/processed) per D-04 — not only "processed"
  - _is_complete helper extracted as a pure function for clarity; returns False when pubdate is None (Pitfall 1)
  - zip(..., strict=False) for EPISODE_COLUMNS mapping (columns always match SELECT; strict=True would be noisy)
metrics:
  duration: "6m 4s"
  completed: "2026-05-19T07:37:10Z"
  tasks: 2
  files: 3
---

# Phase 05 Plan 02: DB Viewer Endpoints Summary

**One-liner:** Four read-only aiohttp endpoints expose episodes (with pipeline_state filesystem upgrade), transcriptions, ad detections, and LLM cost aggregates over a per-request SQLite connection.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing test scaffold for all 4 DB viewer endpoints | 6fea420 | tests/test_api_db.py |
| 2 (GREEN) | Implement create_db_router and wire into api/server.py | 54bb611 | api/routes/db.py, api/server.py, tests/test_api_db.py |

## Endpoint Contracts

### GET /api/v1/db/episodes
Query params: `?offset=0&limit=50&feed={slug}` (limit capped at 200, offset min 0; non-integer → 400)

Response: JSON array, each row contains all ~24 episodes columns plus:
- `feed_slug` — `slugify(podcast)`
- `pipeline_state` — derived from LEFT JOIN + filesystem check: `skipped | complete | processed | transcribed | downloaded | pending`
- `pipeline_state_db` is NOT returned (dropped from response)

Sort: `ORDER BY pubdate IS NULL ASC, pubdate DESC` (NULLs last, most recent first)

### GET /api/v1/db/transcriptions/{guid}
- 404 when no transcriptions row for guid
- 200: `{"guid", "text", "segments": [{"start", "end", "text"}, ...]}`
- Segment keys are `start`/`end` (not `start_ms`/`end_ms`) per D-08

### GET /api/v1/db/ads/{guid}
- 404 when no ad_detection_runs row for guid
- 200: `{"guid", "detected": true, "segments": [{"start_ms", "end_ms", "confidence", "sponsor", "ad_topic"}, ...]}`
- `indices` column never present in response (D-09)
- `detected` is always `true` when run row exists, even with empty segments

### GET /api/v1/db/costs
Query param: `?feed={slug}` (optional; unknown slug returns `{total: 0.0, by_model: [], by_episode: []}`)

Response: `{"total": float, "by_model": [{"provider", "model", "cost"}], "by_episode": [{"guid", "cost"}]}`
- `by_episode` always excludes NULL-guid rows (D-10)
- Feed-filtered: JOIN to episodes.podcast naturally excludes pre-migration NULL-guid rows from all sections

## Write Endpoints

None — only GET handlers registered. Verified:
```
grep -c "@routes.post\|@routes.patch\|@routes.delete\|@routes.put" api/routes/db.py → 0
```

## Coverage and Ruff Results

- `uv run pytest --cov=. -q`: 930 passed, 100% coverage
- `uv run ruff check .`: All checks passed

## Deviations from Plan

### Auto-applied Rule 2 adjustments

**1. [Rule 2 - Missing Coverage] Added 4 edge-case tests for uncovered branches**
- **Found during:** Task 2 post-GREEN coverage check
- **Issue:** 8 statements uncovered: `_resolve_slug` return None, HTTPBadRequest raise, empty-list return for unknown feed slug on episodes, empty response for unknown feed slug on costs
- **Fix:** Added `test_episodes_invalid_limit_returns_400`, `test_episodes_invalid_offset_returns_400`, `test_episodes_unknown_feed_slug_returns_empty_list`, `test_costs_unknown_feed_slug_returns_empty_response`
- **Files modified:** tests/test_api_db.py
- **Commit:** included in 54bb611

**2. [Rule 1 - Bug] Removed unused `_seed_episode` helper**
- **Found during:** Task 2 coverage check (lines 82-87 uncovered)
- **Issue:** Helper defined but never called (inline DB seeding was used instead)
- **Fix:** Removed the unused helper function
- **Files modified:** tests/test_api_db.py
- **Commit:** included in 54bb611

**3. [Rule 1 - Bug] Removed `# noqa: ASYNC240` directive**
- **Found during:** Task 2 ruff check
- **Issue:** Ruff flagged it as an unused noqa directive (this ruff version does not have the ASYNC240 rule active or the rule doesn't apply in this context)
- **Fix:** Removed the directive
- **Files modified:** api/routes/db.py
- **Commit:** included in 54bb611

## Known Stubs

None.

## Threat Flags

All mitigations from the plan's threat register applied:
- T-05-02-01: `?feed` slug reverse-resolved via `_resolve_slug(slug, cfg.feeds)` → parameterized `WHERE e.podcast = ?`
- T-05-02-02: `int()` wrapped in try/except ValueError → 400; `max(0, ...)` / `min(..., 200)` applied
- T-05-02-03: `{guid}` used only in parameterized SQL `?` — never in filesystem paths
- T-05-02-04: Every handler opens `async with Database(db_path) as db:` per request (4 occurrences confirmed by grep)
- T-05-02-05: Limit capped at 200 server-side
- T-05-02-07: `indices` excluded from SELECT and response (grep confirms 0 occurrences in db.py outside schema string)
- T-05-02-09: No write paths (grep confirms 0 POST/PATCH/DELETE/PUT routes)
- T-05-02-10: Glob pattern built from `slugify()` output + constant strftime — no user input reaches filesystem paths

## Self-Check: PASSED

- api/routes/db.py exists: ✓
- Contains `def create_db_router(db_path`: ✓
- 4 `@routes.get(` decorators: ✓
- 0 write-method decorators: ✓
- 4 `async with Database(db_path)` calls: ✓
- Contains `ORDER BY e.pubdate IS NULL ASC, e.pubdate DESC`: ✓
- Contains `"detected": True`: ✓
- Contains `guid IS NOT NULL` in by_episode query: ✓
- api/server.py imports and calls `create_db_router`: ✓
- `uv run pytest tests/test_api_db.py -x -q`: 22 passed ✓
- `uv run pytest --cov=. -q`: 930 passed, 100% coverage ✓
- `uv run ruff check .`: All checks passed ✓
- Commits 6fea420 (RED) and 54bb611 (GREEN) verified in git log ✓
