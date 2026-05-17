# Phase 4: Config & Feed Management - Research

**Researched:** 2026-05-17
**Domain:** aiohttp route factories, Pydantic model round-trips, atomic YAML writes, aiosqlite per-request reads
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** `GET /api/v1/settings` returns the full `AppConfig` fields **plus** a `credentials` section showing `"set"` or `"not set"` for each provider key. Actual key values are never returned.
- **D-02:** Only the `credentials` section needs redaction. `AppConfig` has no secrets.
- **D-03:** `GET /api/v1/settings` re-reads `config.yaml` from disk on every request. No caching. No `pending_restart` field.
- **D-04:** `PATCH /api/v1/settings` is NOT blocked during active pipeline runs.
- **D-05:** `PATCH /api/v1/settings` uses deep merge — client sends only changed fields; server merges into current on-disk config and validates through Pydantic.
- **D-06:** The `feeds` key is excluded from `PATCH /api/v1/settings`. Feed CRUD is handled exclusively by `/api/v1/feeds/*`.
- **D-07:** Unknown keys in the `PATCH` payload → 422 Unprocessable Entity. Pydantic `extra='forbid'` enforces this.
- **D-08:** Feed slug derived as `slugify(feed.title)`. No `slug` field on `FeedConfig`. Title rename changes slug — acceptable.
- **D-09:** `GET /api/v1/feeds` episode count comes from DB (`COUNT(*)` WHERE `podcast = feed.title`), not `episodes_to_keep`.
- **D-10:** `POST /api/v1/feeds` requires `title` and `url`; `enabled` defaults `true`; `episodes_to_keep` defaults to model default. Validates through `FeedConfig`. Duplicate title → 409.
- **D-11:** `PATCH /api/v1/feeds/{slug}` can update `url`, `enabled`, `episodes_to_keep`. `title` is excluded (would break slug and DB linkage).
- **D-12:** Feed write endpoints are NOT blocked during active pipeline runs.

### Claude's Discretion

- Route file organization: `api/routes/settings.py` and `api/routes/feeds.py`, following `create_X_router(deps) -> RouteTableDef` factory pattern.
- Exact temp file naming for atomic config write (e.g., `config.yaml.tmp` or `tempfile.NamedTemporaryFile` in the same directory).
- Whether to open a dedicated read-only aiosqlite connection per-request for episode counts in `GET /api/v1/feeds`, or reuse the `Database` context manager pattern from Phase 3.
- HTTP status for `DELETE /api/v1/feeds/{slug}` when slug not found — 404 (per REQUIREMENTS.md).
- Response shape for PATCH success — `200` with updated resource or `204 No Content`.

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.

</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STAT-02 | `GET /api/v1/settings` returns current config as JSON with all credential fields redacted | D-01/D-02/D-03: re-read YAML per request; redact Credentials via PROVIDER_KEY_MAP |
| STAT-03 | `PATCH /api/v1/settings` validates merged payload through Pydantic, writes atomically to `config.yaml`, returns 422 on validation failure | D-05/D-06/D-07: deep merge, strip `feeds` key, `extra='forbid'`, temp file + os.replace |
| FEED-01 | `GET /api/v1/feeds` returns all configured feeds with slug, URL, and episode count | D-08/D-09: slugify per feed, COUNT(*) from episodes table per feed title |
| FEED-02 | `POST /api/v1/feeds` adds a validated new feed entry to `config.yaml`; rejects duplicates | D-10: validate FeedConfig, duplicate title → 409, atomic write |
| FEED-03 | `PATCH /api/v1/feeds/{slug}` updates a feed's URL or per-feed settings after Pydantic validation | D-11: resolve slug, partial update excluding title, validate, atomic write |
| FEED-04 | `DELETE /api/v1/feeds/{slug}` removes a feed from `config.yaml`; returns 404 if not found | D-08/D-12: resolve slug, filter feeds list, atomic write |

</phase_requirements>

---

## Summary

Phase 4 adds six REST endpoints across two new route modules (`api/routes/settings.py`, `api/routes/feeds.py`). All six follow the already-established `create_X_router(deps) -> web.RouteTableDef` factory pattern. The two substantive technical challenges are (1) atomic config writes with Pydantic round-trip serialization and (2) threading `config_path: Path` through the call stack so route handlers can both read and write `config.yaml`.

