# Phase 2: SSE Progress Stream - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-15
**Phase:** 2-SSE Progress Stream
**Areas discussed:** Stage signal timing, Event payload schema, Run-level counter delivery, SSE idle connect behavior

---

## Stage Signal Timing

### Q1: When should EPISODE_STAGE_CHANGED fire?

| Option | Description | Selected |
|--------|-------------|----------|
| Before work starts | Emit 'entering stage X' before component call — matches user mental model | |
| After DB write completes | Emit 'completed stage X' after storing result — more accurate but less real-time | |
| Both — entering and completed | Two events per stage: started + completed | ✓ |

**User's choice:** Both — entering and completed

### Q2: How to distinguish stage_started from stage_completed?

| Option | Description | Selected |
|--------|-------------|----------|
| status field in payload | Keep EPISODE_STAGE_CHANGED; add status: 'started'\|'completed' to payload | ✓ |
| Separate enum members | Add EPISODE_STAGE_STARTED alongside existing EPISODE_STAGE_CHANGED | |

**User's choice:** status field in payload (Recommended)

---

## Event Payload Schema

### Q1: What fields should EPISODE_STAGE_CHANGED carry?

| Option | Description | Selected |
|--------|-------------|----------|
| guid + stage + status + feed_slug | Includes feed context for grouping without lookup | ✓ |
| guid + stage + status only | Leaner, no feed context | |
| guid + stage + status + feed_slug + timestamp | Adds ISO timestamp for timing/debugging | |

**User's choice:** guid + stage + status + feed_slug (Recommended)

### Q2: What should EPISODE_COMPLETED and EPISODE_FAILED carry?

| Option | Description | Selected |
|--------|-------------|----------|
| guid + feed_slug + outcome | Outcome: 'edited'\|'copied'\|'skipped'; error: short message for FAILED | ✓ |
| guid + feed_slug only | Minimal, no outcome or error info | |

**User's choice:** guid + feed_slug + outcome (Recommended)

### Q3: What should RUN_STARTED and RUN_COMPLETED carry?

| Option | Description | Selected |
|--------|-------------|----------|
| feed_slugs list + episode count | Total episode count upfront for progress bar | ✓ |
| Minimal — empty payload {} | Just signals start/end | |
| feed_slugs only, no episode count | No total count | |

**User's choice:** feed_slugs list + episode count (Recommended)

---

## Run-Level Counter Delivery

### Q1: How should run-level counters reach the client?

| Option | Description | Selected |
|--------|-------------|----------|
| Embed in EPISODE_COMPLETED / EPISODE_FAILED | feed_done/feed_failed/feed_total fields in outcome events | ✓ |
| Standalone RUN_COUNTERS event | New event type, new enum member | |
| Client computes from episode events | No counter fields, pushes logic to consumers | |

**User's choice:** Embed in EPISODE_COMPLETED / EPISODE_FAILED (Recommended)

### Q2: Where does Pipeline track the per-feed counters?

| Option | Description | Selected |
|--------|-------------|----------|
| In the existing _Stores dataclass | Add done/failed int fields to _Stores — zero new state object | ✓ |
| In a new dict on the Pipeline instance | self._counters: dict[str, dict] — explicit but redundant | |

**User's choice:** In the existing _Stores dataclass (Recommended)

---

## SSE Idle Connect Behavior

### Q1: What happens when a client connects while no pipeline run is active?

| Option | Description | Selected |
|--------|-------------|----------|
| Silent wait — no initial event | Client waits; Phase 3 status endpoint handles idle/running state | ✓ |
| Emit a server.idle event on connect | Immediate confirmation connection is live; new event type needed | |

**User's choice:** Silent wait — no initial event (Recommended)

---

## Claude's Discretion

- Exact Python field name for `episodes_total` on `_Stores` — consistent with `episodes_done` / `episodes_failed`
- Whether `_on_download_progress` / `_on_preprocess_progress` call `emit()` directly or via a shared helper
- The `feed_slug` lookup in progress callbacks — closure capture vs. passing down the call chain
- `DOWNLOAD_PROGRESS` and `ENCODE_PROGRESS` payload schema — defaulted to `{"guid": "...", "feed_slug": "...", "percent": 0.75}` (not explicitly discussed; follows the pattern from other events)

## Deferred Ideas

None — discussion stayed within phase scope.
