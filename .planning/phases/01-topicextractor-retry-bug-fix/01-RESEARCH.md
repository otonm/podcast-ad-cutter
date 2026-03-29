# Phase 1: TopicExtractor Retry Bug Fix - Research

**Researched:** 2026-03-29
**Domain:** Python async LLM retry loop — internal refactor only
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Extract JSON parsing to a private `_parse_response(self, content: str, guid: str) -> TopicExtraction` method — raises `JSONDecodeError` or `KeyError` on failure. Mirrors AdDetector structurally; keeps the retry loop in `extract()` readable.
- **D-02:** The retry loop runs up to `max_retries` total attempts (not `max_retries` retries after the first). `max_retries=1` → 1 total call, 0 retries. Matches existing test: `test_extract_custom_max_retries`.
- **D-03:** On each parse failure (not the last attempt): append the bad response as an `assistant` message and `_RETRY_PROMPT` as a `user` message, then call `litellm.acompletion` again.
- **D-04:** Cost accumulates across all attempts — sum of `_compute_cost(response)` from every LLM call, including failed parse attempts.
- **D-05:** On the last attempt: raise `TopicExtractionError` with the guid in the message.
- **D-06:** API failures on the initial call raise `TopicExtractionError` immediately — no retry.
- **D-07:** API failures on retry calls also raise `TopicExtractionError` immediately — unrecoverable. Matches AdDetector behaviour.

### Claude's Discretion

- Error message wording (tests only assert `guid in message`)
- Warning log message format on failed parse attempts

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BUG-01 | TopicExtractor retries on malformed JSON — implementation raises immediately instead of appending retry prompt and calling LLM again (5 failing tests in test_topic_extractor.py) | Direct code inspection confirms bug location at `topic_extractor.py:164-177`; AdDetector reference implementation provides the exact pattern to port |
| TEST-03 | All existing tests remain green; 100% coverage maintained; ruff clean | Test suite confirmed: 24 passing, 5 failing; `uv run pytest --cov=.` and `uv run ruff` are the verification commands |
</phase_requirements>

---

## Summary

Phase 1 is a targeted internal bug fix with zero external dependencies. `TopicExtractor.extract()` currently raises `TopicExtractionError` immediately on the first JSON parse failure (lines 164–177 of `components/topic_extractor.py`). The correct behaviour — retry with correction messages up to `max_retries` times — is already fully implemented in `AdDetector.detect()` (lines 205–243 of `components/ad_detector.py`). The fix is a structural port of that pattern.

All five failing tests are in the "Retry loop" section of `tests/test_topic_extractor.py` (lines 370–474) and precisely define the required behaviour: retry on `JSONDecodeError` or `KeyError`, accumulate cost across attempts, append assistant + user correction messages on each failed attempt, raise only after all attempts exhausted. The 24 currently passing tests must remain green.

The only file that changes is `components/topic_extractor.py`. No schema changes, no new dependencies, no database or integration changes.

**Primary recommendation:** Port `AdDetector.detect()` retry loop verbatim into `TopicExtractor.extract()`, extracting the JSON construction logic into a `_parse_response()` helper as decided in D-01.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| litellm | >=1.82.6 (already installed) | Async LLM calls | Already used throughout the codebase |
| pytest-asyncio | >=0.24 (already installed) | Async test execution | `asyncio_mode = "auto"` in pyproject.toml — no marker needed |

No new dependencies required. This phase introduces no new libraries.

**Installation:** None needed.

---

## Architecture Patterns

### Recommended Project Structure

No structural changes. The single file to modify is:

```
components/
└── topic_extractor.py   # Only file modified
```

### Pattern: Retry Loop with Cost Accumulation (from AdDetector)

**What:** Initial LLM call outside the retry loop; retry loop iterates `range(self._max_retries)`; parse attempt on every iteration; on parse failure append messages + call again; on last iteration raise error.

**When to use:** Any LLM component where malformed JSON is possible and retry improves reliability.

**Canonical reference:** `components/ad_detector.py` lines 190–243.