The codebase is a strong foundation. `EpisodeStore`, `Database`, `slugify`, `AppConfig`, `FeedConfig`, and `Credentials` are all ready to use as-is. The key discovery is that `AppConfig` does **not** currently have `extra='forbid'` — this must be added to satisfy D-07 (unknown PATCH keys → 422). Verification confirms Pydantic's default allows extra keys silently.

The atomic write uses stdlib only: `yaml.dump(cfg.model_dump(mode='json'), ...)` → temp file in same directory → `os.replace()`. No `aiofiles` dependency exists or is needed — the write is small and sync; wrapping with `asyncio.to_thread` is optional but appropriate for the async codebase.

**Primary recommendation:** Add `model_config = ConfigDict(extra='forbid')` to `AppConfig`, then implement settings and feeds routers with per-request YAML reads and `os.replace()` atomic writes.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Settings read (GET /api/v1/settings) | API/Backend | — | Reads YAML from filesystem, redacts credentials; no client-side role |
| Settings write (PATCH /api/v1/settings) | API/Backend | Filesystem | Validates through Pydantic, writes YAML atomically |
| Feed CRUD (GET/POST/PATCH/DELETE /api/v1/feeds) | API/Backend | DB (read-only count) | Config writes are YAML; episode counts are a DB read |
| Episode count query | DB/Storage | — | COUNT(*) WHERE podcast=title via aiosqlite per-request connection |
| Slug derivation | API/Backend | — | Pure function `slugify(title)` — no storage layer involvement |
| Config file path flow | CLI Layer → API Layer | — | `args.config` → `serve()` → `create_app()` → route factories |

---

## Standard Stack

### Core (all already in project dependencies)
[VERIFIED: codebase grep]

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `aiohttp` | `>=3` | Route handlers, `web.RouteTableDef`, `web.json_response`, HTTP exceptions | Project's API framework |
| `pydantic` | `>=2` | `AppConfig.model_validate()`, `FeedConfig.model_validate()`, `model_dump(mode='json')` | Project-wide validation layer |
| `pydantic-settings` | `>=2` | `Credentials()` reads env vars; presence check via `getattr` | Existing credential model |
| `python-slugify` | `>=8` | `slugify(feed.title)` — identical call already in `control.py` | Existing slug algorithm |
| `pyyaml` | `>=6` | `yaml.safe_load()` for reading, `yaml.dump()` for writing config | Existing config format |
| `aiosqlite` | `>=0.22.1` | Per-request async SQLite reads for episode counts | Existing DB layer |

### Supporting (stdlib — no install needed)

| Module | Purpose |
|--------|---------|
| `os` | `os.replace()` for atomic file swap |
| `tempfile` | `NamedTemporaryFile(dir=config_path.parent, delete=False)` for temp file in same filesystem |
| `asyncio` | `asyncio.to_thread()` for sync YAML write in async context |
| `pathlib.Path` | `config_path` type throughout |

**Installation:** No new packages required. All dependencies are already declared in `pyproject.toml`.

---

## Package Legitimacy Audit

No new external packages are introduced in this phase. All libraries used are existing project dependencies confirmed by `pyproject.toml`. [VERIFIED: codebase grep]

---

## Architecture Patterns

### System Architecture Diagram

```
HTTP Client
    │
    ▼ GET/PATCH /api/v1/settings
    │ GET/POST/PATCH/DELETE /api/v1/feeds/{slug}
    │
    ▼
aiohttp Request Handler (api/routes/settings.py, api/routes/feeds.py)
    │                              │
    ├──► yaml.safe_load(config_path)   ├──► Database(db_path) [per-request]
    │    AppConfig.model_validate()    │    COUNT(*) WHERE podcast=title
    │                                  └──► episode_count per feed
    │
    ├──► Deep merge (recursive dict merge)
    │    AppConfig.model_validate(merged)  [validation gate]
    │
    └──► NamedTemporaryFile (same dir as config.yaml)
         yaml.dump(cfg.model_dump(mode='json'), tmp)
         os.replace(tmp_path, config_path)          [atomic swap]
         └──► web.json_response(updated_resource)
```

