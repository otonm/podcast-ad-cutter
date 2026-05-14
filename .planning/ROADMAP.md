# Roadmap: Podcast Ad Cutter — Web API

## Overview

Brownfield milestone that adds a REST + SSE web API to an existing async Python pipeline. Six phases deliver the capability vertically: each phase ships a working end-to-end slice — server foundation, live event streaming, pipeline control, config/feed management, database viewer, and log access — building on the previous phase until a web UI can observe and control the entire pipeline without touching the filesystem or CLI.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: API Foundation** - `--serve` flag starts aiohttp server; health check responds; EventBus and dual-mode entry are clean
- [ ] **Phase 2: SSE Progress Stream** - Connected SSE client receives live stage-transition and progress events while the pipeline runs
- [ ] **Phase 3: Pipeline Control** - Client can start, stop, and inspect a pipeline run; per-feed and per-episode control works
- [ ] **Phase 4: Config & Feed Management** - All settings and feed configuration can be read and modified via API; changes persist atomically
- [ ] **Phase 5: Database Viewer** - All database tables exposed as read-only REST endpoints with pagination and filtering
- [ ] **Phase 6: Log Access** - All log files can be listed, downloaded, and tailed in real time via SSE

## Phase Details

### Phase 1: API Foundation
**Goal**: `--serve` flag starts an aiohttp.web server that responds to health checks; the EventBus class exists; Pipeline accepts optional EventBus; dual-mode entry is clean
**Mode**: mvp
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02
**Success Criteria** (what must be TRUE):
  1. Running `python main.py --serve` starts the server and keeps the process alive; running without `--serve` still runs the pipeline once and exits
  2. `GET /api/v1/health` returns HTTP 200 with a JSON body containing server uptime and application version
  3. An `EventBus` class exists that supports multiple concurrent subscriber queues and an `emit()` method
  4. `Pipeline` accepts an optional `EventBus` argument without breaking existing CLI behavior
**Plans**: TBD
**UI hint**: no

### Phase 2: SSE Progress Stream
**Goal**: A connected SSE client receives live stage-transition events, download/encode percentages, and run-level counters while the pipeline runs
**Mode**: mvp
**Depends on**: Phase 1
**Requirements**: EVT-01
**Success Criteria** (what must be TRUE):
  1. A client connected to `GET /api/v1/events` receives a Server-Sent Event for each episode stage transition (download, preprocess, transcribe, topic, ad-detect, edit)
  2. Download and encode percentage progress is streamed as numeric fields in SSE event payloads
  3. Run-level counters (total/done/failed per feed) are included in events and stay accurate as episodes complete
  4. Multiple concurrent SSE clients each receive the full event stream independently
  5. Disconnecting a client does not affect other connected clients or the pipeline
**Plans**: TBD
**UI hint**: no

### Phase 3: Pipeline Control
**Goal**: A client can start, stop, and inspect a pipeline run; per-feed and per-episode control works
**Mode**: mvp
**Depends on**: Phase 2
**Requirements**: STAT-01, CTRL-01, CTRL-02, CTRL-03, CTRL-04, CTRL-05
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/status` returns current state (idle/running), active feed slug, per-feed episode counts, and run start time
  2. `POST /api/v1/run` triggers a full pipeline run and returns 409 if a run is already active
  3. `POST /api/v1/run/stop` signals graceful stop and returns 409 if nothing is running
  4. `POST /api/v1/feeds/{slug}/run` runs the pipeline for one specific feed identified by slug
  5. `POST /api/v1/episodes/{guid}/reprocess` resets an episode's DB state and requeues it; accepts optional stage parameter for partial reset
  6. `POST /api/v1/episodes/{guid}/skip` marks an episode as permanently skipped; pipeline does not process it on subsequent runs
**Plans**: TBD
**UI hint**: no

### Phase 4: Config & Feed Management
**Goal**: All settings and feed configuration can be read and modified via the API; changes persist atomically to config.yaml
**Mode**: mvp
**Depends on**: Phase 3
**Requirements**: STAT-02, STAT-03, FEED-01, FEED-02, FEED-03, FEED-04
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/settings` returns the full config as JSON with all credential fields (API keys, tokens) replaced by redacted placeholders
  2. `PATCH /api/v1/settings` validates the merged payload through Pydantic and writes atomically to `config.yaml`; returns 422 with field-level errors on validation failure
  3. `GET /api/v1/feeds` returns all configured feeds with slug, URL, and episode count
  4. `POST /api/v1/feeds` adds a validated new feed entry to `config.yaml` and rejects duplicates
  5. `PATCH /api/v1/feeds/{slug}` updates a feed's URL or per-feed settings after Pydantic validation
  6. `DELETE /api/v1/feeds/{slug}` removes the feed from `config.yaml`; returns 404 if slug does not exist
**Plans**: TBD
**UI hint**: no

### Phase 5: Database Viewer
**Goal**: All database tables exposed as read-only REST endpoints with pagination and filtering
**Mode**: mvp
**Depends on**: Phase 4
**Requirements**: DB-01, DB-02, DB-03, DB-04
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/db/episodes` returns episodes with GUID, feed slug, pipeline state, and timestamps; `?offset` and `?limit` pagination works; `?feed={slug}` filters by feed
  2. `GET /api/v1/db/transcriptions/{guid}` returns full transcription text and segment data for the requested episode
  3. `GET /api/v1/db/ads/{guid}` returns detected ad segments, confidence scores, and cut ranges for the requested episode
  4. `GET /api/v1/db/costs` returns LLM API costs per episode and aggregate totals; `?feed={slug}` filter works
  5. All DB endpoints are read-only; no write path exists through the API
**Plans**: TBD
**UI hint**: no

### Phase 6: Log Access
**Goal**: All log files (general + per-episode) can be listed, downloaded, and tailed in real time
**Mode**: mvp
**Depends on**: Phase 5
**Requirements**: LOG-01, LOG-02, LOG-03
**Success Criteria** (what must be TRUE):
  1. `GET /api/v1/logs` returns a list of all log files with filename, size in bytes, and last-modified timestamp
  2. `GET /api/v1/logs/{filename}` returns the full log file content; `?offset=N&limit=N` byte-offset pagination returns the correct slice
  3. `GET /api/v1/logs/{filename}/tail` streams new log lines via SSE as they are appended to the file in real time
  4. Requesting a filename with path traversal characters (e.g., `../`) returns 400 or 404 and does not expose files outside the log directory
**Plans**: TBD
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. API Foundation | 0/TBD | Not started | - |
| 2. SSE Progress Stream | 0/TBD | Not started | - |
| 3. Pipeline Control | 0/TBD | Not started | - |
| 4. Config & Feed Management | 0/TBD | Not started | - |
| 5. Database Viewer | 0/TBD | Not started | - |
| 6. Log Access | 0/TBD | Not started | - |