```python
# Source: components/ad_detector.py (lines 190-243) — exact pattern to mirror
# 1. Initial API call (before loop) — API failures raise immediately
try:
    response = await litellm.acompletion(...)
except Exception as exc:
    raise TopicExtractionError(...) from exc

total_cost = self._compute_cost(response)
content = response.choices[0].message.content

# 2. Retry loop — max_retries total attempts
for attempt in range(self._max_retries):
    try:
        result = self._parse_response(content, guid)
        cost_record = TopicExtractionCost(...)
        return (guid, result, cost_record)
    except (json.JSONDecodeError, KeyError) as exc:
        if attempt == self._max_retries - 1:
            raise TopicExtractionError(f"... {guid} ...") from exc
        # Not last attempt — append messages and retry
        logger.warning(f"... {guid} ... attempt {attempt + 1} ...")
        messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": _RETRY_PROMPT},
        ]
        try:
            response = await litellm.acompletion(...)
        except Exception as retry_exc:
            raise TopicExtractionError(...) from retry_exc
        total_cost += self._compute_cost(response)
        content = response.choices[0].message.content
```

### Pattern: _parse_response Helper

**What:** Private method that parses content string into the domain object, raising `json.JSONDecodeError` or `KeyError` on bad input. No side effects; no logging.

**Signature (decided, D-01):**
```python
def _parse_response(self, content: str, guid: str) -> TopicExtraction:
    data = json.loads(content)          # raises JSONDecodeError
    return TopicExtraction(
        guid=guid,
        podcast=...,                    # needs podcast + title from outer scope
        title=...,
        topic=data["topic"],            # raises KeyError if missing
        hosts=data["hosts"],
        show=data["show"],
    )
```

**Note on signature:** `TopicExtraction` requires `guid`, `podcast`, and `title`, which are not in `content`. The helper must accept them as parameters. Recommended signature: `_parse_response(self, content: str, guid: str, podcast: str, title: str) -> TopicExtraction`. This differs slightly from AdDetector's `_parse_response(self, content, guid)` because AdDetector constructs `AdSegmentDetection` objects that only need data from the JSON. The planner should decide the exact signature.

### Anti-Patterns to Avoid

- **Inverting the loop logic:** Catching exceptions outside the loop and checking attempt count separately creates off-by-one errors. Keep parse + check + retry all inside the single `for` block.
- **Resetting total_cost:** `total_cost` is initialised from the first response before the loop; only add to it inside the loop. Do not reassign.
- **Using `max_retries` retries after the first call:** D-02 is explicit: `max_retries` is total attempts. `range(self._max_retries)` gives indices 0..max_retries-1; the last attempt is `attempt == self._max_retries - 1`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry with correction messages | Custom retry decorator | Direct loop in `extract()` | The retry prompt is conversation-stateful (appends messages); standard retry decorators do not thread message state |
| Cost accumulation | Separate cost tracker | `total_cost += self._compute_cost(response)` | Already works in AdDetector; same pattern |

---

## Common Pitfalls

### Pitfall 1: _parse_response Needs More Parameters Than AdDetector's

**What goes wrong:** Copying AdDetector's `_parse_response(self, content, guid)` signature verbatim fails because `TopicExtraction` also requires `podcast` and `title` (not in the JSON content).

**Why it happens:** `AdSegmentDetection` is constructed entirely from JSON data. `TopicExtraction` needs episode metadata from the `extract()` call arguments.

**How to avoid:** Either (a) extend the signature to `_parse_response(self, content, guid, podcast, title)`, or (b) construct `TopicExtraction` in the loop body after calling a narrower helper. Option (a) is simpler.

**Warning signs:** `TypeError: _parse_response() missing required arguments` at runtime.

### Pitfall 2: The Existing parse-and-raise Block Still Present

**What goes wrong:** If the old `try/except (JSONDecodeError, KeyError)` block at lines 164–177 is left in place after adding the retry loop, the error is raised before the loop can retry.

**Why it happens:** The current bug is that very block. The entire block must be removed and replaced by the retry loop.

**How to avoid:** Delete lines 163–186 (from `content = response.choices[0].message.content` through `return (guid, extraction, cost_record)`) and replace with the retry loop structure.

### Pitfall 3: cost_record Created Before Loop Exits

**What goes wrong:** Building `TopicExtractionCost` before the loop means it captures `total_cost` before retry costs are added.

**Why it happens:** The original code computed cost after a single attempt. In the retry loop, `total_cost` is the running sum; `cost_record` must be constructed at the return statement inside the loop, not before it.

