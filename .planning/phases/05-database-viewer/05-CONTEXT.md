# Phase 5: Database Viewer - Context

**Gathered:** 2026-05-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Expose the existing SQLite database as read-only REST endpoints: episode list with pagination and feed filter, per-episode transcription detail, per-episode ad detection results, and LLM cost aggregates. No write path exists through any of these endpoints. One schema migration is required to link cost_tracking rows to episodes.

**Requirements in scope:** DB-01, DB-02, DB-03, DB-04
**Out of scope:** DB-05 (topics endpoint — deferred to v2), any write endpoints, authentication.

</domain>

<decisions>
## Implementation Decisions

### Schema Migration (cost_tracking)

- **D-01:** `cost_tracking` table must gain a nullable `guid TEXT REFERENCES episodes(guid)` column via `ALTER TABLE`. Existing rows get `NULL`. This is a prerequisite for DB-04 per-episode cost breakdown.
- **D-02:** `CostTrackingStore.save_cost()` gains an optional `guid: str | None = None` parameter. All pipeline call sites that know the episode GUID at cost-recording time must pass it.

### Episode List — DB-01

- **D-03:** Response includes the **full episodes row** (all ~20 columns) plus a derived `pipeline_state` field. `feed_slug` is derived on the fly as `slugify(episode.podcast)` — same function used in `FeedPublisher` and `feeds.py`.
- **D-04:** `pipeline_state` is derived in a **single SQL query with LEFT JOINs** (not N sub-queries). Priority ladder (highest wins): `skipped` (episodes.skipped=1) → `complete` (output file exists on filesystem) → `processed` (has ad_detection_runs row) → `transcribed` (has transcriptions row) → `downloaded` (has episode_audio_metadata row) → `pending`.
- **D-05:** `complete` is determined by checking for the episode's output file on the filesystem. `create_db_router` receives `output_dir: Path` from `create_app`. The executor must derive the exact output file naming convention from the pipeline codebase.
- **D-06:** Default sort: `pubdate DESC`, NULL pubdates sort last. Default `limit=50`, `offset=0`, max enforced `limit=200`. The `?feed={slug}` filter maps slug back to feed title via the same `slugify()` reverse lookup pattern (iterate feeds config, match slug).
- **D-07:** `pubdate` is the only timestamp returned — no pipeline processing timestamps exist in the DB schema and none will be added in this phase.

### Transcription Detail — DB-02

- **D-08:** Response shape: `{guid, text, segments: [{start, end, text}, ...]}`. `start`/`end` are the segment timestamps from `transcription_segments`. Returns 404 if no transcription row exists for the GUID.

### Ad Detection Detail — DB-03

- **D-09:** Response shape: `{guid, detected: bool, segments: [{start_ms, end_ms, confidence, sponsor, ad_topic}, ...]}`. `detected` is `true` when an `ad_detection_runs` row exists (even if segments is empty — episode was processed but had no ads). `indices` column is an internal implementation detail and is **not** returned. Returns 404 if no `ad_detection_runs` row exists for the GUID.

### Cost Aggregates — DB-04

- **D-10:** Response shape: `{total: float, by_model: [{provider, model, cost}, ...], by_episode: [{guid, cost}, ...]}`. `by_episode` entries have `NULL` guid for rows predating the migration — omit those rows from `by_episode` (include in total and `by_model` only). `?feed={slug}` filters all three sections to episodes belonging to the matched feed (join via `episodes.podcast`).

### Claude's Discretion

