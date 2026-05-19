# Phase 5: Database Viewer - Pattern Map

**Mapped:** 2026-05-19
**Files analyzed:** 6 new/modified files
**Analogs found:** 6 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `api/routes/db.py` | route | request-response | `api/routes/feeds.py` | exact |
| `api/server.py` | config | request-response | `api/server.py` (self, extend) | exact |
| `database/connection.py` | migration | CRUD | `database/connection.py` (self, extend) | exact |
| `database/cost_tracking_store.py` | service | CRUD | `database/cost_tracking_store.py` (self, extend) | exact |
| `components/pipeline.py` | service | CRUD | `components/pipeline.py` (self, extend — 3 call sites) | exact |
| `tests/test_api_db.py` | test | request-response | `tests/test_api_feeds.py` | exact |

---

## Pattern Assignments

### `api/routes/db.py` (route, request-response)

**Analog:** `api/routes/feeds.py`

**Imports pattern** (`api/routes/feeds.py` lines 1-22):
```python
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml
from aiohttp import web
from slugify import slugify

from config.config_loader import AppConfig
from database.connection import Database

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)
```

**Factory signature pattern** (`api/routes/feeds.py` line 54):
```python
def create_feeds_router(config_path: Path, db_path: Path) -> web.RouteTableDef:
```

For `db.py`, the factory signature becomes (note: `config_path` is required for `?feed` slug reverse-lookup — see RESEARCH.md Pitfall 2):
```python
def create_db_router(db_path: Path, output_dir: Path, config_path: Path) -> web.RouteTableDef:
    routes = web.RouteTableDef()
    ...
    return routes
```

**Slug reverse-lookup helper pattern** (`api/routes/feeds.py` lines 25-30 and `api/routes/control.py` lines 26-31):
```python
# feeds.py version — returns FeedConfig
def _find_feed_by_slug(slug: str, feeds: list[FeedConfig]) -> FeedConfig | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed
    return None

# control.py version — returns title string
def _resolve_slug(slug: str, feeds: list) -> str | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None
```

**Config read pattern** (`api/routes/feeds.py` lines 69-71):
```python
with config_path.open() as f:
    raw = yaml.safe_load(f)
cfg = AppConfig.model_validate(raw)
```

**Per-request Database connection pattern** (`api/routes/feeds.py` lines 73-80):
```python
async with Database(db_path) as db:
    cursor = await db.conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE podcast = ?",
        (feed.title,),
    )
    row = await cursor.fetchone()
```

**Per-request Database connection — async context manager on cursor** (`api/routes/control.py` lines 171-178):
```python
async with Database(db_path) as db:
    store = EpisodeStore(db.conn)
    ok = await store.skip_episode(guid)
if not ok:
    raise web.HTTPNotFound(
        text=f'{{"error": "episode not found: {guid}"}}',
        content_type="application/json",
    )
```

**404 error pattern** (`api/routes/feeds.py` lines 128-132 and `api/routes/control.py` lines 137-140):
```python
raise web.HTTPNotFound(
    text='{"error": "feed not found"}',
    content_type="application/json",
)
```

**Query param parsing pattern** (`api/routes/control.py` line 119):
```python
force = request.rel_url.query.get("force", "").lower() == "true"
```
For `db.py`, apply the same pattern for `offset`, `limit`, and `feed`:
```python
limit = min(int(request.rel_url.query.get("limit", 50)), 200)
offset = max(0, int(request.rel_url.query.get("offset", 0)))
feed_slug = request.rel_url.query.get("feed")
```

**json_response pattern** (`api/routes/feeds.py` line 91):
```python
return web.json_response(result)
```

**Path param pattern** (`api/routes/control.py` line 165):
```python
guid = request.match_info["guid"]
```

**DB-01 pipeline_state SQL** (from RESEARCH.md Pattern 4 — verified against SQLite 3.50.4):
```sql
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
```

