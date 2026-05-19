---
status: complete
phase: 04-config-feed-management
source: [04-01-SUMMARY.md, 04-02-SUMMARY.md]
started: 2026-05-17T23:34:29Z
updated: 2026-05-19T00:00:00Z
---

## Current Test
<!-- OVERWRITE each test - shows where we are -->

number: 12
name: DELETE /api/v1/feeds/{slug} — remove the test feed
expected: |
  curl -X DELETE http://localhost:8080/api/v1/feeds/uat-test-feed
  Returns HTTP 204 No Content. GET /api/v1/feeds no longer includes "UAT Test Feed".
result: complete

## Tests

### 1. Cold Start Smoke Test
expected: |
  Kill any running server process. Start fresh:
    uv run python main.py --serve
  Server boots without errors. Then:
    curl http://localhost:8080/api/v1/health
  Returns HTTP 200 with JSON body containing uptime and version.
result: pass

### 2. GET /api/v1/settings — returns full config
expected: |
  curl http://localhost:8080/api/v1/settings
  Returns HTTP 200 with JSON containing all AppConfig fields (app, output,
  transcription, topic_detection, ad_detection, logging sections).
  A "credentials" key is present alongside the config sections.
result: pass

### 3. GET /api/v1/settings — credentials redacted
expected: |
  In the response from the previous test, the "credentials" section shows
  "set" or "not set" for each provider key (e.g. openai_api_key, deepgram_api_key).
  No actual API key values appear anywhere in the response.
result: pass

### 4. PATCH /api/v1/settings — update a config field
expected: |
  curl -X PATCH http://localhost:8080/api/v1/settings \
    -H "Content-Type: application/json" \
    -d '{"logging": {"level": "DEBUG"}}'
  Returns HTTP 200. A subsequent GET /api/v1/settings shows logging.level as "DEBUG".
  Revert after: PATCH with {"logging": {"level": "INFO"}}
result: pass
note: AppConfig field is "log" not "logging" — {"log": {"level": "DEBUG"}} works; {"logging": ...} correctly returns 422 (extra inputs not permitted)

### 5. PATCH /api/v1/settings — unknown key rejected
expected: |
  curl -X PATCH http://localhost:8080/api/v1/settings \
    -H "Content-Type: application/json" \
    -d '{"nonexistent_key": "value"}'
  Returns HTTP 422 with a validation error body. config.yaml is unchanged.
result: pass

### 6. GET /api/v1/feeds — lists feeds with episode counts
expected: |
  curl http://localhost:8080/api/v1/feeds
  Returns HTTP 200 with a JSON array. Each feed object contains:
  slug, title, url, enabled, episodes_to_keep, episode_count.
  episode_count is an integer (0 or more — sourced from the episodes DB table).
result: pass

### 7. POST /api/v1/feeds — add a new feed
expected: |
  curl -X POST http://localhost:8080/api/v1/feeds \
    -H "Content-Type: application/json" \
    -d '{"title": "UAT Test Feed", "url": "https://example.com/feed.rss"}'
  Returns HTTP 201 with the created feed object including its slug.
  GET /api/v1/feeds now includes "UAT Test Feed" in the list.
result: pass

### 8. POST /api/v1/feeds — duplicate title rejected
expected: |
  curl -X POST http://localhost:8080/api/v1/feeds \
    -H "Content-Type: application/json" \
    -d '{"title": "UAT Test Feed", "url": "https://example.com/other.rss"}'
  (same title as test 7)
  Returns HTTP 409 Conflict. The feed list is unchanged.
result: pass

### 9. PATCH /api/v1/feeds/{slug} — update a feed field
result: pass
expected: |
  Using the slug from test 7 (likely "uat-test-feed"):
  curl -X PATCH http://localhost:8080/api/v1/feeds/uat-test-feed \
    -H "Content-Type: application/json" \
    -d '{"enabled": false}'
  Returns HTTP 200. GET /api/v1/feeds shows enabled: false for that feed.

### 10. PATCH /api/v1/feeds/{slug} — title changes ignored
expected: |
  curl -X PATCH http://localhost:8080/api/v1/feeds/uat-test-feed \
    -H "Content-Type: application/json" \
    -d '{"title": "New Title Attempt", "url": "https://example.com/feed.rss"}'
  Returns HTTP 200. GET /api/v1/feeds shows the title is still "UAT Test Feed"
  (title field is silently ignored in PATCH).
result: pass

### 11. DELETE /api/v1/feeds/{slug} — unknown slug returns 404
expected: |
  curl -X DELETE http://localhost:8080/api/v1/feeds/no-such-feed
  Returns HTTP 404. Feed list is unchanged.
result: pass

### 12. DELETE /api/v1/feeds/{slug} — remove the test feed
expected: |
  curl -X DELETE http://localhost:8080/api/v1/feeds/uat-test-feed
  Returns HTTP 204 No Content. GET /api/v1/feeds no longer includes "UAT Test Feed".
result: pass

## Summary

total: 12
passed: 12
issues: 0
pending: 0
skipped: 0

## Gaps

[none yet]