- Route organization: `api/routes/db.py` with `create_db_router(db_path, output_dir)` factory following the established pattern.
- Whether `pipeline_state` derivation uses a single complex LEFT JOIN or a CTE — executor's choice based on readability.
- Exact SQL for `pubdate DESC NULLS LAST` (SQLite syntax: `ORDER BY pubdate DESC, id DESC` since SQLite doesn't support NULLS LAST natively — `pubdate IS NULL ASC, pubdate DESC`).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Requirements & Architecture
- `.planning/ROADMAP.md` — Phase 5 goal, success criteria (DB-01–04), dependency chain
- `.planning/REQUIREMENTS.md` — Full DB-01 through DB-04 requirement text; traceability
- `.planning/PROJECT.md` — Core constraints: same-process server, async throughout, never share aiosqlite connection

### Database Schema (source of truth)
- `database/connection.py` — All table schemas: `episodes`, `episode_audio_metadata`, `transcriptions`, `transcription_segments`, `topic_extractions`, `ad_segments`, `ad_detection_runs`, `cost_tracking`; `Database` async context manager
- `database/episode_store.py` — `EpisodeStore`; `get_episodes_for_feed()` column order reference; `STAGE_CASCADE` map
- `database/transcription_store.py` — `TranscriptionStore`; segment schema reference
- `database/ad_store.py` — `AdStore`; `ad_segments` / `ad_detection_runs` access patterns
- `database/cost_tracking_store.py` — `CostTrackingStore.save_cost()` — call sites must be updated to pass `guid`

### Key Existing Files (integration points)
- `api/server.py` — `create_app()` factory; route registration pattern; `app` dict for shared state
- `api/routes/feeds.py` — `create_feeds_router(config_path, db_path)` factory — closest pattern to follow (receives both config and db_path)
- `api/routes/control.py` — `create_control_router()` — `Database` per-request open pattern; slug resolution
- `components/feed_publisher.py` — `slugify()` — authoritative slug algorithm (line ~120)
- `config/config_loader.py` — `AppConfig`, `FeedConfig`; `config.app.paths` for output_dir

### CLAUDE.md Constraints (hard rules)
- Never share the aiosqlite connection between pipeline and API handlers — open per-request `Database` context
- Never use `web.run_app()` — `AppRunner` + `TCPSite`
- F-strings only for logging
- 100% test coverage required

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `database/connection.py:Database` — async context manager; open per-request for all DB viewer reads
- `components/feed_publisher.py:slugify` — derive feed_slug per episode; reverse-lookup for `?feed={slug}` filter
- `api/routes/feeds.py:create_feeds_router` — exact factory pattern and dependency injection to replicate for `create_db_router`

### Established Patterns
- **`create_X_router(deps) -> web.RouteTableDef`** — factory from health.py, events.py, control.py, settings.py, feeds.py; db.py follows identically
- **Per-request `async with Database(db_path) as db:`** — established in Phase 3 (control.py) and Phase 4 (feeds.py); all DB viewer handlers follow the same pattern
- **`app["key"]`** — aiohttp app dict for server-lifetime shared state; `app["db_path"]` and `app["output_dir"]` stored here
- **`asyncio_mode = "auto"`** — async tests need no decorator
- **WAL constraint** — dedicated read-only connection per request; CLAUDE.md prohibits sharing with pipeline connection

### Integration Points
- `api/server.py:create_app()` — add `output_dir: Path` parameter; register `create_db_router(db_path, output_dir)` route table; store `app["output_dir"] = output_dir`
- `main.py` — pass `config.app.paths.output_dir` (or equivalent config field) to `serve()` so `create_app()` receives it
- `database/cost_tracking_store.py:save_cost()` — add `guid: str | None = None` parameter; all pipeline call sites updated
- `database/connection.py` — add `ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid)` migration (wrapped in `contextlib.suppress(OperationalError)` like existing migrations)

</code_context>

<specifics>
## Specific Ideas

- `pipeline_state` derivation uses LEFT JOINs in a single SQL query — no N+1 per-row sub-queries
- `complete` state requires filesystem check: `Path(output_dir / <derived_filename>).exists()` — executor derives the naming convention from the pipeline output code
- `cost_tracking.guid` is nullable — pre-migration rows have NULL; `by_episode` in DB-04 response silently omits NULL-guid rows
- DB-03's `detected` flag is `true` when `ad_detection_runs` has a row, even if `ad_segments` is empty (no ads found is still a completed detection)

</specifics>

<deferred>
## Deferred Ideas

- **DB-05: Topics endpoint** — `GET /api/v1/db/topics/{guid}` (topic name, hosts, show from `topic_extractions`) — deferred to v2 per REQUIREMENTS.md
- **Episode ordering control** — `?sort=pubdate|id` query param — not requested; pubdate DESC is the fixed default

</deferred>

---

*Phase: 5-database-viewer*
*Context gathered: 2026-05-19*
