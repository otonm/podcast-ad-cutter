# Requirements: Podcast Ad Cutter — Web API

**Defined:** 2026-05-14
**Core Value:** A web UI can start a run, watch it progress in real time, and inspect every result without touching the filesystem or CLI.

## v1 Requirements

### Infrastructure

- [ ] **INFRA-01**: Server starts in API mode when `--serve` flag is passed to `main.py`; bare invocation still runs pipeline once and exits
- [ ] **INFRA-02**: `GET /api/v1/health` returns 200 with server uptime and version

### Pipeline Control

- [ ] **CTRL-01**: `POST /api/v1/run` triggers a full pipeline run across all configured feeds; returns 409 if a run is already active
- [ ] **CTRL-02**: `POST /api/v1/run/stop` signals the active pipeline run to stop gracefully; returns 409 if nothing is running
- [ ] **CTRL-03**: `POST /api/v1/feeds/{slug}/run` triggers pipeline processing for a specific feed only
- [ ] **CTRL-04**: `POST /api/v1/episodes/{guid}/reprocess` resets DB state for one episode (full reset or from a specified stage) and requeues it for the next run
- [ ] **CTRL-05**: `POST /api/v1/episodes/{guid}/skip` marks an episode as permanently skipped so the pipeline will not process it

### Progress Streaming

- [ ] **EVT-01**: `GET /api/v1/events` streams Server-Sent Events to connected clients; events include: per-episode stage transitions (download → preprocess → transcribe → topic → ad-detect → edit), download/encode percentage, and run-level counters (total/done/failed per feed)

### Status & Settings

- [ ] **STAT-01**: `GET /api/v1/status` returns current pipeline state (idle/running), active feed slug, per-feed episode counts, and run start time
- [ ] **STAT-02**: `GET /api/v1/settings` returns the current config as JSON with all credential fields redacted
- [ ] **STAT-03**: `PATCH /api/v1/settings` validates the merged payload through Pydantic, writes atomically to `config.yaml`, returns 422 on validation failure; changes apply on the next run

### Feed Management

- [ ] **FEED-01**: `GET /api/v1/feeds` returns all configured feeds with slug, URL, and episode count
- [ ] **FEED-02**: `POST /api/v1/feeds` adds a new feed entry to `config.yaml`; validates feed object through Pydantic before writing
- [ ] **FEED-03**: `PATCH /api/v1/feeds/{slug}` updates a feed's URL or per-feed settings; validates before writing
- [ ] **FEED-04**: `DELETE /api/v1/feeds/{slug}` removes a feed from `config.yaml`

### Log Access

- [ ] **LOG-01**: `GET /api/v1/logs` lists all log files (general app log + per-episode logs) with filename, size in bytes, and last-modified timestamp
- [ ] **LOG-02**: `GET /api/v1/logs/{filename}` returns full log file content with optional byte-offset pagination (`?offset=N&limit=N`)
- [ ] **LOG-03**: `GET /api/v1/logs/{filename}/tail` streams new lines via SSE as they are appended to the log file (using `asyncio.to_thread` file polling)

### Database Viewer

- [ ] **DB-01**: `GET /api/v1/db/episodes` returns the episode list with GUID, feed slug, pipeline state, and timestamps; supports `?offset=N&limit=N` and `?feed={slug}` filter
- [ ] **DB-02**: `GET /api/v1/db/transcriptions/{guid}` returns full transcription text and segments for one episode
- [ ] **DB-03**: `GET /api/v1/db/ads/{guid}` returns detected ad segments, confidence scores, and cut ranges for one episode
- [ ] **DB-04**: `GET /api/v1/db/costs` returns LLM API costs per episode and aggregate totals; supports `?feed={slug}` filter

## v2 Requirements

### Reliability

- **INFRA-03**: CORS middleware to allow cross-origin requests from the web UI (deferred until UI milestone — not needed while API and UI are co-located)
- **EVT-02**: Last-Event-ID replay — reconnecting SSE client receives missed events from a short in-memory buffer
- **EVT-03**: SSE heartbeat — 15-second comment ping to keep connections alive through reverse proxies

### Security

- **SEC-01**: API key authentication via `Authorization: Bearer <token>` header — deferred until deployment beyond trusted local network

### DB Viewer Extras

- **DB-05**: `GET /api/v1/db/topics/{guid}` — topic extraction data (topic name, hosts, show) for one episode

## Out of Scope

| Feature | Reason |
|---------|--------|
| WebSocket transport | REST + SSE covers all use cases; bidirectionality not needed since commands go over REST |
| GraphQL | Added complexity without benefit for this client/server pair |
| Hot-reload config during active run | Risk of config drift mid-run; apply-on-next-run is safe and sufficient |
| Web UI | This milestone is API only; UI is a subsequent milestone |
| Authentication in v1 | Local network deployment; add in v2 when needed |
| Scheduler / cron trigger | Pipeline runs are manual or CLI-triggered; scheduling deferred |

## Traceability

Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Pending |
| INFRA-02 | Phase 1 | Pending |
| EVT-01 | Phase 2 | Pending |
| STAT-01 | Phase 3 | Pending |
| CTRL-01 | Phase 3 | Pending |
| CTRL-02 | Phase 3 | Pending |
| CTRL-03 | Phase 3 | Pending |
| CTRL-04 | Phase 3 | Pending |
| CTRL-05 | Phase 3 | Pending |
| STAT-02 | Phase 4 | Pending |
| STAT-03 | Phase 4 | Pending |
| FEED-01 | Phase 4 | Pending |
| FEED-02 | Phase 4 | Pending |
| FEED-03 | Phase 4 | Pending |
| FEED-04 | Phase 4 | Pending |
| DB-01 | Phase 5 | Pending |
| DB-02 | Phase 5 | Pending |
| DB-03 | Phase 5 | Pending |
| DB-04 | Phase 5 | Pending |
| LOG-01 | Phase 6 | Pending |
| LOG-02 | Phase 6 | Pending |
| LOG-03 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 22 total
- Mapped to phases: 22
- Unmapped: 0 ✓

---
*Requirements defined: 2026-05-14*
*Last updated: 2026-05-14 after initial definition*
