# Phase 5: Database Viewer - Research

**Researched:** 2026-05-19
**Domain:** aiohttp REST endpoints over aiosqlite — read-only DB viewer with schema migration
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Schema Migration (cost_tracking)**
- D-01: `cost_tracking` table must gain a nullable `guid TEXT REFERENCES episodes(guid)` column via `ALTER TABLE`. Existing rows get `NULL`. This is a prerequisite for DB-04 per-episode cost breakdown.
- D-02: `CostTrackingStore.save_cost()` gains an optional `guid: str | None = None` parameter. All pipeline call sites that know the episode GUID at cost-recording time must pass it.

**Episode List — DB-01**
- D-03: Response includes the full episodes row (all ~20 columns) plus a derived `pipeline_state` field. `feed_slug` is derived on the fly as `slugify(episode.podcast)` — same function used in `FeedPublisher` and `feeds.py`.
- D-04: `pipeline_state` is derived in a single SQL query with LEFT JOINs (not N sub-queries). Priority ladder (highest wins): `skipped` (episodes.skipped=1) → `complete` (output file exists on filesystem) → `processed` (has ad_detection_runs row) → `transcribed` (has transcriptions row) → `downloaded` (has episode_audio_metadata row) → `pending`.
- D-05: `complete` is determined by checking for the episode's output file on the filesystem. `create_db_router` receives `output_dir: Path` from `create_app`. The executor must derive the exact output file naming convention from the pipeline codebase.
- D-06: Default sort: `pubdate DESC`, NULL pubdates sort last. Default `limit=50`, `offset=0`, max enforced `limit=200`. The `?feed={slug}` filter maps slug back to feed title via the same `slugify()` reverse lookup pattern (iterate feeds config, match slug).
- D-07: `pubdate` is the only timestamp returned — no pipeline processing timestamps exist in the DB schema and none will be added in this phase.

**Transcription Detail — DB-02**
- D-08: Response shape: `{guid, text, segments: [{start, end, text}, ...]}`. `start`/`end` are the segment timestamps from `transcription_segments`. Returns 404 if no transcription row exists for the GUID.

**Ad Detection Detail — DB-03**
- D-09: Response shape: `{guid, detected: bool, segments: [{start_ms, end_ms, confidence, sponsor, ad_topic}, ...]}`. `detected` is `true` when an `ad_detection_runs` row exists (even if segments is empty). `indices` column is an internal implementation detail and is NOT returned. Returns 404 if no `ad_detection_runs` row exists for the GUID.

**Cost Aggregates — DB-04**
- D-10: Response shape: `{total: float, by_model: [{provider, model, cost}, ...], by_episode: [{guid, cost}, ...]}`. `by_episode` entries with NULL guid are omitted from `by_episode` (included in total and `by_model` only). `?feed={slug}` filters all three sections to episodes belonging to the matched feed (join via `episodes.podcast`).

### Claude's Discretion

