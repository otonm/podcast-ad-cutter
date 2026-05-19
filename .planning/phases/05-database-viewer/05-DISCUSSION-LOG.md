# Phase 5: Database Viewer - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-19
**Phase:** 05-database-viewer
**Areas discussed:** Cost tracking gap, Episode pipeline state, Episode response fields, Pagination defaults, Transcription response shape, Ad segments response shape, Cost endpoint grouping, DB-01 ordering

---

## Cost Tracking Gap

| Option | Description | Selected |
|--------|-------------|----------|
| Add guid via migration | ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid). Existing rows get NULL. Pipeline save_cost() passes GUID going forward. | ✓ |
| Scope DB-04 to per-model totals only | No schema change. No per-episode breakdown. | |

**User's choice:** Add guid via migration
**Notes:** Enables per-episode cost breakdown as required by DB-04. Existing NULL rows are excluded from by_episode section but included in totals.

---

## Episode Pipeline State

| Option | Description | Selected |
|--------|-------------|----------|
| Derive via JOIN | Single SQL query with LEFT JOINs. Python maps presence to state string. | ✓ |
| Five sub-queries per episode | O(n) queries for n episodes. | |
| You decide | Leave to planner/executor. | |

**User's choice:** Derive via JOIN

---

## What "complete" means for pipeline state

| Option | Description | Selected |
|--------|-------------|----------|
| Has ad_detection_run row | Last DB-writing stage. | |
| Has ad_segments (ads found) | Only episodes with detected ads. Confusing for ad-free episodes. | |
| Output file present on filesystem | User's actual intent for "complete". | ✓ |

**User's choice:** Output file present on filesystem
**Notes:** User also introduced "processed" as a distinct state meaning ad detection ran (regardless of output file). State ladder: skipped → complete → processed → transcribed → downloaded → pending.

---

## Episode Response Fields

| Option | Description | Selected |
|--------|-------------|----------|
| Minimal set | guid, feed_slug, title, pubdate, pipeline_state, skipped. | |
| Full row | All ~20 episodes columns plus derived pipeline_state. | ✓ |
| You decide | Leave to planner. | |

**User's choice:** Full row

---

## Timestamps

| Option | Description | Selected |
|--------|-------------|----------|
| Just pubdate | Return pubdate as-is. No pipeline timestamps in DB. | ✓ |
| Add processed_at to ad_detection_runs | Schema migration for processing timestamp. | |

**User's choice:** Just pubdate

---

## Pagination Defaults

| Option | Description | Selected |
|--------|-------------|----------|
| limit=50, offset=0, max=200 | Standard API conventions with hard cap. | ✓ |
| limit=100, no max | Larger default, no ceiling. | |
| You decide | Leave to planner. | |

**User's choice:** limit=50, offset=0, max=200

---

## Transcription Response Shape

| Option | Description | Selected |
|--------|-------------|----------|
| {guid, text, segments: [{start, end, text}...]} | Full transcription with timing segments. | ✓ |
| {guid, text} only | No segments. | |
| You decide | Leave to planner. | |

**User's choice:** {guid, text, segments: [{start, end, text}...]}

---

## Ad Segments Response Shape

| Option | Description | Selected |
|--------|-------------|----------|
| {guid, detected, segments: [{start_ms, end_ms, confidence, sponsor, ad_topic}...]} | Clear detected flag, useful fields only, indices omitted. | ✓ |
| Full row dump including indices | Exposes internal indices column with no UI use. | |

**User's choice:** {guid, detected, segments: [...]} — indices omitted

---

## Cost Endpoint Grouping

| Option | Description | Selected |
|--------|-------------|----------|
| {total, by_model, by_episode} | Grand total + per-model + per-episode breakdown. | ✓ |
| {total, by_episode} only | Simpler but loses model cost visibility. | |

**User's choice:** {total, by_model, by_episode}

---

## DB-01 Ordering

| Option | Description | Selected |
|--------|-------------|----------|
| pubdate DESC | Newest episodes first. Nulls sort last. | ✓ |
| id DESC | Insertion order. More stable but less intuitive. | |

**User's choice:** pubdate DESC (nulls last)

---

## Claude's Discretion

- Route file: `api/routes/db.py` with `create_db_router(db_path, output_dir)` factory
- Whether pipeline_state JOIN uses a single complex LEFT JOIN or CTE
- SQLite NULL-last syntax for pubdate ordering

## Deferred Ideas

- **DB-05 topics endpoint** — `GET /api/v1/db/topics/{guid}` — deferred to v2
- **Episode ordering control** — `?sort=pubdate|id` query param — not requested