**How to avoid:** Create `cost_record` inside the loop's `try` block at the successful return point (mirrors AdDetector lines 208–213).

### Pitfall 4: ruff ANN / D violations

**What goes wrong:** Adding a new private method without a docstring or type annotations triggers ruff errors.

**Why it happens:** `pyproject.toml` selects `ALL` ruff rules; only `D104`, `D107`, `D203`, `D213` are excluded. `ANN` rules are only excluded for `tests/**`.

**How to avoid:** Add a one-line docstring to `_parse_response` and full type annotations on parameters and return type.

---

## Code Examples

### Current Buggy Extract (what to replace)

```python
# Source: components/topic_extractor.py lines 162-187 — THE BUG
content = response.choices[0].message.content

try:
    data = json.loads(content)
    extraction = TopicExtraction(
        guid=guid,
        podcast=podcast,
        title=title,
        topic=data["topic"],
        hosts=data["hosts"],
        show=data["show"],
    )
except (json.JSONDecodeError, KeyError) as exc:
    msg = f"Failed to parse LLM response for '{guid}': {exc!r} — content: {content!r}"
    logger.error(msg)
    raise TopicExtractionError(msg) from exc  # <-- raises immediately, no retry

cost_value = self._compute_cost(response)
cost_record = TopicExtractionCost(...)
logger.debug(...)
return (guid, extraction, cost_record)
```

### Reference Retry Loop (from AdDetector — translate for TopicExtractor)

```python
# Source: components/ad_detector.py lines 202-243
total_cost = self._compute_cost(response)
content = response.choices[0].message.content

for attempt in range(self._max_retries):
    try:
        detections = self._parse_response(content, guid)
        cost_record = AdDetectionCost(
            provider=self._provider,
            model=self._model,
            cost=total_cost,
        )
        logger.debug(f"Detected {len(detections)} ad segment(s) for '{guid}'")
        return (guid, detections, cost_record)
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        if attempt == self._max_retries - 1:
            msg = f"Failed to parse LLM response for '{guid}' after {self._max_retries} attempts: {exc!r}"
            logger.error(msg)
            raise AdDetectionError(msg) from exc

        logger.warning(
            f"Ad detection response parse failed for '{guid}' (attempt {attempt + 1}): {exc!r}"
        )
        messages = [
            *messages,
            {"role": "assistant", "content": content},
            {"role": "user", "content": _RETRY_PROMPT},
        ]
        try:
            response = await litellm.acompletion(
                model=self._model_id,
                messages=messages,
                response_format={"type": "json_object"},
                api_key=self._api_key,
            )
        except Exception as retry_exc:
            msg = f"litellm.acompletion failed for '{guid}' on retry: {retry_exc}"
            raise AdDetectionError(msg) from retry_exc
        total_cost += self._compute_cost(response)
        content = response.choices[0].message.content

msg = f"Ad detection failed for '{guid}': exhausted retries"
raise AdDetectionError(msg)
```

### 5 Failing Tests — What They Assert

```python
# test_extract_retries_on_malformed_json_then_succeeds
# Expects: 2 acompletion calls; cost = 0.001 + 0.002 = 0.003
assert mock_call.await_count == 2
assert cost.cost == pytest.approx(0.003)

# test_extract_retries_on_missing_keys_then_succeeds
# Expects: 2 calls; extraction.hosts correct from 2nd response
assert mock_call.await_count == 2
assert extraction.hosts == "Alice, Bob"
assert cost.cost == pytest.approx(0.003)

# test_extract_retry_appends_correction_messages
# Expects: 2nd call messages have 1 assistant + 2 user roles
roles = [m["role"] for m in retry_msgs]
assert roles.count("assistant") == 1
assert roles.count("user") == 2
assistant_msg = next(m for m in retry_msgs if m["role"] == "assistant")
assert assistant_msg["content"] == bad_content  # exact bad response

# test_extract_raises_after_max_retries_exhausted (default max_retries=3)
# Expects: 3 calls total, then TopicExtractionError with guid
assert mock_call.await_count == 3
assert "ep-1" in exc_info.value.message

# test_extract_cost_accumulates_across_retries
# Expects: 0.001 + 0.001 + 0.005 = 0.007
assert cost.cost == pytest.approx(0.007)
```

---

## Environment Availability

