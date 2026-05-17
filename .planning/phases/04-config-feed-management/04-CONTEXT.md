# Phase 4: Config & Feed Management - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Expose REST endpoints that let clients read application settings and manage feed configuration. All writes validate through Pydantic and persist atomically to `config.yaml`. Changes apply on the next pipeline run — no live-reload.

**Requirements in scope:** STAT-02, STAT-03, FEED-01, FEED-02, FEED-03, FEED-04
**Out of scope:** DB viewer, log access, SSE streaming, authentication — Phases 5–6 and v2.

</domain>

<decisions>
## Implementation Decisions

### Settings Endpoint (STAT-02, STAT-03)

- **D-01:** `GET /api/v1/settings` returns the full `AppConfig` (YAML-backed fields: feeds, models, paths, ad_detection, output, log, base_url) **plus** a separate `credentials` section showing presence — `"set"` or `"not set"` — for each provider key (`groq_api_key`, `openai_api_key`, `openrouter_api_key`). Actual key values are never returned.
- **D-02:** Only the `credentials` section needs redaction treatment. `AppConfig` has no actual secrets; no fields within it are masked.
- **D-03:** `GET /api/v1/settings` re-reads `config.yaml` from disk on every request. After a `PATCH`, the response immediately reflects the change. No in-memory caching of the settings response. No `pending_restart` field.
- **D-04:** `PATCH /api/v1/settings` is NOT blocked during active pipeline runs. Changes apply on next run anyway; no mid-run config drift risk.
- **D-05:** `PATCH /api/v1/settings` uses **deep merge** — client sends only the fields to change (e.g., `{"ad_detection": {"min_confidence": 0.8}}`); server merges into the current on-disk config and validates the merged result through Pydantic before writing.
- **D-06:** The `feeds` key is excluded from `PATCH /api/v1/settings`. Feed CRUD is handled exclusively by `/api/v1/feeds/*` endpoints.
- **D-07:** Unknown keys in the `PATCH` payload → **422 Unprocessable Entity**. Pydantic `extra='forbid'` enforces this; typos are rejected, not silently ignored.

### Feed Endpoints (FEED-01–FEED-04)

- **D-08:** Feed slug is derived on the fly as `slugify(feed.title)` — the same function `FeedPublisher` already uses. No `slug` field is added to `FeedConfig`. Implication: renaming a feed's `title` changes its slug and breaks existing URLs; acceptable for this single-user local tool.
- **D-09:** `GET /api/v1/feeds` episode count per feed comes from the DB — `COUNT(*)` from the episodes table `WHERE podcast = feed.title`. Not the config's `episodes_to_keep` limit.
- **D-10:** `POST /api/v1/feeds` requires `title` and `url`. `enabled` defaults to `true`; `episodes_to_keep` defaults to the `FeedConfig` model default. The server validates through `FeedConfig` before writing. Duplicate title → 409.
- **D-11:** `PATCH /api/v1/feeds/{slug}` can update: `url`, `enabled`, `episodes_to_keep`. `title` is excluded — renaming would change the slug and break the DB's `podcast` column linkage for existing episodes.
- **D-12:** Feed write endpoints (`POST`, `PATCH`, `DELETE`) are **not** blocked during active pipeline runs. Config writes apply on next run; there is no race with an active run reading config (Pipeline receives a fully-constructed `Config` at start, does not re-read config.yaml mid-run).

### Claude's Discretion

- Route file organization — `api/routes/settings.py` and `api/routes/feeds.py` following the `create_X_router(deps) -> RouteTableDef` factory pattern from prior phases.
- Exact temp file naming for atomic config write — e.g., `config.yaml.tmp` or use `tempfile.NamedTemporaryFile` in the same directory.
- Whether to open a dedicated read-only aiosqlite connection for the episode count query in `GET /api/v1/feeds` or reuse the per-request pattern from Phase 3.
- HTTP status for `DELETE /api/v1/feeds/{slug}` when slug not found — 404 (per REQUIREMENTS.md).
- Response shape for PATCH success — `200` with updated resource or `204 No Content`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Requirements & Architecture
- `.planning/ROADMAP.md` — Phase 4 goal, success criteria (STAT-02, STAT-03, FEED-01–04), dependency chain
- `.planning/REQUIREMENTS.md` — Full STAT-02, STAT-03, FEED-01–04 requirement text; traceability to Phase 4
- `.planning/PROJECT.md` — Core constraints: same-process server, async throughout, config isolation