- Route organization: `api/routes/db.py` with `create_db_router(db_path, output_dir)` factory following the established pattern.
- Whether `pipeline_state` derivation uses a single complex LEFT JOIN or a CTE — executor's choice based on readability.
- Exact SQL for `pubdate DESC NULLS LAST` (SQLite syntax: `ORDER BY pubdate IS NULL ASC, pubdate DESC` since SQLite doesn't support NULLS LAST natively).

### Deferred Ideas (OUT OF SCOPE)

- DB-05: Topics endpoint — `GET /api/v1/db/topics/{guid}` (topic name, hosts, show from `topic_extractions`) — deferred to v2.
- Episode ordering control — `?sort=pubdate|id` query param — not requested; pubdate DESC is the fixed default.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DB-01 | `GET /api/v1/db/episodes` — episode list with GUID, feed slug, pipeline state, pubdate; `?offset`/`?limit` pagination; `?feed={slug}` filter | LEFT JOIN SQL verified; NULLS LAST workaround verified; output file glob pattern identified in `components/pipeline.py:530` |
| DB-02 | `GET /api/v1/db/transcriptions/{guid}` — full transcription text and segments | `TranscriptionStore` query patterns confirmed in `database/transcription_store.py` |
| DB-03 | `GET /api/v1/db/ads/{guid}` — detected ad segments, confidence, cut ranges | `AdStore` query patterns confirmed in `database/ad_store.py`; `indices` column excluded |
| DB-04 | `GET /api/v1/db/costs` — LLM costs per episode and aggregates; `?feed={slug}` filter | Cost SQL with JOIN verified; unfiltered + filtered paths verified; NULL-guid handling confirmed |
</phase_requirements>

---

## Summary

Phase 5 exposes four read-only REST endpoints over the existing aiosqlite database. All work builds on the established Phase 3/4 patterns: a `create_db_router(db_path, output_dir)` factory that returns a `web.RouteTableDef`, with per-request `async with Database(db_path) as db:` connections (never shared with the pipeline). No new dependencies are required — aiohttp 3.13.5, aiosqlite 0.22.1, and python-slugify 8.0.4 are already installed.

The phase has two sub-stories: (1) a database schema migration adding a nullable `guid` column to `cost_tracking`, plus updating the three `save_cost()` call sites in `pipeline.py` to pass the episode GUID; (2) the four endpoint implementations. The migration follows the existing `contextlib.suppress(OperationalError)` pattern already used for the `skipped`, `length`, and `source_url` column additions in `database/connection.py`.

The one non-trivial design point is `pipeline_state` for DB-01: the `complete` state requires a filesystem check rather than a DB join. The output file path is derived as `output_dir / slugify(episode.podcast) / f"{pub_date_str}-{slugify(episode.title)}.*"` — a glob matching any extension. The exact pattern is confirmed from `components/pipeline.py:498-531`. All SQL queries have been prototyped and verified against an in-process SQLite instance.

**Primary recommendation:** Follow the `feeds.py` factory pattern exactly. No new libraries needed. The plan should split into two plans: Plan A (schema migration + save_cost update) and Plan B (the four endpoints + server.py wiring).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Episode list with pagination/filter | API / Backend | Database / Storage | SQL query with LEFT JOINs; pagination via LIMIT/OFFSET |
| `pipeline_state` complete check | API / Backend | — | Filesystem check in the request handler; cannot be delegated to SQL |
| Transcription detail | API / Backend | Database / Storage | Direct SQL read of `transcriptions` + `transcription_segments` |
| Ad detection detail | API / Backend | Database / Storage | Direct SQL read of `ad_detection_runs` + `ad_segments` |
| Cost aggregation | API / Backend | Database / Storage | SQL SUM/GROUP BY; feed filter via JOIN to `episodes` |
| Schema migration | Database / Storage | — | `ALTER TABLE` in `Database.__aenter__` idempotent migration block |
| GUID linkage at cost write | API / Backend (pipeline) | Database / Storage | `save_cost()` parameter extension; three call sites in `pipeline.py` |

---

## Standard Stack

### Core (all already installed — no new packages)

| Library | Installed Version | Purpose | Source |
|---------|------------------|---------|--------|
| aiohttp | 3.13.5 | HTTP server; `web.RouteTableDef`, `web.Request`, `web.json_response` | [VERIFIED: installed] |
| aiosqlite | 0.22.1 | Async SQLite driver; per-request connection via `Database` context manager | [VERIFIED: installed] |
| python-slugify | 8.0.4 | `slugify()` — feed slug derivation and reverse lookup | [VERIFIED: installed] |

No new packages needed. Phase 5 is a pure implementation phase using the existing stack.

---

## Package Legitimacy Audit

No new packages are installed in this phase. All packages are existing project dependencies verified in `pyproject.toml`.

**Packages removed due to slopcheck [SLOP] verdict:** none  
**Packages flagged as suspicious [SUS]:** none

---

## Architecture Patterns

### System Architecture Diagram

```
HTTP GET /api/v1/db/episodes?offset=0&limit=50&feed=show-a
        │
        ▼
create_db_router(db_path, output_dir)
        │
        ├── parse query params (offset, limit capped at 200, feed slug)
        │
        ├── [if ?feed] iterate config feeds → slugify(title) → match → get podcast title
        │
        ├── async with Database(db_path) as db:   ← dedicated read-only connection
        │       │
        │       └── SELECT e.*, LEFT JOIN eam, t, adr
        │           ORDER BY e.pubdate IS NULL ASC, e.pubdate DESC
        │           LIMIT ? OFFSET ?
        │
        ├── for each row: filesystem check
        │       output_dir / slugify(podcast) / f"{pub_date_str}-{title_slug}.*"
        │       Path.glob() → exists? → pipeline_state = 'complete'
        │
        └── web.json_response([...])

GET /api/v1/db/transcriptions/{guid}
        │
        ├── async with Database(db_path) as db:
        │       ├── SELECT transcription FROM transcriptions WHERE guid = ?
        │       └── SELECT start_ms, end_ms, text FROM transcription_segments WHERE guid = ?
        │
        └── 404 if no row; else json {guid, text, segments:[...]}

GET /api/v1/db/ads/{guid}
        │
        ├── async with Database(db_path) as db:
        │       ├── SELECT id FROM ad_detection_runs WHERE guid = ?  → detected flag
        │       └── SELECT start_ms, end_ms, confidence, sponsor, ad_topic FROM ad_segments WHERE guid = ?
        │
        └── 404 if no run row; else json {guid, detected, segments:[...]}

GET /api/v1/db/costs?feed=show-a
        │
        ├── [if ?feed] slug → podcast title (same reverse lookup)
        │
        ├── async with Database(db_path) as db:
        │       ├── SUM(cost)  ← total
        │       ├── GROUP BY provider, model  ← by_model
        │       └── JOIN episodes, GROUP BY guid (WHERE guid IS NOT NULL)  ← by_episode
        │
        └── json {total, by_model:[...], by_episode:[...]}
```

### Recommended Project Structure

```
api/
├── routes/
│   ├── db.py          # NEW: create_db_router(db_path, output_dir)
│   ├── feeds.py       # existing
│   ├── control.py     # existing
│   └── ...
database/
├── connection.py      # MODIFIED: add ALTER TABLE cost_tracking ADD COLUMN guid
├── cost_tracking_store.py  # MODIFIED: save_cost(cost, guid=None)
components/
└── pipeline.py        # MODIFIED: pass guid at 3 save_cost() call sites
tests/
└── test_api_db.py     # NEW: full test coverage for all 4 endpoints
```

### Pattern 1: Factory Router (established — replicate from feeds.py)

**What:** `create_db_router(db_path, output_dir)` returns a `web.RouteTableDef` with all handlers registered inside the factory closure.

**When to use:** Every route module in this project follows this pattern.

```python
# Source: api/routes/feeds.py (established pattern)
def create_db_router(db_path: Path, output_dir: Path) -> web.RouteTableDef:
    routes = web.RouteTableDef()

    @routes.get("/api/v1/db/episodes")
    async def get_episodes(request: web.Request) -> web.Response:
        async with Database(db_path) as db:
            ...
        return web.json_response(result)

    return routes
```

### Pattern 2: Per-Request Database Connection (established — same as control.py, feeds.py)

**What:** Open a fresh `Database` context manager for each request. Never cache or reuse the connection across requests.

```python
# Source: api/routes/feeds.py:73-80
async with Database(db_path) as db:
    cursor = await db.conn.execute("SELECT ...", (param,))
    row = await cursor.fetchone()
```

### Pattern 3: SQLite NULLS LAST Workaround

**What:** SQLite does not support the `NULLS LAST` clause. Use `pubdate IS NULL ASC` as the primary sort key.

**Verified against SQLite 3.50.4 (installed).**

```sql
-- Source: verified in this research session
ORDER BY e.pubdate IS NULL ASC, e.pubdate DESC
```

Result: non-NULL pubdates sort descending first; NULLs sort after all non-NULL rows.

### Pattern 4: pipeline_state via LEFT JOIN (DB-01)

**What:** A single SQL query with four LEFT JOINs determines the DB-based pipeline state. `complete` (filesystem check) is applied in Python after the query.

```sql
-- Source: verified in this research session against SQLite 3.50.4
SELECT
    e.id, e.podcast, e.guid, e.title, e.pubdate, e.skipped,
    e.url, e.description, e.explicit, e.duration, e.image_url,
    e.episode_type, e.itunes_author, e.itunes_subtitle, e.itunes_summary,
    e.content_encoded, e.link, e.author, e.itunes_title,
    e.episode_number, e.season_number, e.itunes_block, e.length, e.source_url,
    CASE
        WHEN e.skipped = 1 THEN 'skipped'
        WHEN adr.guid IS NOT NULL THEN 'processed'
        WHEN t.guid IS NOT NULL THEN 'transcribed'
        WHEN eam.guid IS NOT NULL THEN 'downloaded'
        ELSE 'pending'
    END AS pipeline_state_db
FROM episodes e
LEFT JOIN episode_audio_metadata eam ON e.guid = eam.guid
LEFT JOIN transcriptions t ON e.guid = t.guid
LEFT JOIN ad_detection_runs adr ON e.guid = adr.guid
[WHERE e.podcast = ?]
ORDER BY e.pubdate IS NULL ASC, e.pubdate DESC
LIMIT ? OFFSET ?
```

After fetching rows, the handler checks the filesystem for each row where `pipeline_state_db == 'processed'` and upgrades it to `'complete'` if the output file exists. The skipped state cannot be upgraded (skipped takes priority over everything).

### Pattern 5: Output File Existence Check (DB-01 `complete` state)

**What:** Derived directly from `components/pipeline.py:498-531`. The output file path is:

```
output_dir / slugify(episode.podcast) / f"{pub_date_str}-{slugify(episode.title)}.*"
```

The glob matches any file extension (mp3, m4a, etc.) to handle re-encoding with a changed `file_type` config.

```python
# Source: components/pipeline.py:498-531 (pipeline _process_episode)
from slugify import slugify
from pathlib import Path

pub_date_str = pub_date.astimezone().strftime("%d.%m.%Y")
title_slug = slugify(episode_title)
feed_slug = slugify(episode_podcast)
output_feed_dir = output_dir / feed_slug
existing_audio = next(
    (p for p in output_feed_dir.glob(f"{pub_date_str}-{title_slug}.*")),
    None,
)
pipeline_state = "complete" if existing_audio is not None else pipeline_state_db
```

Note: `pubdate` is stored as an ISO-8601 string in the DB. Parse it with `datetime.fromisoformat(pubdate)` before calling `.astimezone().strftime(...)`. Handle `pubdate IS NULL` — these episodes have `pipeline_state_db != 'complete'` (they cannot have been edited without a pubdate), so the filesystem check can be skipped.

### Pattern 6: Schema Migration (idempotent ALTER TABLE)

**What:** Add the migration to `Database.__aenter__` using `contextlib.suppress(OperationalError)` — same as the existing `skipped`, `length`, `source_url` column additions.

```python
# Source: database/connection.py:160-172 (established migration pattern)
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid)"
    )
await self.conn.commit()
```

### Pattern 7: Cost Aggregation SQL (DB-04)

**Two variants — filtered by feed, or unfiltered:**

```sql
-- Unfiltered total + by_model
SELECT provider, model, SUM(cost) AS cost
FROM cost_tracking
GROUP BY provider, model

SELECT SUM(cost) AS total FROM cost_tracking

-- by_episode (always omit NULL guid rows)
SELECT guid, SUM(cost) AS cost
FROM cost_tracking
WHERE guid IS NOT NULL
GROUP BY guid

-- Feed-filtered (join to episodes)
SELECT ct.provider, ct.model, SUM(ct.cost) AS cost
FROM cost_tracking ct
JOIN episodes e ON ct.guid = e.guid
WHERE e.podcast = ?
GROUP BY ct.provider, ct.model

SELECT SUM(ct.cost) AS total
FROM cost_tracking ct
JOIN episodes e ON ct.guid = e.guid
WHERE e.podcast = ?

SELECT ct.guid, SUM(ct.cost) AS cost
FROM cost_tracking ct
JOIN episodes e ON ct.guid = e.guid
WHERE e.podcast = ? AND ct.guid IS NOT NULL
GROUP BY ct.guid
```

All verified against in-process SQLite in this research session.

### Anti-Patterns to Avoid

- **Sharing the aiosqlite connection:** Never reuse the pipeline's `Database` connection in the API layer. Always open a per-request `async with Database(db_path) as db:` in every handler.
- **N+1 for pipeline_state:** Do not fire a separate SQL query per episode to check for transcriptions or ad detection runs. The LEFT JOIN in Pattern 4 handles all state levels in a single query.
- **Filesystem glob in SQL:** SQLite cannot glob the filesystem. The `complete` state check must happen in Python after the SQL query.
- **`web.run_app()`:** Forbidden by CLAUDE.md. The server already uses AppRunner + TCPSite correctly.
- **Returning `indices` from ad_segments:** The `indices` column is an internal implementation detail (a JSON array of segment indices for the LLM). Do not include it in the DB-03 response.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Pagination | Custom offset/cursor logic | SQL `LIMIT ? OFFSET ?` | Built into SQLite; proven pattern |
| Slug derivation | Custom regex/replace | `slugify()` from python-slugify | Same function used in pipeline and feeds.py — must match exactly |
| Async file I/O | Thread pool executor calls | Direct `Path.glob()` (sync) in async handler | File stat calls are fast; existing pipeline uses sync glob with `# noqa: ASYNC240` — same justification applies |
| Response serialization | Custom encoder | `web.json_response(dict)` | aiohttp handles JSON encoding |
| NULL-last ordering | Application-side sort | `ORDER BY pubdate IS NULL ASC, pubdate DESC` | Verified SQLite idiom |

---

## Runtime State Inventory

Phase 5 is not a rename/refactor phase. SKIPPED.

---

## Environment Availability

This phase is purely code changes over the existing stack. No new external dependencies.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Runtime | ✓ | 3.12.13 | — |
| aiohttp | HTTP server | ✓ | 3.13.5 | — |
| aiosqlite | DB access | ✓ | 0.22.1 | — |
| python-slugify | Slug derivation | ✓ | 8.0.4 | — |
| SQLite | Database engine | ✓ | 3.50.4 | — |

**Missing dependencies with no fallback:** none  
**Missing dependencies with fallback:** none

---

## Common Pitfalls

### Pitfall 1: `complete` state and pubdate=NULL episodes

**What goes wrong:** `datetime.fromisoformat(None)` raises `TypeError`. Handler crashes for episodes with NULL pubdate when trying to derive the output filename.

**Why it happens:** `pubdate` is a nullable column. Episodes can be inserted without a pubdate.

**How to avoid:** Before computing the output file glob, check `if row_pubdate is None: pipeline_state = pipeline_state_db; continue`. Episodes without a pubdate cannot have been edited (the audio editor writes `pub_date.astimezone().strftime(...)` — it would also crash), so they will never have a `complete` output file.

**Warning signs:** `TypeError` in the episodes handler log for any episode with NULL pubdate.

### Pitfall 2: feed filter reverse-lookup needs config

**What goes wrong:** `?feed={slug}` needs to map a slug back to a feed title (the `podcast` column in the DB). This requires reading the config file — but `create_db_router` only receives `db_path` and `output_dir` in the current design.

**How to avoid:** The factory must also receive `config_path: Path` (same as `feeds.py`). This is the canonical pattern. The handler reads the config on each request (same as `feeds.py:get_feeds` does). The signature becomes `create_db_router(db_path, output_dir, config_path)`.

**Note:** D-06 says "iterate feeds config, match slug" — this confirms that config access is needed. The CONTEXT.md's "Claude's Discretion" section says `create_db_router(db_path, output_dir)` but this omits `config_path`. The executor should add it, as there is no other way to perform the reverse slug lookup.

**Warning signs:** `?feed=show-a` filter always returns all episodes because the podcast title cannot be derived without config.

### Pitfall 3: max limit not enforced produces large responses

**What goes wrong:** A client sends `?limit=100000`. The handler fires a SELECT returning tens of thousands of rows, serializing them all.

**Why it happens:** No cap on the `limit` query parameter.

**How to avoid:** `limit = min(int(request.rel_url.query.get("limit", 50)), 200)`. D-06 mandates max 200.

**Warning signs:** Slow responses on DB-01 with large databases.

### Pitfall 4: DB-03 `detected` flag vs 404 distinction

**What goes wrong:** Handler returns 404 when `ad_detection_runs` has no row, but also sets `detected: false` on the 404 response — confusing behavior.

**Why it happens:** Misreading D-09: "Returns 404 if no `ad_detection_runs` row exists for the GUID."

**How to avoid:** The distinction is: no row in `ad_detection_runs` → 404. Row exists but `ad_segments` is empty → 200 with `detected: true, segments: []`. Do not conflate these two cases.

### Pitfall 5: Cost filter total includes unfiltered NULL-guid rows

**What goes wrong:** The unfiltered `total` includes pre-migration rows (NULL guid). The feed-filtered `total` JOINs to `episodes` and therefore correctly excludes NULL-guid rows. This is intentional per D-10 but must be implemented consistently.

**How to avoid:** For unfiltered queries: `SELECT SUM(cost) FROM cost_tracking` (includes NULL-guid rows). For feed-filtered: `SELECT SUM(ct.cost) FROM cost_tracking ct JOIN episodes e ON ct.guid = e.guid WHERE e.podcast = ?` (naturally excludes NULL-guid rows since JOIN requires a matching episode).

### Pitfall 6: `save_cost()` call sites in pipeline — guid not always known

**What goes wrong:** Three `save_cost()` call sites in `pipeline.py` (lines 653, 674, 709). All three are inside `_process_episode()` which receives `episode.guid`. All three have the GUID available and should pass it.

**How to avoid:** Verify each call site has access to `episode.guid` before adding the parameter. (All three do — confirmed by reading `pipeline.py:640-712`.)

---

## Code Examples

### DB-01: Episode list with LEFT JOIN and NULLS LAST

```python
# Source: verified in this research session against SQLite 3.50.4
SQL_EPISODES = """
SELECT
    e.id, e.podcast, e.guid, e.title, e.pubdate, e.skipped,
    e.url, e.description, e.explicit, e.duration, e.image_url,
    e.episode_type, e.itunes_author, e.itunes_subtitle, e.itunes_summary,
    e.content_encoded, e.link, e.author, e.itunes_title,
    e.episode_number, e.season_number, e.itunes_block, e.length, e.source_url,
    CASE
        WHEN e.skipped = 1 THEN 'skipped'
        WHEN adr.guid IS NOT NULL THEN 'processed'
        WHEN t.guid IS NOT NULL THEN 'transcribed'
        WHEN eam.guid IS NOT NULL THEN 'downloaded'
        ELSE 'pending'
    END AS pipeline_state_db
FROM episodes e
LEFT JOIN episode_audio_metadata eam ON e.guid = eam.guid
LEFT JOIN transcriptions t ON e.guid = t.guid
LEFT JOIN ad_detection_runs adr ON e.guid = adr.guid
{where}
ORDER BY e.pubdate IS NULL ASC, e.pubdate DESC
LIMIT ? OFFSET ?
"""
```

### DB-01: Output file existence check

```python
# Source: components/pipeline.py:498-531 (output file glob pattern)
from datetime import datetime
from pathlib import Path
from slugify import slugify

def _is_complete(row_pubdate: str | None, episode_title: str, episode_podcast: str, output_dir: Path) -> bool:
    if row_pubdate is None:
        return False
    pub_date = datetime.fromisoformat(row_pubdate).astimezone()
    pub_date_str = pub_date.strftime("%d.%m.%Y")
    title_slug = slugify(episode_title)
    feed_slug = slugify(episode_podcast)
    output_feed_dir = output_dir / feed_slug
    return any(output_feed_dir.glob(f"{pub_date_str}-{title_slug}.*"))
```

### DB-02: Transcription detail

```python
# Source: database/transcription_store.py patterns + response shape from D-08
async with Database(db_path) as db:
    async with db.conn.execute(
        "SELECT transcription FROM transcriptions WHERE guid = ?", (guid,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise web.HTTPNotFound(text='{"error": "not found"}', content_type="application/json")
    async with db.conn.execute(
        "SELECT start_ms, end_ms, text FROM transcription_segments WHERE guid = ? ORDER BY start_ms ASC",
        (guid,),
    ) as cursor:
        segments = await cursor.fetchall()
return web.json_response({
    "guid": guid,
    "text": row[0],
    "segments": [{"start": r[0], "end": r[1], "text": r[2]} for r in segments],
})
```

### DB-03: Ad detection detail

```python
# Source: database/ad_store.py patterns + response shape from D-09
async with Database(db_path) as db:
    async with db.conn.execute(
        "SELECT id FROM ad_detection_runs WHERE guid = ?", (guid,)
    ) as cursor:
        run_row = await cursor.fetchone()
    if run_row is None:
        raise web.HTTPNotFound(text='{"error": "not found"}', content_type="application/json")
    async with db.conn.execute(
        "SELECT start_ms, end_ms, confidence, sponsor, ad_topic FROM ad_segments WHERE guid = ? ORDER BY start_ms ASC",
        (guid,),
    ) as cursor:
        segs = await cursor.fetchall()
return web.json_response({
    "guid": guid,
    "detected": True,
    "segments": [
        {"start_ms": r[0], "end_ms": r[1], "confidence": r[2], "sponsor": r[3], "ad_topic": r[4]}
        for r in segs
    ],
})
```

### schema migration: ALTER TABLE cost_tracking

```python
# Source: database/connection.py:160-172 (established migration pattern)
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid)"
    )
await self.conn.commit()
```

### save_cost signature update

```python
# Source: database/cost_tracking_store.py:34-46 (current implementation to modify)
async def save_cost(self, cost: CostRecord, guid: str | None = None) -> None:
    await self._conn.execute(
        "INSERT INTO cost_tracking (provider, model, cost, guid) VALUES (?, ?, ?, ?)",
        (cost.provider, cost.model, cost.cost, guid),
    )
    await self._conn.commit()
    logger.debug(f"Saved cost ${cost.cost:.6f} for {cost.provider}/{cost.model}")
```

---

## State of the Art

| Old Approach | Current Approach | Impact |
|--------------|------------------|--------|
| `NULLS LAST` SQL clause | `ORDER BY col IS NULL ASC, col DESC` | SQLite has never supported `NULLS LAST`; workaround is idiomatic |
| Sharing a DB connection pool | Per-request `Database` context manager | CLAUDE.md constraint; WAL mode allows concurrent reads from separate connections |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `create_db_router` needs `config_path` parameter (not in CONTEXT.md's discretion note) to support feed slug reverse lookup | Pitfall 2 / Architecture | If config is not accessible, `?feed` filter cannot work — must add config_path or find another approach |
| A2 | The three `save_cost()` call sites (pipeline.py:653, 674, 709) all have `episode.guid` in scope | Code Examples / Pitfall 6 | If any call site does not have a guid, the parameter cannot always be passed — confirmed by reading pipeline.py:640-712 [VERIFIED: read pipeline.py] |

---

## Open Questions

1. **`create_db_router` signature — config_path omission in CONTEXT.md**
   - What we know: D-06 requires `?feed={slug}` filter which requires mapping a slug to a podcast title, which requires iterating the config feeds list.
   - What's unclear: CONTEXT.md's discretion note says `create_db_router(db_path, output_dir)` but does not mention `config_path`. This was likely an oversight.
   - Recommendation: Add `config_path: Path` as a third parameter (consistent with `feeds.py`). The executor should proceed with this; no user confirmation needed as the alternative (hard-coding the config path or using a global) is worse.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (asyncio_mode = "auto") |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/test_api_db.py -x -q` |
| Full suite command | `uv run pytest --cov=. -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DB-01 | Episodes list returns rows with pipeline_state and feed_slug | unit | `uv run pytest tests/test_api_db.py::TestGetEpisodes -x` | ❌ Wave 0 |
| DB-01 | pagination (offset, limit, max=200) | unit | `uv run pytest tests/test_api_db.py::TestGetEpisodespagination -x` | ❌ Wave 0 |
| DB-01 | `?feed=slug` filter maps slug → podcast title | unit | `uv run pytest tests/test_api_db.py::TestGetEpisodesFeedFilter -x` | ❌ Wave 0 |
| DB-01 | `complete` state when output file exists on disk | unit | `uv run pytest tests/test_api_db.py::TestPipelineStateComplete -x` | ❌ Wave 0 |
| DB-02 | Transcription detail returns text + segments | unit | `uv run pytest tests/test_api_db.py::TestGetTranscriptions -x` | ❌ Wave 0 |
| DB-02 | 404 when no transcription row | unit | `uv run pytest tests/test_api_db.py::TestGetTranscriptions404 -x` | ❌ Wave 0 |
| DB-03 | Ad detection returns segments + detected flag | unit | `uv run pytest tests/test_api_db.py::TestGetAds -x` | ❌ Wave 0 |
| DB-03 | 404 when no ad_detection_runs row | unit | `uv run pytest tests/test_api_db.py::TestGetAds404 -x` | ❌ Wave 0 |
| DB-03 | detected=True when run row exists but no segments | unit | `uv run pytest tests/test_api_db.py::TestGetAdsNoSegments -x` | ❌ Wave 0 |
| DB-04 | Cost aggregate — total, by_model, by_episode | unit | `uv run pytest tests/test_api_db.py::TestGetCosts -x` | ❌ Wave 0 |
| DB-04 | `?feed` filter applied to costs | unit | `uv run pytest tests/test_api_db.py::TestGetCostsFeedFilter -x` | ❌ Wave 0 |
| DB-04 | NULL-guid rows omitted from by_episode | unit | `uv run pytest tests/test_api_db.py::TestGetCostsNullGuid -x` | ❌ Wave 0 |
| D-01/D-02 | cost_tracking migration adds guid column; save_cost accepts guid | unit | `uv run pytest tests/test_cost_tracking_store.py tests/test_database_connection.py -x` | ✅ (extend) |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_api_db.py tests/test_cost_tracking_store.py tests/test_database_connection.py -x -q`
- **Per wave merge:** `uv run pytest --cov=. -q`
- **Phase gate:** Full suite green + 100% coverage before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/test_api_db.py` — all DB viewer endpoint tests (new file)
- [ ] Extend `tests/test_cost_tracking_store.py` — add test for `guid` parameter
- [ ] Extend `tests/test_database_connection.py` — add test for `guid` column migration

---

## Security Domain

`security_enforcement` not explicitly disabled in config — including this section.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth in v1 (SEC-01 deferred) |
| V3 Session Management | no | Stateless REST; no sessions |
| V4 Access Control | no | All endpoints are read-only; no authorization decisions |
| V5 Input Validation | yes | `int()` conversion + min/max clamping for offset/limit; slug is string-compared via `slugify()` |
| V6 Cryptography | no | No encryption needed for DB viewer |

### Known Threat Patterns

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via `?feed` query param | Tampering | Parameterized queries (`?` placeholders) — all DB calls already use them |
| Integer overflow / negative offset | Tampering | `max(0, int(...))` for offset; `min(200, int(...))` for limit; wrap in `try/except ValueError → 400` |
| Path traversal via guid in URL | Tampering | GUID is used only in SQL parameterized queries, never in file paths |
| DoS via huge limit | DoS | max limit=200 enforcement (D-06) |

---

## Sources

### Primary (HIGH confidence)

- `database/connection.py` — Table schemas, migration pattern (contextlib.suppress)
- `database/cost_tracking_store.py` — `save_cost()` current signature; call sites at lines 653, 674, 709
- `database/transcription_store.py` — Query patterns for transcriptions + segments
- `database/ad_store.py` — Query patterns for ad_detection_runs + ad_segments
- `database/episode_store.py` — Column order, `STAGE_CASCADE`, row structure
- `api/routes/feeds.py` — `create_feeds_router` factory pattern; `_find_feed_by_slug`; per-request Database open
- `api/server.py` — `create_app()` signature; route registration
- `components/pipeline.py:498-531` — Output file glob pattern; `complete` state detection
- `components/feed_publisher.py:72-86` — `episode_filename()` naming convention; `slugify()` usage
- `config/config_loader.py` — `AppConfig`, `PathsConfig` (output_dir field)
- In-session SQL verification against SQLite 3.50.4 — NULLS LAST workaround; LEFT JOIN state derivation; cost aggregation queries

### Secondary (MEDIUM confidence)

- `tests/test_api_feeds.py` — TestClient / TestServer pattern for aiohttp handler tests; mock Database pattern

---

## Metadata

**Confidence breakdown:**
- Schema migration pattern: HIGH — direct codebase read of existing migration code
- SQL queries: HIGH — prototyped and verified against SQLite 3.50.4 in-process
- Output file naming: HIGH — read from `audio_editor.py` and `pipeline.py` (exact glob pattern)
- Test patterns: HIGH — existing `test_api_feeds.py` provides the exact template
- `config_path` parameter addition: MEDIUM — inferred as necessary; CONTEXT.md discretion note may have omitted it accidentally

**Research date:** 2026-05-19  
**Valid until:** 2026-06-18 (stable stack; no fast-moving dependencies)