Step 2.6: SKIPPED (no external dependencies — purely an internal code change to one Python file).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 0.24 |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/test_topic_extractor.py -v` |
| Full suite command | `uv run pytest --cov=.` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BUG-01 | Retry on malformed JSON, succeed on 2nd attempt | unit | `uv run pytest tests/test_topic_extractor.py::test_extract_retries_on_malformed_json_then_succeeds -x` | Yes |
| BUG-01 | Retry on missing keys, succeed on 2nd attempt | unit | `uv run pytest tests/test_topic_extractor.py::test_extract_retries_on_missing_keys_then_succeeds -x` | Yes |
| BUG-01 | Retry appends assistant + correction user messages | unit | `uv run pytest tests/test_topic_extractor.py::test_extract_retry_appends_correction_messages -x` | Yes |
| BUG-01 | Raises TopicExtractionError after all retries exhausted | unit | `uv run pytest tests/test_topic_extractor.py::test_extract_raises_after_max_retries_exhausted -x` | Yes |
| BUG-01 | Cost accumulates across all retry attempts | unit | `uv run pytest tests/test_topic_extractor.py::test_extract_cost_accumulates_across_retries -x` | Yes |
| TEST-03 | All 24 previously passing tests remain green | unit | `uv run pytest tests/test_topic_extractor.py -v` | Yes |
| TEST-03 | 100% coverage | coverage | `uv run pytest --cov=.` | Yes |
| TEST-03 | No ruff errors | lint | `uv run ruff` | Yes |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_topic_extractor.py -v`
- **Per wave merge:** `uv run pytest --cov=. && uv run ruff`
- **Phase gate:** Full suite green + ruff clean before `/gsd:verify-work`

### Wave 0 Gaps

None — existing test infrastructure covers all phase requirements. All 5 failing tests already exist in `tests/test_topic_extractor.py`. No new test files needed.

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on This Phase |
|-----------|---------------------|
| Always use Context7 for library/API docs before implementing | No new library APIs needed; litellm usage is already established in AdDetector |
| Write tests before implementation (TDD) | Tests already exist and are failing — implementation follows tests |
| `uv run pytest` must pass after every change | Verification step after each task |
| `uv run pytest --cov=.` — 100% coverage required | Phase gate check |
| `uv run ruff` — no errors | Phase gate check; new `_parse_response` method needs docstring + annotations |
| Python 3.12 target | `type` alias syntax already used in file; no impact |
| Async throughout | `extract()` is already `async`; retry calls use `await litellm.acompletion` |
| Context managers for every resource | Not applicable — no file/network handles opened directly |
| f-strings for logging, never `%` operator | Warning log in retry loop must use f-string |

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| Raise on first parse failure (current bug) | Retry loop up to max_retries | 5 failing tests become passing |

No deprecated patterns relevant to this phase.

---

## Open Questions

1. **`_parse_response` signature — `podcast` and `title` parameters**
   - What we know: AdDetector's `_parse_response(self, content, guid)` only needs `guid` because detection objects are fully from JSON. `TopicExtraction` requires `podcast` and `title` from method arguments.
   - What's unclear: Whether to extend to `_parse_response(self, content, guid, podcast, title)` or construct `TopicExtraction` inline in the loop.
   - Recommendation: `_parse_response(self, content: str, guid: str, podcast: str, title: str) -> TopicExtraction` — cleanest, mirrors D-01 intent, keeps the loop body readable. Confirm in PLAN.md.

---

## Sources

### Primary (HIGH confidence)

- Direct source code reading: `components/topic_extractor.py` — current buggy implementation confirmed
- Direct source code reading: `components/ad_detector.py` lines 190–243 — canonical retry pattern
- Direct test execution: `uv run pytest tests/test_topic_extractor.py` — confirmed 5 failing, 24 passing
- Direct source reading: `tests/test_topic_extractor.py` lines 370–474 — exact assertions required

### Secondary (MEDIUM confidence)

- `pyproject.toml` — ruff rules, pytest config, dependency versions confirmed

---

## Metadata

**Confidence breakdown:**
- Bug location: HIGH — confirmed by test run and code inspection
- Fix pattern: HIGH — AdDetector reference implementation is exact model to port
- Test assertions: HIGH — read directly from test file
- Ruff compliance: HIGH — pyproject.toml rules read directly

**Research date:** 2026-03-29
**Valid until:** Not time-sensitive — all findings are from local source files