### Recommended Project Structure

```
api/
├── routes/
│   ├── health.py         # existing
│   ├── events.py         # existing
│   ├── control.py        # existing
│   ├── settings.py       # NEW — STAT-02, STAT-03
│   └── feeds.py          # NEW — FEED-01, FEED-02, FEED-03, FEED-04
```

### Pattern 1: Route Factory with config_path

All new routes receive `config_path: Path` as their factory argument (not a live `Config` object). They read YAML fresh on every request, consistent with D-03 and D-12.

```python
# Source: api/routes/control.py (established pattern, adapted)
def create_settings_router(config_path: Path) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/settings")
    async def get_settings(_request: web.Request) -> web.Response:
        with config_path.open() as f:
            raw = yaml.safe_load(f)
        cfg = AppConfig.model_validate(raw)
        creds = Credentials()
        return web.json_response({
            **cfg.model_dump(mode="json"),
            "credentials": {
                field: ("set" if getattr(creds, field) else "not set")
                for field in PROVIDER_KEY_MAP.values()
            },
        })

    return routes
```

**Key detail:** `cfg.model_dump(mode="json")` converts `Path` fields to strings — required for `json_response`. [VERIFIED: runtime test]

### Pattern 2: Atomic Config Write

```python
# Source: CLAUDE.md mandate + stdlib docs
import os
import tempfile
import yaml

async def _write_config_atomic(config_path: Path, cfg: AppConfig) -> None:
    data = cfg.model_dump(mode="json")
    # NamedTemporaryFile in the same directory guarantees same filesystem → os.replace is atomic
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=config_path.parent,
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    ) as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        tmp_path = f.name
    os.replace(tmp_path, config_path)
```

Use `asyncio.to_thread(_write_config_atomic_sync, ...)` if wrapping in async context. [ASSUMED] — synchronous write is safe for a small YAML file; `asyncio.to_thread` avoids blocking the event loop.

### Pattern 3: Deep Merge for PATCH

```python
# Source: Python 3.9+ dict merge docs [CITED: docs.python.org/3/library/stdtypes.html#mapping-types-dict]
def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge patch into base. Lists are replaced, not extended."""
    result = dict(base)
    for key, val in patch.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result
```

Usage for PATCH /settings:
1. Read YAML → `base_raw` dict
2. Strip `feeds` key from incoming payload (D-06)
3. `merged = _deep_merge(base_raw, payload)`
4. `AppConfig.model_validate(merged)` — raises `ValidationError` on bad data → convert to 422
5. Atomic write

### Pattern 4: Slug Resolver (already in control.py — reuse)

```python
# Source: api/routes/control.py:_resolve_slug (existing)
from slugify import slugify

def _resolve_slug(slug: str, feeds: list[FeedConfig]) -> FeedConfig | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed
    return None
```

The Phase 3 implementation returns just the title; for feeds routes, returning the full `FeedConfig` object is more useful.

### Pattern 5: Per-Request DB Read (episode counts)

```python
# Source: api/routes/control.py:skip_episode_handler (established pattern)
from database.connection import Database

async with Database(db_path) as db:
    async with db.conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE podcast = ?", (feed.title,)
    ) as cursor:
        row = await cursor.fetchone()
    count = row[0] if row else 0
```

No WAL mode pragma is set in `Database.__aenter__` — the connection defaults to SQLite's default journal mode. Since these are reads concurrent with a potentially-writing pipeline, this is a known limitation accepted by the project. [ASSUMED] — adding `PRAGMA journal_mode=WAL` would improve read concurrency but is out of scope for this phase.

### Pattern 6: `extra='forbid'` on AppConfig

D-07 requires unknown PATCH payload keys → 422. Currently `AppConfig` has no `model_config`, so extra keys are silently ignored. Must add:

```python
# Source: pydantic v2 docs [CITED: docs.pydantic.dev/latest/concepts/models/#extra-fields]
from pydantic import BaseModel, ConfigDict

class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ... existing fields unchanged
```

This is a targeted one-line addition to `config/config_loader.py`. After adding, existing tests must still pass — `AppConfig.model_validate(raw)` with a valid YAML will still work; only `model_validate` with unknown top-level keys will now raise `ValidationError`.

