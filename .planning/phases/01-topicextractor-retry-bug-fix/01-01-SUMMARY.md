---
phase: 01-topicextractor-retry-bug-fix
plan: "01"
subsystem: pipeline
tags: [litellm, topic-extraction, retry-loop, json-parsing]

# Dependency graph
requires: []
provides:
  - "TopicExtractor with working retry loop mirroring AdDetector.detect()"
  - "_parse_response helper for JSON parsing with full type annotations"
affects: [02-pipeline-wiring]

# Tech tracking
tech-stack:
  added: []
  patterns: ["retry loop using range(max_retries) total-attempts semantics", "_parse_response helper extracted from inline parse block"]

key-files:
  created:
    - "components/topic_extractor.py"
    - "tests/test_topic_extractor.py"
  modified: []

key-decisions:
  - "Use try/except/else structure to satisfy ruff TRY300 (return in else block, not inside try)"
  - "Add pragma: no cover to structurally unreachable post-loop fallback (same pattern as AdDetector)"
  - "Added test for retry API failure to achieve 100% coverage on topic_extractor.py"

patterns-established:
  - "Retry loop pattern: range(max_retries) = total attempts; messages extended on each parse failure; cost accumulated across all attempts"
  - "TRY300 compliance: successful return placed in else block of try/except"

requirements-completed: [BUG-01]

# Metrics
duration: 2min
completed: 2026-03-29
---

# Phase 01 Plan 01: TopicExtractor Retry Bug Fix Summary

**Fixed TopicExtractor to retry on malformed JSON using range(max_retries) loop with _parse_response helper, mirroring AdDetector.detect() pattern**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-29T11:26:36Z
- **Completed:** 2026-03-29T11:28:36Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments
- Replaced immediate-raise parse block with retry loop that calls acompletion up to max_retries times
- Extracted `_parse_response()` helper with full type annotations and docstring (ruff ANN-compliant)
- Cost now accumulates across all LLM call attempts (total_cost += per retry)
- All 5 previously failing retry-loop tests now pass; total 30 tests (added 1 for retry API failure coverage)
- 100% coverage on components/topic_extractor.py

## Task Commits

Each task was committed atomically:

1. **Task 1: Add _parse_response helper and replace buggy parse block with retry loop** - `20b7ea1` (fix)

## Files Created/Modified
- `components/topic_extractor.py` - Added _parse_response helper; replaced parse-and-raise block with retry loop
- `tests/test_topic_extractor.py` - Added test_extract_api_failure_on_retry_raises_immediately for 100% coverage

## Decisions Made
- Used `try/except/else` structure to move the successful `return` into the `else` block, satisfying ruff TRY300 rule
- Added `# pragma: no cover` to the structurally unreachable post-loop fallback (same pattern already exists in AdDetector)
- Added one new test (`test_extract_api_failure_on_retry_raises_immediately`) to cover the retry API exception path — this was necessary to reach 100% coverage on the modified file per CLAUDE.md requirements

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added test for retry API failure coverage**
- **Found during:** Task 1 (after implementing retry loop)
- **Issue:** Lines 201-203 (retry API exception handler) were not covered by any existing test, giving topic_extractor.py 95% coverage — CLAUDE.md requires 100%
- **Fix:** Added `test_extract_api_failure_on_retry_raises_immediately` test that mocks first acompletion returning bad JSON, second raising RuntimeError
- **Files modified:** tests/test_topic_extractor.py
- **Verification:** `uv run pytest --cov=. --cov-report=term-missing` shows components/topic_extractor.py at 100%
- **Committed in:** 20b7ea1 (Task 1 commit)

**2. [Rule 1 - Bug] Applied TRY300 ruff fix (return in else block)**
- **Found during:** Task 1 (ruff check after initial implementation)
- **Issue:** ruff TRY300 flagged `return` inside `try` block; required moving to `else` block
- **Fix:** Restructured `try/except` to `try/except/else` with return in `else`
- **Files modified:** components/topic_extractor.py
- **Verification:** `uv run ruff check components/topic_extractor.py` passes cleanly
- **Committed in:** 20b7ea1 (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (1 missing test coverage, 1 ruff compliance)
**Impact on plan:** Both auto-fixes necessary for correctness and linting compliance. No scope creep.

## Issues Encountered
- None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- BUG-01 resolved; TopicExtractor retry behaviour now matches AdDetector
- Ready for Plan 02 (pipeline wiring)

---
*Phase: 01-topicextractor-retry-bug-fix*
*Completed: 2026-03-29*
