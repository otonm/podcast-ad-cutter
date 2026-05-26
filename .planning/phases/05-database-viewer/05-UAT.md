---
status: complete
phase: 05-database-viewer
source: [05-01-SUMMARY.md, 05-02-SUMMARY.md]
started: 2026-05-22T00:00:00Z
updated: 2026-05-22T00:10:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Server boots without errors. GET /api/v1/db/episodes returns a valid JSON array (possibly empty).
result: pass

### 2. List Episodes
expected: GET /api/v1/db/episodes returns a JSON array where each item has guid, title, podcast, pubdate, feed_slug, pipeline_state (not pipeline_state_db). Results ordered most-recent first (NULLs last).
result: pass

### 3. Filter Episodes by Feed Slug
expected: GET /api/v1/db/episodes?feed={slug} returns only episodes matching that slug. Unknown slug returns empty array (not 404).
result: pass

### 4. Episode Pagination
expected: limit=2 returns 2 rows; limit=0 and limit=201 return 400; offset=-1 returns 400; limit=abc returns 400.
result: pass

### 5. Get Transcription for Episode
expected: GET /api/v1/db/transcriptions/{guid} returns {"guid","text","segments":[{"start","end","text"},...]} for a known guid. 404 for unknown guid.
result: pass

### 6. Get Ad Detections for Episode
expected: GET /api/v1/db/ads/{guid} returns {"guid","detected":true,"segments":[{start_ms,end_ms,confidence,sponsor,ad_topic}]} for a known guid. No "indices" field. 404 for unknown guid.
result: pass

### 7. Get LLM Cost Aggregates
expected: GET /api/v1/db/costs returns {"total":float,"by_model":[...],"by_episode":[...]}. by_episode excludes NULL-guid rows. Returns valid response even when no costs exist.
result: pass
reported: "500 Internal Server Error on fresh DB that had cost_tracking rows but no guid column (migration not yet applied). Fixed: serve() now opens Database before starting the HTTP server, applying all pending migrations at startup."
severity: major

### 8. Filter Costs by Feed
expected: GET /api/v1/db/costs?feed={slug} returns cost data scoped to that feed. Unknown slug returns {"total":0.0,"by_model":[],"by_episode":[]}.
result: pass

### 9. Cost Tracking Linked to Episodes (guid column)
expected: Pre-migration NULL-guid rows excluded from by_episode. by_model totals still include them.
result: pass

## Summary

total: 9
passed: 9
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

- truth: "GET /api/v1/db/costs returns valid JSON response"
  status: failed
  reason: "User reported: 500 Internal Server Error on fresh DB that has cost_tracking rows but no guid column (migration not yet applied). After manually triggering the migration via write Database, endpoint returns correctly."
  severity: major
  test: 7
  root_cause: "ReadOnlyDatabase never runs migrations. The guid column in cost_tracking is added by Database.__aenter__ (write-capable), which only runs when the pipeline processes an episode. On a DB with pre-pipeline cost rows but no guid column, ct.guid in the SELECT causes a 500."
  artifacts:
    - path: "database/connection.py"
      issue: "Migrations only in Database.__aenter__, not triggered at server startup"
    - path: "api/routes/db.py"
      issue: "costs handler queries ct.guid which may not exist"
  missing:
    - "Run migrations at app startup (e.g. open Database briefly in main.py before starting the HTTP server)"
  debug_session: ""
