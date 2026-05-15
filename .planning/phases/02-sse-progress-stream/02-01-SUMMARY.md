---
phase: "02"
plan: "01"
subsystem: pipeline-events
tags: [pipeline, events, tdd]
key-files:
  - components/pipeline.py
  - tests/test_pipeline.py
metrics:
  tasks_completed: 11
  tests_added: 32
---

# Plan 02-01 Summary: Pipeline Event Instrumentation

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 02-01-01 | cae0db7 | test(02-01): add failing tests for _Stores counter fields |
| 02-01-02 | e99a401 | feat(02-01): add episodes_total/done/failed counter fields to _Stores |
| 02-01-03 | db3b520 | test(02-01): add failing tests for RUN_STARTED and RUN_COMPLETED emits |
| 02-01-04 | eef1f6c | feat(02-01): implement RUN_STARTED and RUN_COMPLETED emits in Pipeline.run() |
| 02-01-05 | 69a10c4 | test(02-01): add failing tests for EPISODE_STAGE_CHANGED started+completed pairs |
| 02-01-06 | 3cb65e9 | feat(02-01): implement EPISODE_STAGE_CHANGED emits for all 6 stage transitions |
| 02-01-07 | ebc2bee | test(02-01): add failing tests for DOWNLOAD_PROGRESS and ENCODE_PROGRESS events |
| 02-01-08 | ba2e05b | feat(02-01): implement DOWNLOAD_PROGRESS and ENCODE_PROGRESS emits via closure |
| 02-01-09 | d731aa6 | test(02-01): add failing tests for EPISODE_COMPLETED and EPISODE_FAILED events |
| 02-01-10 | 446f11c | feat(02-01): implement EPISODE_COMPLETED/FAILED emits with counter increments |
| 02-01-11 | f6e2175 | chore(02-01): verify 100% coverage and fix ruff lint issues |

## Deviations

- `episodes_total` field ordered before `episodes_done/failed` on `_Stores` (plan listed them in reverse); Python dataclasses require non-default fields before default fields — semantically identical.
- `_emit_stage()` helper added to reduce repetition across 12 emit sites (6 stages × 2 statuses); plan implied inline guards but a helper is cleaner with zero behavioral difference.
- `_process_episode_until_final` now returns `str` outcome ("skipped"|"edited"|"copied") to allow EPISODE_COMPLETED payload to carry the outcome field.
- 3 existing tests updated from asserting `on_progress=pipeline._on_download_progress` to `on_progress=ANY` since the closure replaces the direct method reference.

## Self-Check

PASSED — all verification criteria met:

- `uv run pytest tests/test_pipeline.py -v` exits 0 (102 passed)
- `uv run pytest --cov=components --cov-report=term-missing tests/test_pipeline.py` shows 100% on components/pipeline.py
- `uv run ruff check` exits 0
- All 7 PipelineEventType members emit calls present in pipeline.py
- `_Stores` declares episodes_done, episodes_failed, episodes_total
- All existing _Stores construction sites pass episodes_total=len(episodes)
- With event_bus=None, no emit is invoked (all guards check `if self._event_bus is not None`)