**DB-01 output file existence check** (output path derived from `components/pipeline.py` lines 498-531):
```python
# pipeline.py line 498 — the strftime format used in output filenames
pub_date_str = episode.pub_date.astimezone().strftime("%d.%m.%Y")
# pipeline.py line 531 — glob matching any extension
existing_audio = next(
    (p for p in output_feed_dir.glob(f"{pub_date_str}-{title_slug}.*")),
    None,
)
```

In the DB viewer handler, apply the same pattern (DB rows use string pubdate — parse first):
```python
from datetime import datetime
from pathlib import Path
from slugify import slugify

def _is_complete(row_pubdate: str | None, title: str, podcast: str, output_dir: Path) -> bool:
    if row_pubdate is None:
        return False
    pub_date = datetime.fromisoformat(row_pubdate).astimezone()
    pub_date_str = pub_date.strftime("%d.%m.%Y")
    feed_slug = slugify(podcast)
    title_slug = slugify(title)
    return any(
        (output_dir / feed_slug).glob(f"{pub_date_str}-{title_slug}.*")  # noqa: ASYNC240
    )
```

---

### `api/server.py` (config, request-response) — extend existing file

**Analog:** `api/server.py` (self, lines 28-59)

**Current `create_app` signature** (`api/server.py` lines 28-34):
```python
def create_app(
    event_bus: EventBus,
    start_time: float,
    run_state: RunState,
    config: Config,
    config_path: Path,
) -> web.Application:
```

**Route registration pattern** (`api/server.py` lines 54-58):
```python
app.add_routes(create_health_router(start_time))
app.add_routes(create_events_router(event_bus))
app.add_routes(create_control_router(config, event_bus, run_state))
app.add_routes(create_settings_router(config_path))
app.add_routes(create_feeds_router(config_path, config.app.paths.data_dir / "data.db"))
```

New line to add (after feeds router):
```python
app.add_routes(create_db_router(
    config.app.paths.data_dir / "data.db",
    config.app.paths.output_dir,
    config_path,
))
```

---

### `database/connection.py` (migration) — extend existing file

**Analog:** `database/connection.py` lines 160-172 (existing migration pattern)

**Migration pattern to copy and extend** (`database/connection.py` lines 160-172):
```python
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN length INTEGER NOT NULL DEFAULT 0"
    )
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"
    )
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE episodes ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0"
    )
await self.conn.commit()
```

New migration to add after the existing block (before the final `commit`):
```python
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid)"
    )
```

---

### `database/cost_tracking_store.py` (service, CRUD) — extend existing file

**Analog:** `database/cost_tracking_store.py` lines 34-46

**Current `save_cost` signature** (lines 34-46):
```python
async def save_cost(self, cost: CostRecord) -> None:
    """Append a cost record to ``cost_tracking``."""
    await self._conn.execute(
        "INSERT INTO cost_tracking (provider, model, cost) VALUES (?, ?, ?)",
        (cost.provider, cost.model, cost.cost),
    )
    await self._conn.commit()
    logger.debug(f"Saved cost ${cost.cost:.6f} for {cost.provider}/{cost.model}")
```

New signature (add optional `guid` parameter; update SQL):
```python
async def save_cost(self, cost: CostRecord, guid: str | None = None) -> None:
    await self._conn.execute(
        "INSERT INTO cost_tracking (provider, model, cost, guid) VALUES (?, ?, ?, ?)",
        (cost.provider, cost.model, cost.cost, guid),
    )
    await self._conn.commit()
    logger.debug(f"Saved cost ${cost.cost:.6f} for {cost.provider}/{cost.model}")
```

---

### `components/pipeline.py` (service, CRUD) — 3 call site updates

**Analog:** `components/pipeline.py` lines 653, 674, 709

All three `save_cost()` calls are inside `_process_episode()` which has `episode.guid` in scope.

**Call site 1** (line 653 — ad detection cost):
```python
# Before:
await stores.cost.save_cost(ad_cost)
# After:
await stores.cost.save_cost(ad_cost, guid=episode.guid)
```

