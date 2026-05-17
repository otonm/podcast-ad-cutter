---
status: partial
phase: 03-pipeline-control
source: [03-01-SUMMARY.md, 03-02-SUMMARY.md, 03-03-SUMMARY.md]
started: 2026-05-16T22:30:00Z
updated: 2026-05-16T22:30:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: Kill any running server. Start fresh with `uv run python main.py`. Server boots without errors, and `curl http://localhost:8080/api/v1/status` returns a 200 with JSON (state: idle).
result: pass

### 2. GET /api/v1/status — idle response shape
expected: With server running and no pipeline active, `curl http://localhost:8080/api/v1/status` returns `{"state":"idle","started_at":null,"active_feed_slug":null,"current_episode_guid":null,"feeds":{}}`.
result: pass

### 3. POST /api/v1/run — starts pipeline (202)
expected: `curl -X POST http://localhost:8080/api/v1/run` returns HTTP 202. Immediately after, GET /api/v1/status shows `state: running` with a non-null `started_at` and `feeds` map populated.
result: pass

### 4. POST /api/v1/run — 409 when already running
expected: While the pipeline is active (state=running), `curl -X POST http://localhost:8080/api/v1/run` returns HTTP 409 (Conflict). The pipeline keeps running.
result: pass

### 5. POST /api/v1/run/stop — graceful stop
expected: While the pipeline is active, `curl -X POST http://localhost:8080/api/v1/run/stop` returns 200. Shortly after, GET /api/v1/status shows `state: idle` again.
result: issue
reported: "works in principle, the process however runs almost completely to the end, which defeats the purpose of the stop command. the current episode should finish processing and then the whole process should stop."
severity: major

### 6. POST /api/v1/feeds/{slug}/run — feed-specific start (202)
expected: With a configured feed whose slug is e.g. `my-podcast`, `curl -X POST http://localhost:8080/api/v1/feeds/my-podcast/run` returns 202. GET /api/v1/status shows the pipeline running with `active_feed_slug` matching that feed.
result: pass

### 7. POST /api/v1/feeds/{slug}/run — 404 on unknown slug
expected: `curl -X POST http://localhost:8080/api/v1/feeds/does-not-exist/run` returns HTTP 404. No pipeline starts.
result: pass

### 8. POST /api/v1/episodes/{guid}/skip — mark episode skipped
expected: With pipeline idle and a known episode GUID in the DB, `curl -X POST http://localhost:8080/api/v1/episodes/{guid}/skip` returns 200. Re-running the pipeline should skip that episode (no events emitted for it).
result: blocked
blocked_by: prior-phase
reason: "impossible to test without the guid — no endpoint to list episodes/GUIDs yet"

### 9. POST /api/v1/episodes/{guid}/skip — 409 when pipeline active
expected: While the pipeline is running, `curl -X POST http://localhost:8080/api/v1/episodes/{guid}/skip` returns HTTP 409. The pipeline continues uninterrupted.
result: blocked
blocked_by: prior-phase
reason: "impossible to test without the guid — no endpoint to list episodes/GUIDs yet"

### 10. POST /api/v1/episodes/{guid}/reprocess — reset episode to stage
expected: With pipeline idle and a processed episode GUID, `curl -X POST 'http://localhost:8080/api/v1/episodes/{guid}/reprocess?stage=transcribe'` returns 200. The episode's downstream DB records (transcriptions, topic_extractions, ad_segments, ad_detection_runs) are deleted; the episode can now be re-processed from transcribe onward.
result: blocked
blocked_by: prior-phase
reason: "impossible to test without the guid — no endpoint to list episodes/GUIDs yet"

### 11. POST /api/v1/episodes/{guid}/reprocess — 422 on invalid stage
expected: `curl -X POST 'http://localhost:8080/api/v1/episodes/{guid}/reprocess?stage=invalid'` returns HTTP 422. No DB changes occur.
result: blocked
blocked_by: prior-phase
reason: "impossible to test without the guid — no endpoint to list episodes/GUIDs yet"

## Summary

total: 11
passed: 6
issues: 1
pending: 0
blocked: 4
skipped: 0
blocked: 0

## Gaps

- truth: "Graceful stop finishes current episode then halts — subsequent episodes are not processed"
  status: failed
  reason: "User reported: works in principle, the process however runs almost completely to the end, which defeats the purpose of the stop command. the current episode should finish processing and then the whole process should stop."
  severity: major
  test: 5
  root_cause: ""
  artifacts: []
  missing: []
  debug_session: ""