### Codebase Architecture
- `.planning/codebase/ARCHITECTURE.md` — Full layer diagram; Pipeline as sole Config owner; anti-patterns
- `.planning/codebase/STACK.md` — aiohttp version; pytest-asyncio auto mode; aioresponses

### Key Existing Files (integration points)
- `config/config_loader.py` — `AppConfig`, `FeedConfig`, `Credentials`, `Config`, `load_config`; `PROVIDER_KEY_MAP` maps provider name to credentials field name; Pydantic validation entry point
- `config.example.yaml` — Canonical config schema reference (fields, types, defaults)
- `components/feed_publisher.py` — `slugify(feed.title)` slug derivation (line ~120); authoritative slug algorithm
- `api/server.py` — `create_app(event_bus, start_time, config_path)` factory; `app` dict for shared state; route registration pattern
- `api/routes/health.py` — `create_health_router()` factory pattern to follow
- `api/routes/control.py` — `create_control_router(event_bus, config)` factory from Phase 3; settings/feeds routes follow this
- `database/episode_store.py` — `get_episodes_for_feed(podcast)` (podcast = feed title); episode count query
- `database/connection.py` — `Database` async context manager; open per request for DB reads

### CLAUDE.md Constraints (hard rules)
- Config writes must be atomic: validate through Pydantic first, write to temp file, `os.replace()` for the swap
- Never share the aiosqlite connection between pipeline and API handlers
- Never use `web.run_app()` — use `AppRunner` + `TCPSite`
- F-strings only for logging (no `%` operator)
- 100% test coverage required

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `config/config_loader.py:AppConfig` — Pydantic model; use `model_validate()` on the deep-merged dict for PATCH validation; `extra='forbid'` enforces strict field checking
- `config/config_loader.py:FeedConfig` — Pydantic model for individual feed entries; `model_validate()` for POST/PATCH feed validation
- `config/config_loader.py:Credentials` — `pydantic-settings` model; check `getattr(creds, field)` to determine `"set"` vs `"not set"` for the response
- `config/config_loader.py:PROVIDER_KEY_MAP` — maps provider names to credential field names; use to build the credentials presence response
- `components/feed_publisher.py:slugify` — already imported; use the same call for slug derivation in feed routes
- `database/connection.py:Database` — async context manager; open per request for episode count queries

### Established Patterns
- **`create_X_router(deps) -> web.RouteTableDef`** — factory pattern from health.py, events.py, control.py; settings.py and feeds.py follow exactly
- **`app["key"]`** — aiohttp app dict for server-lifetime shared state; `app["config_path"]` for the config file path
- **Short-lived per-request aiosqlite connection** — Phase 3 pattern for episode control; same pattern for episode count in FEED-01
- **`asyncio_mode = "auto"`** in pytest config — async tests need no decorator
- **Pydantic `model_validate()`** — used throughout; feed and settings PATCH both validate merged result before writing

### Integration Points
- `api/server.py:create_app()` — add `config_path: Path` parameter; register `create_settings_router(config_path)` and `create_feeds_router(config_path)` route tables; store `app["config_path"] = config_path`
- `main.py` — pass config file path to `serve()` so `create_app()` can receive it; CLI mode unchanged
- `database/episode_store.py` — add a `count_episodes_for_feed(podcast: str) -> int` method (or inline a COUNT query in the feed route handler)

</code_context>

<specifics>
## Specific Ideas

- Credentials presence response structure: `{"credentials": {"groq_api_key": "set", "openai_api_key": "not set", "openrouter_api_key": "not set"}}`
- Deep merge implementation: `dict1 | dict2` (Python 3.9+) works for shallow; use a recursive helper for nested dicts, then pass the merged result to `AppConfig.model_validate(merged, strict=False)`.
- PATCH /settings excludes the `feeds` key: strip it from the incoming payload before merging, or let Pydantic's `extra='forbid'` handle it (since `AppConfig` doesn't have a `feeds` field that can be patched separately — actually `AppConfig.feeds` exists, so strip explicitly).
- Feed duplicate check: compare incoming `title` against all existing `feed.title` values (case-sensitive match); return 409 if duplicate found.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 4-Config-Feed-Management*
*Context gathered: 2026-05-17*
