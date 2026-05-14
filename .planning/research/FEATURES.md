# Feature Landscape: REST + SSE API for Podcast Ad Cutter

**Domain:** Pipeline control and observation API for a local-network media processing tool
**Researched:** 2026-05-14
**Mode:** Ecosystem

---

## Summary

This API's sole job is to make the pipeline observable and controllable from a web UI. The pipeline is already fully functional via CLI; the API adds no new processing logic. Every feature must be evaluated through this lens: "Can the UI do anything useful without this?" If yes, defer it. If no, it's table stakes.

The domain closest to this project is ETL/media pipeline dashboards (Airflow, Jenkins, Azure DevOps Pipelines). Those systems converge on a clear pattern: a small set of control endpoints (trigger/cancel), a richer set of observation endpoints (status, logs, data), and a real-time channel (SSE or WebSocket). Authentication is irrelevant for single-user local tools.

For real-time updates, SSE is the correct choice over WebSockets. SSE is one-directional (server to client), uses plain HTTP, requires no upgrade handshake, and is natively supported in browsers with automatic reconnection. For a pipeline that only needs to push progress events to the UI, SSE is simpler and sufficient. The aiohttp-sse library (maintained by aio-libs, the same org as aiohttp) provides a `sse_response` context manager that integrates directly into the existing aiohttp dependency with no additional framework.

Progress event design follows the Jenkins/Azure DevOps pattern: each event carries stage name, status (pending/running/done/error), and optional numeric progress (0-100) for stages where byte counts are available (download, audio encode). The episode GUID is always included so a multi-episode UI can route events to the right row.

Log endpoints follow the cursor-based pagination pattern from API design best practices: offset/limit for content reads, SSE tail for real-time follow. Keyset pagination by byte offset is preferred over line-number offset because log files grow continuously and line-number offsets shift.

Config and feed CRUD use GET + PATCH (merge patch semantics). Validation happens before any write: if the Pydantic model rejects the input, return 422 and do not touch config.yaml. This prevents partial writes that corrupt the file.

DB viewer endpoints are read-only GET with offset/limit pagination and a small set of filterable query params (feed slug, episode guid, status). No write operations, no delete. The pipeline is the sole writer; the API is a window.

---

## Table Stakes

Features the UI cannot function without. Missing any of these makes the API useless for its stated purpose.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| GET /status — pipeline run state | UI needs to know if something is running before showing controls | Low | Returns: running/idle, active feed slug, active episode guid |
| POST /run — trigger full pipeline run | Core control action; the whole point of having a UI | Low | 409 if already running |
| POST /run/stop — cancel running pipeline | User must be able to stop a runaway or incorrect run | Medium | Must signal the asyncio task cleanly; pipeline handles graceful exit |
| GET /events (SSE) — real-time progress stream | Without this, UI must poll; polling gives degraded UX for a long multi-episode run | High | Per-episode stage events + run-level counters; broadcast to all connected clients |
| GET /feeds — list configured feeds | UI needs feed list to populate any control surface | Low | Returns config feeds array; includes enabled flag and slug |
| POST /feeds — add a feed | Feed management is an explicitly required capability | Low | Validates URL format + required fields before writing config.yaml |
| PATCH /feeds/{slug} — update a feed | Must be able to toggle enabled/disabled and update URL | Low | Merge patch; validates before write |
| DELETE /feeds/{slug} — remove a feed | Feed list grows stale; must be purgeable | Low | Removes from config.yaml; does not touch DB or output files |
| GET /settings — current config | UI settings panel needs to show current values | Low | Returns full AppConfig as JSON; strips credentials |
| PATCH /settings — update settings | User must be able to change models, paths, thresholds from UI | Medium | Pydantic validation before write; applies on next run |
| GET /logs — list log files | UI log viewer needs a file picker | Low | Returns filenames + sizes + modification timestamps |
| GET /logs/{filename} — log file content | Core debugging capability; log content must be readable | Low | Offset+limit pagination by byte; returns lines array + next_offset |
| GET /episodes — list episodes with status | UI dashboard row is an episode; must know state for each | Low | Paginated; filterable by feed slug and status |
| GET /episodes/{guid} — episode detail | Clicking a row in the UI needs a detail view | Low | Returns all DB fields for that episode |

---

## Differentiators

