# Phase 6: Log Access - Context

**Gathered:** 2026-05-22
**Status:** Ready for planning

<domain>
## Phase Boundary

Expose the `logs/` directory as REST + SSE endpoints: list all log files (app-level + per-episode) with metadata, read full or paginated file content, and tail files in real time via SSE. Path traversal protection is required. No write path exists.

**Requirements in scope:** LOG-01, LOG-02, LOG-03
**Out of scope:** authentication, log deletion, log filtering/search, Last-Event-ID replay (EVT-02 — deferred v2).

</domain>

<decisions>
## Implementation Decisions

### Log Listing — LOG-01

- **D-01:** Response is a **hierarchical JSON object**: `{"app_logs": [...], "episode_logs": {"<feed-slug>": [...]}}`. App logs = top-level `logs/*.log` files. Episode logs = `logs/episodes/<feed-slug>/*.log` files grouped by feed slug.
- **D-02:** Each entry has three fields: `filename` (relative path from `logs/` root, e.g. `2026-05-22T02-12-19.log` or `episodes/prof-g-markets/ai-skeptic.ts.log`), `size_bytes` (integer), `last_modified` (ISO 8601 timestamp).
- **D-03:** `filename` is the string clients pass directly in the URL: `GET /api/v1/logs/episodes/prof-g-markets/ai-skeptic.ts.log` — no extra encoding needed.

### Route Pattern — LOG-02 + LOG-03

- **D-04:** Route uses **aiohttp tail match**: `routes.get("/api/v1/logs/{tail:.*}")` and `routes.get("/api/v1/logs/{tail:.*}/tail")`. This captures multi-segment paths (including slashes) without URL encoding.
- **D-05:** Path traversal validation: resolve `(log_dir / tail).resolve()` and verify it's relative to `log_dir.resolve()` using `Path.is_relative_to()`. Return **400** (not 404) on traversal attempts — unambiguous rejection.

### Log File Content — LOG-02

- **D-06:** Response Content-Type is `text/plain; charset=utf-8`. Body is the raw log text (or byte slice). No JSON wrapping.
- **D-07:** Pagination metadata in response headers: `X-Log-Size: <total_bytes>`, `X-Log-Offset: <offset>`, `X-Log-Limit: <bytes_returned>`. `?offset=N&limit=N` are byte offsets (not lines). No `offset` = return full file; no `limit` = return to EOF.

### SSE Tail — LOG-03

- **D-08:** On connect, send the last `?bytes=N` of the file as an initial SSE event (backfill). Default `N=8192` (8 KB). Min and max are not enforced — caller controls. If the file is smaller than N bytes, send the whole file.
- **D-09:** After backfill, poll the file every `?interval=N` seconds for new content. Default `N=1.0`. Min `0.5`, max `10.0` — clamp silently (no error on out-of-range values).
- **D-10:** Polling uses `asyncio.to_thread` (as specified in LOG-03). Track current byte position; on each poll, read from last position to current EOF.
- **D-11:** **Rotation detection**: if `file.stat().st_size < last_position` (file shrank), reopen from byte 0. This handles log rotation transparently without breaking the SSE stream.
- **D-12:** Each SSE event contains one or more new log lines as plain text in the `data:` field. No JSON wrapping. Matches the existing `events.py` SSE pattern.
- **D-13:** Disconnect handling: unregister the polling task in a `finally` block (same pattern as SSE subscriber cleanup in `events.py`).

### Dependency Injection

- **D-14:** `create_logs_router(log_dir: Path) -> web.RouteTableDef` — constructor arg pattern, identical to `create_db_router(db_path, output_dir)`. `main.py` extracts log dir from `config.app.log` and passes to `serve()`, which passes to `create_app()`, which passes to `create_logs_router()`.

### Claude's Discretion