**Call site 2** (line 674 — topic extraction cost):
```python
# Before:
await stores.cost.save_cost(topic_cost)
# After:
await stores.cost.save_cost(topic_cost, guid=episode.guid)
```

**Call site 3** (line 709 — transcription cost):
```python
# Before:
await stores.cost.save_cost(cost)
# After:
await stores.cost.save_cost(cost, guid=episode.guid)
```

---

### `tests/test_api_db.py` (test, request-response) — new file

**Analog:** `tests/test_api_feeds.py`

**Test file imports pattern** (`tests/test_api_feeds.py` lines 1-16):
```python
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from aiohttp.test_utils import TestClient, TestServer

from api.event_bus import EventBus
from api.run_state import RunState
from api.server import create_app
from tests.test_config_loader import VALID_YAML
```

**YAML fixture pattern** (`tests/test_api_feeds.py` lines 21-56):
```python
_TWO_FEEDS_YAML = """\
feeds:
  - title: "Show A"
    url: "https://show-a.example/feed.rss"
    ...
"""
```

**Database mock helper pattern** (`tests/test_api_feeds.py` lines 59-88):
```python
def _make_db_patch(*, counts: dict[str, int] | None = None):
    mock_db_obj = MagicMock()
    mock_db_cm = MagicMock()
    mock_db_cm.__aenter__ = AsyncMock(return_value=mock_db_obj)
    mock_db_cm.__aexit__ = AsyncMock(return_value=False)

    async def _execute(sql: str, params: tuple) -> MagicMock:
        cursor = MagicMock()
        ...
        cursor.fetchone = AsyncMock(return_value=(...))
        return cursor

    mock_db_obj.conn.execute = _execute

    import contextlib

    @contextlib.contextmanager
    def _patches():
        with patch("api.routes.feeds.Database", return_value=mock_db_cm) as mock_db_cls:
            yield mock_db_cls

    return _patches()
```

For `test_api_db.py`, patch target is `api.routes.db.Database`.

**App factory helper pattern** (`tests/test_api_feeds.py` lines 91-97):
```python
def _make_app(tmp_path: Path, yaml_content: str = _TWO_FEEDS_YAML) -> tuple[object, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml_content)
    cfg = MagicMock()
    cfg.app.paths.data_dir = tmp_path
    app = create_app(EventBus(), time.monotonic(), RunState(), cfg, config_path)
    return app, config_path
```

For `test_api_db.py`, `cfg.app.paths.output_dir` must also be set:
```python
cfg.app.paths.output_dir = tmp_path / "output"
```

**TestClient/TestServer test pattern** (`tests/test_api_feeds.py` lines 105-121):
```python
class TestGetFeeds:
    async def test_returns_both_feeds_with_slugs_and_counts(self, tmp_path) -> None:
        app, _ = _make_app(tmp_path)
        with _make_db_patch(...):
            async with TestClient(TestServer(app)) as client:
                resp = await client.get("/api/v1/feeds")
                assert resp.status == 200
                data = await resp.json()
                ...
```

**Migration test pattern** (`tests/test_database_connection.py` lines 57-67):
```python
async def _column_names(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(episodes)")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}

async def test_episodes_table_has_url_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "url" in await _column_names(db_path)
```

Apply same pattern for `cost_tracking` guid column test:
```python
async def _cost_tracking_column_names(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(cost_tracking)")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}

async def test_cost_tracking_has_guid_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "guid" in await _cost_tracking_column_names(db_path)
```

**CostTrackingStore test pattern** (`tests/test_cost_tracking_store.py` lines 21-30):
```python
async def test_save_cost_stores_correct_values(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    async with Database(db_path) as db:
        store = CostTrackingStore(db.conn)
        await store.save_cost(_cost(provider="groq", model="whisper-large-v3-turbo", cost=0.0042))

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT provider, model, cost FROM cost_tracking")
        row = await cursor.fetchone()
    assert row == ("groq", "whisper-large-v3-turbo", 0.0042)
```