Features that improve UX meaningfully without being blockers. Build after all table stakes are solid.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| SSE /logs/{filename}/tail — real-time log follow | Developer ergonomics: follow a log like `tail -f` without shell access | Medium | Requires async file watcher (asyncio + inotify or poll); SSE stream per file |
| POST /feeds/{slug}/run — trigger single feed | Power user workflow: re-run just one feed without touching others | Low | Reuses pipeline run path, scoped to slug; 409 if run in progress |
| POST /episodes/{guid}/reprocess — reset and re-run episode | Recovery action: re-run a failed or incorrectly processed episode | Medium | Must reset DB state to initial, then queue episode for re-run through state machine |
| POST /episodes/{guid}/skip — mark episode as done without processing | Intentional exclusion: some episodes the user does not want processed | Low | Sets DB state to a terminal "skipped" sentinel; pipeline guard skips it |
| GET /db/transcriptions — paginated transcription records | Inspect LLM transcription output without opening SQLite directly | Low | Read-only; filter by guid |
| GET /db/ad-detections — paginated ad detection records | Verify which segments were detected as ads + confidence scores | Low | Read-only; filter by guid, min_confidence |
| GET /db/costs — paginated cost tracking records | Track LLM spend over time | Low | Read-only; filter by provider/model |
| GET /db/topics — paginated topic extraction records | Inspect extracted topic + host metadata | Low | Read-only; filter by guid |
| SSE /events with event type filtering | Reduce noise: client subscribes to only "episode:progress" events, not run-level counters | Low | SSE event field carries type name; client uses EventSource.addEventListener by type |
| SSE Last-Event-ID replay — resume missed events | Resilient UI: reconnect after browser tab sleep without losing context | Medium | Requires in-memory ring buffer (last N events); replay on reconnect using Last-Event-ID header |
| GET /episodes?status=failed — filter by failure state | Maintenance workflow: quickly surface all episodes that errored | Low | Adds status query param to /episodes; maps to DB state column |

---

## Anti-features (v1)

Things to deliberately not build in v1. Each has a concrete reason and a "when to revisit" condition.

| Anti-Feature | Why Avoid | What to Do Instead | Revisit When |
|--------------|-----------|-------------------|--------------|
| Authentication / API keys | Local network only; adds implementation complexity with zero security benefit in the target deployment | Document that the API binds to localhost or a trusted LAN interface | Moving to cloud or multi-user deployment |
| WebSocket endpoint | SSE covers all push-to-client needs; WebSocket adds bidirectional complexity and a separate protocol upgrade path | Use SSE for push; HTTP POST for control commands | A use case requiring client-to-server streaming emerges |
| Live/hot config apply (mid-run) | Config changes during a run risk partial-run config drift and undefined behavior in the state machine | Apply on next run; return a "will apply on next run" note in PATCH response | Pipeline gains a safe config-reload checkpoint mechanism |
| Episode create/delete via API | Episodes are derived from RSS feed contents; the pipeline is the authoritative creator and deleter | Skip or reprocess via POST /episodes/{guid}/skip or /reprocess | Feed management model changes fundamentally |
| Bulk operations (multi-episode batch skip/reprocess) | Requires careful transaction semantics and error reporting; rare use case for a personal tool | Single-episode actions cover the practical need | User feedback shows bulk is frequently needed |
| Metrics / Prometheus endpoint | No existing metrics instrumentation; adding it is a separate project | Use log-derived cost data via /db/costs | Operator wants dashboards beyond what the native UI provides |
| Full-text search over logs or transcriptions | High implementation complexity (SQLite FTS or elasticsearch); not needed for a single-user local tool | Use paginated log reads + browser Ctrl+F | Scale or multi-user requirements emerge |
| Admin endpoints (drop DB, reset all state) | Destructive operations via HTTP with no auth are dangerous even on localhost | Use CLI or direct SQLite access | Auth is implemented |
| OpenAPI/Swagger UI | Useful for public APIs; this API is consumed by one UI we control | Write the client against the known spec; add OpenAPI later if third-party integrations emerge | API becomes public-facing |

---

## Endpoint Inventory (proposed)

Grouped by resource. HTTP verbs follow REST conventions. All paths prefixed `/api/v1`.

### Pipeline Control

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | /api/v1/status | Current run state | Returns `{running: bool, feed_slug: str|null, episode_guid: str|null, started_at: str|null}` |
| POST | /api/v1/run | Trigger full pipeline run | 202 Accepted if started; 409 Conflict if already running |
| POST | /api/v1/run/stop | Cancel current run | 200 if signal sent; 409 if not running |
| POST | /api/v1/feeds/{slug}/run | Trigger single feed | Same 202/409 semantics; implies full run scoped to slug |

### Real-Time Events

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | /api/v1/events | SSE stream — all run + episode events | `text/event-stream`; event types: `run:started`, `run:finished`, `episode:stage`, `episode:progress`, `episode:done`, `episode:error` |

### Feeds (Config CRUD)

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | /api/v1/feeds | List feeds from config | Returns array with slug, title, url, enabled, episodes_to_keep |
| POST | /api/v1/feeds | Add feed | Body: `{title, url, enabled?, episodes_to_keep?}`; 422 on validation fail |
| PATCH | /api/v1/feeds/{slug} | Update feed fields | Merge patch; validates before writing config.yaml |
| DELETE | /api/v1/feeds/{slug} | Remove feed | 404 if slug not found; 200 on success |

