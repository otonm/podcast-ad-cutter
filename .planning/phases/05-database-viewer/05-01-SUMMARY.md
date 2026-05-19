---
phase: 05-database-viewer
plan: "01"
subsystem: database
tags: [sqlite, migration, cost-tracking, schema, tdd]
dependency_graph:
  requires: []
  provides: [cost_tracking.guid column, save_cost guid parameter, pipeline guid linkage]
  affects: [database/connection.py, database/cost_tracking_store.py, components/pipeline.py]
tech_stack:
  added: []
  patterns: [idempotent ALTER TABLE migration, optional keyword argument extension, surgical call-site update]
key_files:
  created: []
  modified:
    - database/connection.py
    - database/cost_tracking_store.py
    - components/pipeline.py
    - tests/test_database_connection.py
    - tests/test_cost_tracking_store.py
decisions:
  - ALTER TABLE wrapped in contextlib.suppress(aiosqlite.OperationalError) — idempotent per existing pattern
  - guid is optional with default None — backward compatible with all existing callers
  - No CREATE TABLE modification — migration is the only mechanism (matches D-01 constraint)
metrics:
  duration: "2m 12s"
  completed: "2026-05-19T07:28:37Z"
  tasks: 3
  files: 5
---

# Phase 05 Plan 01: cost_tracking guid column migration and pipeline linkage Summary

**One-liner:** Idempotent ALTER TABLE adds nullable guid column to cost_tracking; save_cost extended with optional guid kwarg; all three pipeline call sites pass episode.guid.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for guid column migration | 6202edc | tests/test_database_connection.py |
| 1 (GREEN) | Idempotent cost_tracking guid migration | 44a1210 | database/connection.py |
| 2 (RED) | Failing tests for save_cost guid param | 1e57c25 | tests/test_cost_tracking_store.py |
| 2 (GREEN) | Extend save_cost with optional guid | 012957e | database/cost_tracking_store.py |
| 3 | Pass episode.guid at all three call sites | 97504e9 | components/pipeline.py |

## Schema Change

**cost_tracking before:**
```
id INTEGER PRIMARY KEY AUTOINCREMENT
provider TEXT NOT NULL
model TEXT NOT NULL
cost REAL NOT NULL
```

**cost_tracking after:**
```
id INTEGER PRIMARY KEY AUTOINCREMENT
provider TEXT NOT NULL
model TEXT NOT NULL
cost REAL NOT NULL
guid TEXT REFERENCES episodes(guid)   -- nullable; NULL for pre-migration rows
```

Migration statement:
```sql
ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid)
```
Wrapped in `contextlib.suppress(aiosqlite.OperationalError)` — idempotent on already-migrated DBs.

## Call Site Updates Confirmed

All three `save_cost()` invocations in `_process_episode` now pass `guid=episode.guid`:

- Line ~653 — ad detection cost: `await stores.cost.save_cost(ad_cost, guid=episode.guid)`
- Line ~674 — topic extraction cost: `await stores.cost.save_cost(topic_cost, guid=episode.guid)`
- Line ~709 — transcription cost: `await stores.cost.save_cost(cost, guid=episode.guid)`

Grep verification: `grep -c 'save_cost(.*guid=episode\.guid' components/pipeline.py` → 3

## Coverage and Ruff Results

- `uv run pytest --cov=.`: 908 passed, 100% coverage
- `uv run ruff check .`: All checks passed

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Threat Flags

No new trust boundaries introduced. All SQL uses parameterized `?` placeholders (T-05-01-02 mitigated). Migration is idempotent (T-05-01-01 mitigated).

## Self-Check: PASSED

- database/connection.py: contains `ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid)` ✓
- database/cost_tracking_store.py: contains `async def save_cost(self, cost: CostRecord, guid: str | None = None)` ✓
- components/pipeline.py: 3 call sites with `guid=episode.guid` ✓
- All 5 commits verified in git log ✓
- 908 tests passing, 100% coverage ✓