For guid test:
```python
async def test_save_cost_with_guid_stores_guid(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    async with Database(db_path) as db:
        # Insert a parent episode row first (foreign key constraint)
        await db.conn.execute(
            "INSERT INTO episodes (podcast, title, guid, url) VALUES (?, ?, ?, ?)",
            ("Show A", "Ep 1", "guid-1", "https://example.com/ep1"),
        )
        await db.conn.commit()
        store = CostTrackingStore(db.conn)
        await store.save_cost(_cost(cost=0.005), guid="guid-1")

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT guid FROM cost_tracking")
        row = await cursor.fetchone()
    assert row == ("guid-1",)
```

---

## Shared Patterns

### Per-Request Database Connection (never shared)
**Source:** `api/routes/feeds.py` lines 73-80 and `api/routes/control.py` lines 171-173
**Apply to:** All four handlers in `api/routes/db.py`
```python
async with Database(db_path) as db:
    cursor = await db.conn.execute("SELECT ...", (param,))
    row = await cursor.fetchone()
```
CLAUDE.md hard constraint: never reuse the pipeline connection. Always open `async with Database(db_path)` per request.

### 404 Error Response
**Source:** `api/routes/feeds.py` lines 128-132
**Apply to:** DB-02 (no transcription), DB-03 (no ad_detection_runs row)
```python
raise web.HTTPNotFound(
    text='{"error": "not found"}',
    content_type="application/json",
)
```

### Slug Reverse Lookup
**Source:** `api/routes/feeds.py` lines 25-30 (returns FeedConfig) and `api/routes/control.py` lines 26-31 (returns title string)
**Apply to:** DB-01 `?feed` filter, DB-04 `?feed` filter
```python
def _resolve_slug(slug: str, feeds: list[FeedConfig]) -> str | None:
    for feed in feeds:
        if slugify(feed.title) == slug:
            return feed.title
    return None
```

### Config Read Per Request
**Source:** `api/routes/feeds.py` lines 69-71
**Apply to:** Any handler in `db.py` that needs to resolve a `?feed` slug
```python
with config_path.open() as f:
    raw = yaml.safe_load(f)
cfg = AppConfig.model_validate(raw)
```

### Idempotent ALTER TABLE Migration
**Source:** `database/connection.py` lines 160-172
**Apply to:** `database/connection.py` migration block (D-01)
```python
with contextlib.suppress(aiosqlite.OperationalError):
    await self.conn.execute(
        "ALTER TABLE cost_tracking ADD COLUMN guid TEXT REFERENCES episodes(guid)"
    )
await self.conn.commit()
```

### f-string Logging
**Source:** `api/routes/feeds.py` line 116, `database/cost_tracking_store.py` line 46
**Apply to:** All new log statements
```python
logger.info(f"Feed added: {new_feed.title!r}")
logger.debug(f"Saved cost ${cost.cost:.6f} for {cost.provider}/{cost.model}")
```
CLAUDE.md hard constraint: f-strings only, never `%` operator.

### NULLS LAST SQL Ordering (SQLite idiom)
**Source:** RESEARCH.md Pattern 3, verified against SQLite 3.50.4
**Apply to:** DB-01 episode list ORDER BY
```sql
ORDER BY e.pubdate IS NULL ASC, e.pubdate DESC
```

---

## No Analog Found

All files have close matches. No files require falling back to RESEARCH.md patterns alone.

---

## Metadata

**Analog search scope:** `api/routes/`, `api/`, `database/`, `components/`, `tests/`
**Files scanned:** 8 (feeds.py, control.py, server.py, connection.py, cost_tracking_store.py, pipeline.py, test_api_feeds.py, test_database_connection.py, test_cost_tracking_store.py)
**Pattern extraction date:** 2026-05-19