### Pattern 7: `create_app` signature change

`create_app` must accept `config_path: Path` and store it in `app["config_path"]`. `serve()` must receive and forward it from `main()`:

```python
# main.py: serve() call
await serve(args.host, args.port, cfg, args.config)   # args.config is already a Path

# api/server.py: serve() signature
async def serve(host: str, port: int, config: Config, config_path: Path) -> None:
    ...
    app = create_app(event_bus, start_time, run_state, config, config_path)

# api/server.py: create_app() signature
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
    config_path: Path,
) -> web.Application:
    app["config_path"] = config_path
    ...
    app.add_routes(create_settings_router(config_path))
    app.add_routes(create_feeds_router(config_path, config.app.paths.data_dir))
```

### Anti-Patterns to Avoid

- **Passing live `Config` to route handlers for settings/feed writes:** `Config` is constructed once at startup; route handlers that need to write must read YAML fresh, not use the in-memory object.
- **`yaml.safe_dump` with `Path` objects:** `PathsConfig` fields are `pathlib.Path`; always use `model_dump(mode='json')` before YAML serialization to get strings. [VERIFIED: runtime test]
- **Writing to `config.yaml` directly (no temp file):** A crash mid-write corrupts the config. Always write to a temp file in the same directory and use `os.replace()`. [CITED: CLAUDE.md]
- **Sharing the pipeline's aiosqlite connection:** The CLAUDE.md constraint is absolute — open a new `Database` context per route request.
- **`AppConfig.feeds` field in PATCH /settings payload:** The `feeds` key must be stripped from the PATCH body before merging. If left in, `model_validate` with `extra='forbid'` would still reject it because... wait, actually `feeds` IS a valid `AppConfig` field. The correct guard is to explicitly strip it from the payload: `payload.pop('feeds', None)`.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON validation of PATCH body | Custom field parsing | `AppConfig.model_validate()` with `extra='forbid'` | Handles nested models, type coercion, field-level errors automatically |
| Slug generation | Custom URL-safe string transform | `slugify()` from `python-slugify` | Already used; identical output to FeedPublisher |
| Episode count per feed | Custom query builder | Inline `COUNT(*) WHERE podcast=?` with `Database` context manager | One-liner, existing pattern |
| YAML → JSON conversion | Custom serializer | `model_dump(mode='json')` | Handles Path → str, all Pydantic types |
| Safe YAML write | Custom lock/write | `NamedTemporaryFile` + `os.replace()` | POSIX atomic; same filesystem guarantees atomicity |

---

## Common Pitfalls

### Pitfall 1: `model_dump()` returns `Path` objects — YAML serializer crashes
**What goes wrong:** `yaml.dump(cfg.model_dump())` raises `RepresenterError` for `pathlib.PosixPath`.
**Why it happens:** `model_dump()` preserves Python types. `PathsConfig` fields are `Path`.
**How to avoid:** Always use `cfg.model_dump(mode='json')` before serializing.
**Warning signs:** `yaml.representer.RepresenterError: cannot represent an object` in logs.
[VERIFIED: runtime test]

### Pitfall 2: `feeds` key not stripped before PATCH /settings merge
**What goes wrong:** Client sends `{"feeds": [...]}` in a PATCH /settings; the request modifies feeds through the settings endpoint, bypassing the feeds CRUD contract (D-06).
**Why it happens:** `feeds` is a valid `AppConfig` field so Pydantic accepts it.
**How to avoid:** Explicitly `payload.pop('feeds', None)` before the deep merge.
**Warning signs:** Feed list changes when calling PATCH /settings.

### Pitfall 3: `AppConfig` missing `extra='forbid'` — unknown keys silently ignored
**What goes wrong:** PATCH /settings with typos (e.g., `{"ad_detecgion": {"min_confidence": 0.9}}`) succeeds with 200 but the change is silently lost.
**Why it happens:** Pydantic v2 defaults to `extra='ignore'`.
**How to avoid:** Add `model_config = ConfigDict(extra='forbid')` to `AppConfig`.
**Warning signs:** No existing tests catch this — must be added.
[VERIFIED: runtime test confirms current behavior allows extra keys]