### Settings (Config GET/PATCH)

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | /api/v1/settings | Full current config | Omits credentials (env vars); returns full AppConfig-shaped JSON |
| PATCH | /api/v1/settings | Update settings | Merge patch; validates full config after merge; writes config.yaml on success |

### Episodes (DB read-only)

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | /api/v1/episodes | Paginated episode list | Query params: `feed_slug`, `status`, `limit` (default 20, max 100), `offset` |
| GET | /api/v1/episodes/{guid} | Episode detail | All DB fields for that guid |
| POST | /api/v1/episodes/{guid}/reprocess | Reset + re-run episode | Clears DB state; queues episode through state machine |
| POST | /api/v1/episodes/{guid}/skip | Mark episode as skipped | Sets terminal "skipped" state in DB |

### Database Viewer (read-only)

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | /api/v1/db/transcriptions | Paginated transcription records | Query: `guid`, `limit`, `offset` |
| GET | /api/v1/db/ad-detections | Paginated ad detection records | Query: `guid`, `min_confidence`, `limit`, `offset` |
| GET | /api/v1/db/topics | Paginated topic extraction records | Query: `guid`, `limit`, `offset` |
| GET | /api/v1/db/costs | Paginated cost records | Query: `provider`, `model`, `limit`, `offset` |

### Logs

| Method | Path | Purpose | Notes |
|--------|------|---------|-------|
| GET | /api/v1/logs | List log files | Returns array of `{filename, size_bytes, modified_at}`; includes app log + all per-episode logs |
| GET | /api/v1/logs/{filename} | Paginated log content | Query: `offset` (byte offset), `limit` (bytes); returns `{lines: [...], next_offset: int}` |
| GET | /api/v1/logs/{filename}/tail | SSE real-time tail | `text/event-stream`; pushes new lines as they are written; client disconnects to stop |

---

## Feature Dependencies

```
POST /run → GET /status (status endpoint tells UI if run was accepted or rejected)
GET /events → POST /run (events only flow during an active run)
POST /episodes/{guid}/reprocess → GET /episodes/{guid} (need to know current state first)
GET /api/v1/logs/{filename} → GET /api/v1/logs (need filename list before content)
GET /db/transcriptions → GET /episodes (episode guid needed to filter)
PATCH /settings → GET /settings (need current values before patching)
PATCH /feeds/{slug} → GET /feeds (need slug from feed list)
```

---

## MVP Recommendation

Build in this order to get a functional UI as fast as possible:

**Phase 1 — Control + Observe (unblock the UI)**
1. GET /api/v1/status
2. POST /api/v1/run + POST /api/v1/run/stop
3. GET /api/v1/events (SSE, basic stage events, no replay buffer)
4. GET /api/v1/episodes + GET /api/v1/episodes/{guid}

**Phase 2 — Manage (config + feeds)**
5. GET/PATCH /api/v1/settings
6. GET/POST/PATCH/DELETE /api/v1/feeds

**Phase 3 — Debug (logs + DB viewer)**
7. GET /api/v1/logs + GET /api/v1/logs/{filename}
8. GET /api/v1/db/* endpoints

**Defer to later**
- POST /episodes/{guid}/reprocess and /skip — useful but not needed to observe a run
- GET /logs/{filename}/tail — nice-to-have; polling /logs/{filename} with advancing offset is acceptable initially
- SSE Last-Event-ID replay — reconnection is acceptable without replay for a local tool
- POST /feeds/{slug}/run — single-feed trigger; full run covers 90% of the use case

---

## Sources

- [API Design Guidance: Long-Running Background Jobs — Tyk](https://tyk.io/blog/api-design-guidance-long-running-background-jobs/)
- [REST API Design for Long-Running Tasks — RestfulAPI.net](https://restfulapi.net/rest-api-design-for-long-running-tasks/)
- [Jenkins Pipeline Stage View REST API](https://github.com/jenkinsci/pipeline-stage-view-plugin/blob/master/rest-api/README.md)
- [Azure DevOps Build Status REST API](https://learn.microsoft.com/en-us/rest/api/azure/devops/build/status/get)
- [aiohttp-sse — aio-libs](https://github.com/aio-libs/aiohttp-sse)
- [Server-Sent Events: A Comprehensive Guide](https://medium.com/@moali314/server-sent-events-a-comprehensive-guide-e4b15d147576)
- [SSE Format and Last-Event-ID — javascript.info](https://javascript.info/server-sent-events)
- [Pagination Best Practices — Speakeasy](https://www.speakeasy.com/api-design/pagination)
- [REST API Design: Filtering, Sorting, Pagination — Moesif](https://www.moesif.com/blog/technical/api-design/REST-API-Design-Filtering-Sorting-and-Pagination/)
- [HTTP PATCH Method — Postman Blog](https://blog.postman.com/http-patch-method/)
- [Best Practices for a Pragmatic RESTful API — Vinay Sahni](https://www.vinaysahni.com/best-practices-for-a-pragmatic-restful-api)