- Exact aiohttp route registration order (whether `/tail` sub-route needs to be registered before the glob or handled inside the handler via suffix check).
- Whether `asyncio.to_thread` wraps a single `Path.read_bytes()` slice or opens a file handle that's kept open between polls.
- SSE event `id:` field — omit (EVT-02 Last-Event-ID replay is deferred v2).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Requirements & Architecture
- `.planning/ROADMAP.md` — Phase 6 goal, success criteria (LOG-01–03), dependency chain
- `.planning/REQUIREMENTS.md` — Full LOG-01, LOG-02, LOG-03 requirement text; traceability
- `.planning/PROJECT.md` — Core constraints: async throughout, same-process server, CLAUDE.md rules

### Key Existing Files (integration points)
- `api/server.py` — `create_app()` factory; route registration pattern; `app` dict for shared state
- `api/routes/db.py` — `create_db_router(db_path, output_dir)` — closest pattern to follow (multi-arg constructor)
- `api/routes/events.py` — SSE streaming pattern; subscriber cleanup in `finally` block — tail SSE follows the same disconnect-handling pattern
- `main.py` — `configure_logging()` and `_rotate_logs()` — shows log dir config; `serve()` entry point to update
- `utils/episode_log.py` — `open_episode_log()` — shows log path convention: `logs/episodes/<podcast-slug>/<episode-slug>.<ts>.log`

### CLAUDE.md Constraints (hard rules)
- Never use `web.run_app()` — `AppRunner` + `TCPSite`
- F-strings only for logging
- 100% test coverage required
- Async throughout; no blocking calls in event loop

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `utils/episode_log.py` — log path convention; `rotate_episode_logs()` shows slug + timestamp filename format
- `api/routes/events.py` — SSE response setup, `finally`-block subscriber cleanup — direct template for `/tail` handler
- `api/routes/db.py:create_db_router` — multi-arg factory pattern; `{tail:.*}` route equivalent for DB was single-level

### Established Patterns
- **`create_X_router(deps) -> web.RouteTableDef`** — factory from all existing route files; `create_logs_router(log_dir)` follows identically
- **aiohttp `app["key"]`** — store `log_dir` in `app["log_dir"]` if needed, or pass via closure
- **`asyncio_mode = "auto"`** — async tests need no decorator; test pattern established in `tests/test_api_*.py`
- **SSE disconnect handling** — `finally` block unregisters the subscriber; tail polling task must be cancelled in the same block

### Integration Points
- `api/server.py:create_app()` — add `log_dir: Path` parameter; register `create_logs_router(log_dir)` route table
- `main.py:serve()` — extract `log_dir` from config and pass to `create_app()`
- `config/config_loader.py` — check `app.log` config field for log directory path (currently used by `configure_logging`)

</code_context>

<specifics>
## Specific Ideas

- Route strategy: register `/api/v1/logs/{tail:.*}/tail` BEFORE `/api/v1/logs/{tail:.*}` so aiohttp's router matches the `/tail` suffix correctly (more specific routes must come first in aiohttp's `RouteTableDef`).
- Rotation detection: compare `stat().st_size < last_position` rather than inode tracking — simpler and sufficient for the local log rotation case.
- Backfill implementation: seek to `max(0, file_size - bytes_param)`, read to EOF, send as first `data:` event.
- `asyncio.to_thread` wraps the blocking `file.seek()` + `file.read()` per poll cycle — keep the file handle open between polls for efficiency, close in the `finally` block.

</specifics>

<deferred>
## Deferred Ideas

- **EVT-02: Last-Event-ID replay for /tail** — reconnecting client resumes from byte offset via `Last-Event-ID` header — deferred to v2 per REQUIREMENTS.md
- **Log search/filtering** — `?contains=ERROR` query param to filter lines — not in scope; belongs in a future enhancement phase
- **Log deletion** — `DELETE /api/v1/logs/{filename}` — not requested; no write path in this phase

</deferred>

---

*Phase: 6-log-access*
*Context gathered: 2026-05-22*