### Pitfall 4: `create_app` signature change breaks existing tests
**What goes wrong:** All existing `test_api_*.py` files call `create_app(EventBus(), time.monotonic(), run_state, config)` with 4 args; adding `config_path` as 5th breaks them.
**Why it happens:** Positional argument addition.
**How to avoid:** Make `config_path` a keyword argument with a default: `config_path: Path = Path("config.yaml")`, or update all test call sites (better: update all call sites for correctness).
**Warning signs:** `TypeError: create_app() takes 4 positional arguments but 5 were given` in test run.

### Pitfall 5: FeedConfig validation rejects `title`-only PATCH inputs
**What goes wrong:** PATCH /feeds/{slug} with `{"url": "..."}` fails `FeedConfig.model_validate()` because `title` is required.
**Why it happens:** `FeedConfig.title` has no default — it's a required field.
**How to avoid:** Partial update pattern — load the existing feed from config, build a merged dict with the patch fields, then `model_validate` the merged result. Don't validate the raw patch dict directly.
**Warning signs:** 422 on valid PATCH requests with partial fields.

### Pitfall 6: Concurrent PATCH requests corrupt config.yaml
**What goes wrong:** Two simultaneous PATCH requests both read the YAML, both modify it, and the slower write overwrites the faster write's change.
**Why it happens:** No file-level locking in the atomic write pattern.
**How to avoid:** [ASSUMED] — This is a known limitation for a single-user local tool; the CONTEXT.md does not require locking. If needed, an `asyncio.Lock` stored in `app` dict can serialize writes. Out of scope for this phase.

---

## Code Examples

### GET /api/v1/settings — full handler shape

```python
# Source: CONTEXT.md D-01/D-02/D-03 + config_loader.py patterns
@routes.get("/api/v1/settings")
async def get_settings(_request: web.Request) -> web.Response:
    with config_path.open() as f:
        raw = yaml.safe_load(f)
    cfg = AppConfig.model_validate(raw)
    creds = Credentials()
    body = cfg.model_dump(mode="json")
    body["credentials"] = {
        field: ("set" if getattr(creds, field) else "not set")
        for field in PROVIDER_KEY_MAP.values()
    }
    return web.json_response(body)
```

### PATCH /api/v1/settings — validation + atomic write

```python
# Source: CONTEXT.md D-05/D-06/D-07
@routes.patch("/api/v1/settings")
async def patch_settings(request: web.Request) -> web.Response:
    payload = await request.json()
    payload.pop("feeds", None)  # D-06: feeds managed by /api/v1/feeds/*
    with config_path.open() as f:
        base_raw = yaml.safe_load(f)
    merged = _deep_merge(base_raw, payload)
    try:
        cfg = AppConfig.model_validate(merged)
    except ValidationError as exc:
        raise web.HTTPUnprocessableEntity(
            text=exc.json(), content_type="application/json"
        ) from exc
    await asyncio.to_thread(_write_config_sync, config_path, cfg)
    return web.json_response(cfg.model_dump(mode="json"))
```

### GET /api/v1/feeds — with episode counts

