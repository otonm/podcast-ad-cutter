# Phase 1: TopicExtractor Retry Bug Fix - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Fix `TopicExtractor.extract()` to retry up to `max_retries` times when JSON parsing fails (JSONDecodeError or KeyError), mirroring the retry pattern already working in `AdDetector.detect()`. API-level failures are not retried. This phase touches only `components/topic_extractor.py` and ensures all 5 previously failing tests pass.

</domain>

<decisions>
## Implementation Decisions

### Method Structure
- **D-01:** Extract JSON parsing to a private `_parse_response(self, content: str, guid: str) -> TopicExtraction` method — raises `JSONDecodeError` or `KeyError` on failure. Mirrors AdDetector structurally; keeps the retry loop in `extract()` readable.

### Retry Loop
- **D-02:** The retry loop runs up to `max_retries` total attempts (not `max_retries` retries after the first). `max_retries=1` → 1 total call, 0 retries. Matches existing test: `test_extract_custom_max_retries`.
- **D-03:** On each parse failure (not the last attempt): append the bad response as an `assistant` message and `_RETRY_PROMPT` as a `user` message, then call `litellm.acompletion` again.
- **D-04:** Cost accumulates across all attempts — sum of `_compute_cost(response)` from every LLM call, including failed parse attempts.
- **D-05:** On the last attempt: raise `TopicExtractionError` with the guid in the message.

### Error Handling
- **D-06:** API failures on the initial call raise `TopicExtractionError` immediately — no retry.
- **D-07:** API failures on retry calls also raise `TopicExtractionError` immediately — unrecoverable. Matches AdDetector behaviour.

### Claude's Discretion
- Error message wording (tests only assert `guid in message`)
- Warning log message format on failed parse attempts

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Reference Implementation
- `components/ad_detector.py` — AdDetector.detect() contains the exact retry pattern to mirror; `_parse_response` method shows the helper structure

### Tests to Pass
- `tests/test_topic_extractor.py` — All 5 failing tests in the "Retry loop" section define required behaviour precisely; also contains tests for cost accumulation across retries

### Bug Description
- `.planning/REQUIREMENTS.md` §BUG-01 — Concise description of the bug and expected fix

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `TopicExtractor._compute_cost()` — already implemented, unchanged
- `TopicExtractor._build_messages()` — already implemented, used to construct initial messages
- `TopicExtractor._truncate_transcript()` — already implemented, runs before retry loop
- `_RETRY_PROMPT` constant — already defined in `components/topic_extractor.py`, ready to use

### Established Patterns
- AdDetector retry loop (lines ~175–220 in `ad_detector.py`): `for attempt in range(self._max_retries)` → try parse → on failure: append messages + retry acompletion → on last attempt: raise error
- `TopicExtractionError` already imported and used in `topic_extractor.py`

### Integration Points
- No external integration changes — this fix is self-contained within `components/topic_extractor.py`
- `Pipeline` calls `TopicExtractor.extract()` — signature and return type are unchanged

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches within the decisions captured above.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 01-topicextractor-retry-bug-fix*
*Context gathered: 2026-03-29*
