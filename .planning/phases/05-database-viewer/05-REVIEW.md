---
phase: 05-database-viewer
reviewed: 2026-05-19T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - api/routes/db.py
  - api/server.py
  - components/pipeline.py
  - database/connection.py
  - database/cost_tracking_store.py
  - tests/test_api_db.py
  - tests/test_cost_tracking_store.py
  - tests/test_database_connection.py
findings:
  critical: 0
  warning: 0
  info: 2
  total: 2
status: fixed
---

# Phase 05: Code Review Report

**Reviewed:** 2026-05-19T00:00:00Z
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

This phase adds read-only DB viewer endpoints (`GET /api/v1/db/episodes|transcriptions|ads|costs`) backed by a new `Database` async context manager, a `CostTrackingStore`, and a suite of integration tests. The overall architecture is sound — parameterised queries throughout, clean context manager usage, and good test coverage.

Three critical issues were found: the read-path in `api/routes/db.py` opens a **write-capable** connection (violating the CLAUDE.md constraint against sharing the pipeline's connection), a negative `limit` value slips through validation and becomes a SQLite no-op that silently returns zero rows, and the `Database.__aenter__` method applies schema migrations inside a shared write-capable connection that is also handed to read endpoints. Additionally, four warnings cover a config-file re-read on every request, unguarded `None` title/podcast passed to `slugify`, a missing `_is_complete` guard for a `None` title, and an f-string SQL injection surface in a test helper.

---

## Critical Issues

### CR-01: Read endpoints open a write-capable connection — violates CLAUDE.md shared-connection constraint

**File:** `api/routes/db.py:116`, `api/routes/db.py:143`, `api/routes/db.py:168`, `api/routes/db.py:206`

**Issue:** Every DB handler calls `async with Database(db_path) as db:` which opens the same `aiosqlite` connection class used by the write pipeline, applies migrations (`ALTER TABLE`), and commits on every open. CLAUDE.md explicitly states: *"Never share the aiosqlite connection between the pipeline and DB read handlers — open a dedicated read-only connection with WAL mode in the API layer."* The current implementation opens a fully writable connection (no `PRAGMA journal_mode=WAL`, no `uri=True` with `mode=ro`), so concurrent reads during a pipeline write are not safely isolated and the migration code runs on every API request, which is both wasteful and risky for concurrent access.

**Fix:**

```python
# database/connection.py — add a lightweight read-only variant:

class ReadOnlyDatabase:
    """Read-only async context manager for the API layer.

    Opens in WAL mode via URI so concurrent reads never block the writer.
    Does NOT run migrations.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self.conn: aiosqlite.Connection

    async def __aenter__(self) -> "ReadOnlyDatabase":
        uri = f"file:{self._db_path}?mode=ro"
        self.conn = await aiosqlite.connect(uri, uri=True)
        await self.conn.execute("PRAGMA journal_mode=WAL")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.conn.close()
```

Then in `api/routes/db.py` replace every `async with Database(db_path) as db:` with
`async with ReadOnlyDatabase(db_path) as db:`.

---

### CR-02: Negative `limit` silently returns zero rows

**File:** `api/routes/db.py:91`

**Issue:** The validation is:
```python
limit = min(int(request.rel_url.query.get("limit", 50)), 200)
```
When the caller passes `limit=-1`, `int("-1")` succeeds, `min(-1, 200)` returns `-1`, and the query is executed as `LIMIT -1 OFFSET 0` — which in SQLite means return *all* rows (unlimited). A caller passing `limit=-9999` therefore bypasses the 200-row cap entirely and can dump the entire episodes table.

**Fix:**

```python
try:
    limit = int(request.rel_url.query.get("limit", 50))
    offset = int(request.rel_url.query.get("offset", 0))
except ValueError:
    raise web.HTTPBadRequest(
        text='{"error": "offset and limit must be integers"}',
        content_type="application/json",
    ) from None

if not (1 <= limit <= 200) or offset < 0:
    raise web.HTTPBadRequest(
        text='{"error": "limit must be 1–200 and offset must be >= 0"}',
        content_type="application/json",
    )
```

---

### CR-03: `Database.__aenter__` runs DDL migrations on every API request

**File:** `database/connection.py:160-176`

**Issue:** The four `ALTER TABLE … ADD COLUMN` migrations are wrapped in `contextlib.suppress(aiosqlite.OperationalError)` and run every time any code calls `async with Database(db_path)`. When the API layer opens a connection per request (four endpoints, potentially hundreds of concurrent calls), every single request executes four `ALTER TABLE` statements and then a `COMMIT`. SQLite serialises writers, so this creates unnecessary write locks that block the pipeline and each other. More critically, if CR-01 is fixed and a `ReadOnlyDatabase` class is introduced, those migrations must **never** run inside a read-only connection — the current design makes it impossible to separate them.

The migrations should be split out into an explicit `Database.migrate()` method (or applied once during startup in `main.py`), not re-applied on every `__aenter__`.

**Fix:**

```python
# database/connection.py
async def __aenter__(self) -> Self:
    """Open the database connection and apply the base schema only."""
    self._db_path.parent.mkdir(parents=True, exist_ok=True)
    self.conn = await aiosqlite.connect(self._db_path)
    await self.conn.execute("PRAGMA journal_mode=WAL")
    await self.conn.execute("PRAGMA foreign_keys = ON")
    await self._apply_schema()
    return self

async def _apply_schema(self) -> None:
    """Create tables and indexes, run column migrations exactly once."""
    # ... CREATE TABLE IF NOT EXISTS statements ...
    # ... ALTER TABLE migrations ...
    await self.conn.commit()
```

Then call `await db.migrate()` once in `main.py` / `serve()` before the API starts accepting requests.

---

## Warnings

### WR-01: Config YAML is re-read and re-parsed on every feed-filtered request

**File:** `api/routes/db.py:102-105`, `api/routes/db.py:199-202`

**Issue:** Both `get_episodes` and `get_costs` open and parse `config.yaml` on every request that supplies a `?feed=` query parameter:

```python
with config_path.open() as f:
    raw = yaml.safe_load(f)
cfg = AppConfig.model_validate(raw)
```

`yaml.safe_load` + `AppConfig.model_validate` run on every request. Under load, this is repeated synchronous file I/O inside an async handler. A stale config lookup is also possible if the file is being atomically replaced (as required by CLAUDE.md) mid-read.

**Fix:** Pass the live `AppConfig` (or just `cfg.feeds`) into `create_db_router` and store it, re-reading only when a `SIGHUP` or similar refresh signal is received. Since `create_app` already receives the full `Config` object, this is straightforward:

```python
def create_db_router(db_path: Path, output_dir: Path, feeds: list) -> web.RouteTableDef:
    # feeds is cfg.feeds, captured at startup
    ...
    podcast_title = _resolve_slug(feed_slug, feeds)
```

---

### WR-02: `_is_complete` is called with `title` that could be `None` at the SQLite level

**File:** `api/routes/db.py:62-71`, `api/routes/db.py:127-129`

**Issue:** The `episodes` schema declares `title TEXT NOT NULL`, but `_is_complete` receives `row_dict["title"]` which is populated from a raw `fetchall()` row without any null guard. More concretely, `slugify(None)` in `_is_complete` raises `TypeError` in `python-slugify` when `None` is passed as input. While the schema forbids `NULL`, any row inserted outside the ORM or by an old schema version could trigger this. The function has no guard:

```python
def _is_complete(row_pubdate: str | None, title: str, podcast: str, output_dir: Path) -> bool:
    if row_pubdate is None:
        return False
    pub_date = datetime.fromisoformat(row_pubdate).astimezone()
    ...
    feed_slug = slugify(podcast)   # crashes if podcast is None
    title_slug = slugify(title)    # crashes if title is None
```

**Fix:**

```python
def _is_complete(row_pubdate: str | None, title: str | None, podcast: str | None, output_dir: Path) -> bool:
    if row_pubdate is None or title is None or podcast is None:
        return False
    ...
```

---

### WR-03: `limit=0` is accepted and returns an empty but successful response

**File:** `api/routes/db.py:91`

**Issue:** `min(int("0"), 200)` produces `0`. SQLite `LIMIT 0` returns zero rows. The caller receives HTTP 200 with an empty list, which is indistinguishable from "no episodes in the database." This is a usability bug that can mask real data. (This is a separate concern from CR-02's negative-limit issue.)

**Fix:** Enforce `limit >= 1` in the same bounds check recommended for CR-02.

---

### WR-04: `_table_column_names` helper in tests uses an f-string SQL injection surface

**File:** `tests/test_database_connection.py:138`

**Issue:**

```python
async def _table_column_names(db_path: Path, table: str) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(f"PRAGMA table_info({table})")
```

`table` is interpolated directly into the SQL string. While this is a test helper and the call sites all pass hard-coded string literals today, `PRAGMA table_info` does not accept bound parameters (`?`), so future callers that pass user-controlled or variable table names could be exploited. In test code this is low severity, but it also trains a "f-string in SQL is fine" pattern that is dangerous to copy into production.

**Fix:** Document the limitation with a comment, or use a whitelist:

```python
_KNOWN_TABLES = frozenset({
    "episodes", "episode_audio_metadata", "transcriptions",
    "transcription_segments", "cost_tracking", "topic_extractions",
    "ad_segments", "ad_detection_runs",
})

async def _table_column_names(db_path: Path, table: str) -> set[str]:
    assert table in _KNOWN_TABLES, f"unexpected table name: {table!r}"
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(f"PRAGMA table_info({table})")  # noqa: S608
```

---

## Info

### IN-01: `database/connection.py` docstring says "Only the Pipeline should instantiate this class" — now inaccurate

**File:** `database/connection.py:129`

**Issue:** The class docstring reads: *"Only the Pipeline should instantiate this class."* With the addition of the DB viewer routes, `api/routes/db.py` also instantiates `Database` directly. The docstring is misleading for future contributors.

**Fix:** Update the docstring to reflect the current usage, or remove the constraint once CR-01 is addressed (at which point the API layer will use `ReadOnlyDatabase` instead).

---

### IN-02: `get_episodes` returns `feed_slug` derived from the database value, not the URL slug

**File:** `api/routes/db.py:134`

**Issue:**

```python
row_dict["feed_slug"] = slugify(row_dict["podcast"])
```

The `feed_slug` in the response is computed from the `podcast` column stored in the DB at ingest time. If the feed's `title` in `config.yaml` changes between runs, the stored `podcast` value and the config slug diverge. The `?feed=` filter uses the *config*-derived slug (via `_resolve_slug`), but the response's `feed_slug` uses the *DB*-derived slug. This inconsistency means a client could receive rows whose `feed_slug` does not match the filter parameter they submitted.

**Fix:** This is a design-level issue. If the `podcast` column is the source of truth, document it. If the config is the source of truth, keep both values consistent by normalising at write time. At minimum, add a comment explaining the expected invariant.

---

_Reviewed: 2026-05-19T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