```python
# Source: CONTEXT.md D-08/D-09 + database/connection.py pattern
@routes.get("/api/v1/feeds")
async def get_feeds(_request: web.Request) -> web.Response:
    with config_path.open() as f:
        raw = yaml.safe_load(f)
    cfg = AppConfig.model_validate(raw)
    result = []
    async with Database(db_path) as db:
        for feed in cfg.feeds:
            async with db.conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE podcast = ?", (feed.title,)
            ) as cursor:
                row = await cursor.fetchone()
            count = row[0] if row else 0
            result.append({
                "slug": slugify(feed.title),
                "title": feed.title,
                "url": feed.url,
                "enabled": feed.enabled,
                "episodes_to_keep": feed.episodes_to_keep,
                "episode_count": count,
            })
    return web.json_response(result)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Pydantic v1 `Config` inner class | Pydantic v2 `model_config = ConfigDict(...)` | Pydantic v2.0 | `extra='forbid'` now set via `ConfigDict`, not inner class |
| `dict()` on Pydantic model | `model_dump(mode='json')` | Pydantic v2.0 | `mode='json'` essential for Path→str conversion |
| `model_dict` direct write to YAML | `model_dump(mode='json')` first | Pydantic v2.0 | Prevents `RepresenterError` for complex types |

**Deprecated/outdated:**
- `model.dict()`: Replaced by `model.model_dump()` in Pydantic v2. Do not use.
- `model.json()`: Replaced by `model.model_dump_json()` in Pydantic v2. Do not use directly for YAML write — use `model_dump(mode='json')` instead.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Synchronous YAML write wrapped in `asyncio.to_thread` is appropriate; blocking the loop on a small file write is acceptable without it | Architecture Patterns / Pattern 2 | Event loop blocked during write; acceptable for single-user tool but could be cleaned up |
| A2 | No file-level locking needed for concurrent PATCH requests | Common Pitfalls / Pitfall 6 | Concurrent writes could lose data; mitigated by single-user context |
| A3 | No WAL pragma needed on per-request read connections | Architecture Patterns / Pattern 5 | Pipeline writes could block read queries; unlikely given write frequency |

---

## Open Questions

1. **Should `GET /api/v1/feeds` open one `Database` context for all feeds, or one per feed?**
   - What we know: The existing `Database` context manager opens/closes the connection on entry/exit. Opening once for all feeds in the loop is more efficient.
   - What's unclear: Whether the planner should structure it as one `async with Database` block with N queries inside, or N separate blocks.
   - Recommendation: One `Database` context wrapping all per-feed COUNT queries — matches the pipeline's own pattern of keeping one connection open per run.

2. **`config_path` in `create_app` — default value or required argument?**
   - What we know: 12 existing test files call `create_app()` without `config_path`. Adding it as required breaks all of them.
   - What's unclear: Whether to update all test call sites or use a default.
   - Recommendation: Update all call sites in `test_api_server.py`, `test_api_health.py`, `test_api_control.py`, etc. to pass `tmp_path / "config.yaml"` (pytest fixture). This makes tests explicit about config path. The alternative (a default) masks real test gaps.

---

## Environment Availability

Step 2.6: SKIPPED — no new external dependencies. All required tools (`python-slugify`, `pyyaml`, `aiosqlite`, `pydantic`, `aiohttp`) are confirmed present in `pyproject.toml` and installed in the project virtualenv.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio 0.24 |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_api_settings.py tests/test_api_feeds.py -x` |
| Full suite command | `uv run pytest --cov=.` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STAT-02 | GET /api/v1/settings returns AppConfig fields + credentials presence | unit | `uv run pytest tests/test_api_settings.py -x` | No — Wave 0 |
| STAT-02 | credentials section shows "set"/"not set", never actual values | unit | `uv run pytest tests/test_api_settings.py::TestGetSettings -x` | No — Wave 0 |
| STAT-02 | re-reads disk on every request (post-PATCH response reflects change) | unit | `uv run pytest tests/test_api_settings.py::TestGetSettings::test_reflects_disk_change -x` | No — Wave 0 |
| STAT-03 | PATCH merges payload into existing config (deep merge) | unit | `uv run pytest tests/test_api_settings.py::TestPatchSettings -x` | No — Wave 0 |
| STAT-03 | PATCH excludes feeds key from merge | unit | `uv run pytest tests/test_api_settings.py::TestPatchSettings::test_feeds_key_excluded -x` | No — Wave 0 |
| STAT-03 | PATCH unknown key → 422 | unit | `uv run pytest tests/test_api_settings.py::TestPatchSettings::test_unknown_key_returns_422 -x` | No — Wave 0 |
| STAT-03 | PATCH writes atomically (temp file + os.replace) | unit | `uv run pytest tests/test_api_settings.py::TestPatchSettings::test_atomic_write -x` | No — Wave 0 |
| FEED-01 | GET /api/v1/feeds returns slugs, URLs, episode counts | unit | `uv run pytest tests/test_api_feeds.py::TestGetFeeds -x` | No — Wave 0 |
| FEED-01 | Episode count is from DB, not episodes_to_keep | unit | `uv run pytest tests/test_api_feeds.py::TestGetFeeds::test_episode_count_from_db -x` | No — Wave 0 |
| FEED-02 | POST /api/v1/feeds adds feed and writes config | unit | `uv run pytest tests/test_api_feeds.py::TestPostFeed -x` | No — Wave 0 |
| FEED-02 | POST duplicate title → 409 | unit | `uv run pytest tests/test_api_feeds.py::TestPostFeed::test_duplicate_title_returns_409 -x` | No — Wave 0 |
| FEED-03 | PATCH /api/v1/feeds/{slug} updates url/enabled/episodes_to_keep | unit | `uv run pytest tests/test_api_feeds.py::TestPatchFeed -x` | No — Wave 0 |
| FEED-03 | PATCH unknown slug → 404 | unit | `uv run pytest tests/test_api_feeds.py::TestPatchFeed::test_unknown_slug_returns_404 -x` | No — Wave 0 |
| FEED-04 | DELETE /api/v1/feeds/{slug} removes feed from config | unit | `uv run pytest tests/test_api_feeds.py::TestDeleteFeed -x` | No — Wave 0 |
| FEED-04 | DELETE unknown slug → 404 | unit | `uv run pytest tests/test_api_feeds.py::TestDeleteFeed::test_unknown_slug_returns_404 -x` | No — Wave 0 |
| STAT-03 | `AppConfig.extra='forbid'` rejects unknown fields | unit | `uv run pytest tests/test_config_loader.py -x` | Partial — extend existing |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_api_settings.py tests/test_api_feeds.py -x`
- **Per wave merge:** `uv run pytest --cov=.`
- **Phase gate:** Full suite green + 100% coverage before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_api_settings.py` — covers STAT-02, STAT-03
- [ ] `tests/test_api_feeds.py` — covers FEED-01, FEED-02, FEED-03, FEED-04
- [ ] Extend `tests/test_config_loader.py` — add `test_extra_key_raises_validation_error` after `extra='forbid'` added
- [ ] Update existing test call sites: `test_api_health.py`, `test_api_control.py`, `test_api_server.py` — add `config_path` arg to all `create_app()` calls

