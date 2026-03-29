---
phase: 01-topicextractor-retry-bug-fix
verified: 2026-03-29T12:00:00Z
status: passed
score: 10/10 must-haves verified
re_verification: false
---

# Phase 1: TopicExtractor Retry Bug Fix — Verification Report

**Phase Goal:** Fix the TopicExtractor retry bug so that parse failures on LLM responses trigger retries up to max_retries instead of raising immediately.
**Verified:** 2026-03-29
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | TopicExtractor.extract() calls litellm.acompletion a second time when the first response is unparseable JSON | VERIFIED | `test_extract_retries_on_malformed_json_then_succeeds` passes; `mock_call.await_count == 2` assertion green |
| 2 | TopicExtractor.extract() calls litellm.acompletion a second time when the first response is valid JSON missing required keys | VERIFIED | `test_extract_retries_on_missing_keys_then_succeeds` passes; `mock_call.await_count == 2` assertion green |
| 3 | On each retry the messages list is extended with the bad assistant response and _RETRY_PROMPT as a user turn | VERIFIED | `test_extract_retry_appends_correction_messages` passes; asserts `roles.count("assistant") == 1`, `roles.count("user") == 2`, `assistant_msg["content"] == bad_content` |
| 4 | Cost accumulates across all LLM call attempts (sum of _compute_cost per call) | VERIFIED | `test_extract_cost_accumulates_across_retries` passes; `cost.cost == pytest.approx(0.007)` (sum of 0.001 + 0.001 + 0.005) confirmed |
| 5 | TopicExtractionError is raised only after all max_retries attempts are exhausted, with guid in the message | VERIFIED | `test_extract_raises_after_max_retries_exhausted` passes; `mock_call.await_count == 3` and `"ep-1" in exc_info.value.message` confirmed |
| 6 | API-level failures raise TopicExtractionError immediately without retrying | VERIFIED | `test_extract_api_failure_does_not_retry` passes; `mock_call.await_count == 1` confirmed. API failure on retry also raises immediately: `test_extract_api_failure_on_retry_raises_immediately` passes |
| 7 | All 5 previously failing tests in test_topic_extractor.py pass | VERIFIED | `uv run pytest tests/test_topic_extractor.py -v` exits 0 with 30 passed (original 5 retry tests + 25 other tests) |
| 8 | All tests across the entire test suite pass | VERIFIED | `uv run pytest --cov=.` exits 0 with 564 passed, 0 failed |
| 9 | Test coverage is 100% | VERIFIED | TOTAL line shows 100%; components/topic_extractor.py 100% (93 stmts, 0 miss) |
| 10 | ruff reports no errors or warnings | VERIFIED | `uv run ruff check .` exits 0: "All checks passed!" |

**Score:** 10/10 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `components/topic_extractor.py` | Fixed TopicExtractor with retry loop and `_parse_response` helper | VERIFIED | File exists, 217 lines, fully substantive; wired into test suite |

**Artifact — Level 1 (Exists):** File present at `components/topic_extractor.py`

**Artifact — Level 2 (Substantive):**
- `def _parse_response(self, content: str, guid: str, podcast: str, title: str) -> TopicExtraction:` present at line 101
- Docstring present: `"""Parse an LLM JSON response into a TopicExtraction; raises JSONDecodeError or KeyError on failure."""`
- `for attempt in range(self._max_retries):` present at line 177
- No bare `raise TopicExtractionError` outside retry loop (line 172 is the initial API failure path; line 184 is inside the loop's last-attempt guard; line 216 is the unreachable post-loop fallback)
- All f-strings in log calls (no `%` operator)

**Artifact — Level 3 (Wired):**
- Imported and exercised by 30 tests in `tests/test_topic_extractor.py`
- `_parse_response` called at line 179 inside the retry loop
- `litellm.acompletion` called at line 163 (initial call) and line 195 (retry call inside except branch)

**Artifact — Level 4 (Data Flow):** Not applicable — TopicExtractor is a service class, not a renderer. Data flows through function return values, which the tests directly assert.

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `components/topic_extractor.py:extract()` | `_parse_response()` | Called inside `for attempt in range(self._max_retries)` loop | VERIFIED | Pattern `_parse_response(content` found at line 179, inside the for loop body |
| `components/topic_extractor.py:extract()` | `litellm.acompletion` | Retry call inside except block | VERIFIED | Two `await litellm.acompletion` calls found: line 163 (initial) and line 195 (inside the except branch of the retry loop) |
| `pyproject.toml` | `uv run pytest --cov=.` | pytest configuration and coverage settings | VERIFIED | `[tool.pytest.ini_options]` section present at line 52 of pyproject.toml |

---

### Data-Flow Trace (Level 4)

Not applicable for this phase. The change is to a service method (`extract()`) that returns values directly to callers. No rendering pipeline or dynamic UI to trace.

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All 30 topic extractor tests pass | `uv run pytest tests/test_topic_extractor.py -v` | 30 passed in 1.43s | PASS |
| Full suite 564 tests pass | `uv run pytest --cov=.` | 564 passed in 4.06s | PASS |
| Coverage is 100% | `uv run pytest --cov=.` TOTAL line | TOTAL 5728 stmts, 0 miss, 100% | PASS |
| Ruff exits clean | `uv run ruff check .` | "All checks passed!" exit 0 | PASS |
| `_parse_response` correct signature | grep on topic_extractor.py | Found at line 101 with full type annotations and return type | PASS |
| `range(self._max_retries)` loop present | grep on topic_extractor.py | Found at line 177 | PASS |
| Cost accumulation wired | grep `total_cost +=` | Found at line 204 inside retry except branch | PASS |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| BUG-01 | 01-01-PLAN.md | TopicExtractor retries on malformed JSON — raises immediately instead of retrying up to max_retries | SATISFIED | Retry loop at line 177 using `range(self._max_retries)`; 5 previously failing retry tests now pass; `test_extract_raises_after_max_retries_exhausted` confirms 3 calls before error |
| TEST-03 | 01-02-PLAN.md | All existing tests remain green; 100% coverage maintained; ruff clean | SATISFIED | 564 tests pass, TOTAL 100% coverage, ruff exits 0 with no errors |

Both requirements assigned to Phase 1 are accounted for. No orphaned requirements found — REQUIREMENTS.md traceability table shows both BUG-01 and TEST-03 mapped to Phase 1 and marked Complete.

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `components/topic_extractor.py` | 215-216 | `# pragma: no cover` on post-loop fallback | Info | Intentional — structurally unreachable due to the `range(self._max_retries)` + last-attempt guard on line 181 raising before loop exit. Same pattern established in `components/ad_detector.py`. Not a stub — the preceding loop always either returns or raises. |

No blocker or warning anti-patterns found.

---

### Human Verification Required

None. All behaviors are fully verifiable through the test suite and static analysis. The fix is internal logic — no UI, no external service integration, no visual output.

---

### Gaps Summary

No gaps. All must-haves from both plans are satisfied:

- Plan 01-01 (BUG-01): `_parse_response` helper exists with correct signature and docstring; retry loop uses `range(self._max_retries)` total-attempts semantics; messages correctly extended on parse failure; cost accumulated with `total_cost +=`; `TopicExtractionError` raised with guid in message after exhausting attempts; API failures raise immediately on both initial and retry calls.

- Plan 01-02 (TEST-03): 564 tests pass with 0 failures; TOTAL coverage is 100%; ruff reports no errors or warnings.

Phase goal fully achieved.

---

_Verified: 2026-03-29_
_Verifier: Claude (gsd-verifier)_