---

## Security Domain

> `security_enforcement` not set in `.planning/config.json` — treated as enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth in v1 (SEC-01 deferred) |
| V3 Session Management | no | Stateless REST endpoints |
| V4 Access Control | no | No auth in v1 |
| V5 Input Validation | yes | Pydantic `model_validate()` with `extra='forbid'`; `ValidationError` → 422 |
| V6 Cryptography | no | No crypto; credential values are never returned in responses |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Credential value leakage in GET /settings | Information Disclosure | Presence check via `getattr(creds, field)` — never return the value; confirmed in response shape |
| PATCH payload with `feeds` key modifying feed list via settings endpoint | Tampering | Explicit `payload.pop('feeds', None)` before merge |
| PATCH payload with unknown keys silently ignored | Tampering | `AppConfig.extra='forbid'` → ValidationError → 422 |
| Config file write race (two concurrent PATCHes) | Tampering | Accepted limitation for single-user tool; `os.replace()` atomicity ensures no partial-write corruption |

---

## Sources

### Primary (HIGH confidence)
- Codebase direct reads — `config/config_loader.py`, `api/server.py`, `api/routes/control.py`, `api/routes/health.py`, `database/connection.py`, `database/episode_store.py`, `main.py`, `tests/test_api_control.py`, `tests/test_api_health.py`
- Runtime verification — `uv run python` sessions confirming `model_dump(mode='json')` Path→str conversion and current `AppConfig` extra-key behavior

### Secondary (MEDIUM confidence)
- [CITED: docs.pydantic.dev/latest/concepts/models/#extra-fields] — Pydantic v2 `extra='forbid'` via `ConfigDict`
- [CITED: docs.python.org/3/library/os.html#os.replace] — `os.replace()` atomic semantics on POSIX

### Tertiary (LOW confidence)
None — all critical claims verified against codebase or official docs.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all packages verified in `pyproject.toml` and runtime
- Architecture: HIGH — patterns directly observed in existing route files
- Pitfalls: HIGH — Pitfall 3 (extra keys) and Pitfall 1 (Path serialization) confirmed by runtime tests; others derived from codebase patterns
- Test plan: HIGH — mirrors established pattern from Phase 3 test files

**Research date:** 2026-05-17
**Valid until:** 2026-06-17 (stable stack)
