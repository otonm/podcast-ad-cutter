# 02 — SQLite WAL Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** API read handlers never fail with "database is locked" while the pipeline writes.

**Architecture:** `Database.__aenter__` sets `PRAGMA journal_mode=WAL`. The setting is persistent in the DB file, so the `ReadOnlyDatabase` connections the API opens inherit it. CLAUDE.md already mandates this, but the code never set it.

**Tech Stack:** aiosqlite, SQLite.

**Spec:** [plans/README.md](README.md) — G8; CLAUDE.md → Constraints ("read-only connection with WAL mode").

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan).

## Review Focus

1. **A reader during a writer's EXCLUSIVE transaction:** the read succeeds with WAL, and times out with a rollback journal. Tested below.
2. **An existing `data.db` created in rollback mode:** the first `Database` open converts it. The same test covers this, because the pragma runs on every open.
3. **A read-only connection on a WAL DB whose `-shm` file is missing:** the backend opens `Database` once at server start (`api/server.py` `serve()`), which creates it. No test; confirm `serve()` still opens `Database` before the API serves requests.
4. **Docker volume ownership of the `-wal`/`-shm` files:** these are created by the same `app` user. No test; check during plan 06's container verification.

---

### Task 1: Enable WAL on every write connection

**Files:**
- Modify: `database/connection.py` (`Database.__aenter__`, right after `aiosqlite.connect`)
- Test: `tests/test_database_connection.py`

**Interfaces:**
- Produces: no new API. `Database` connections are in WAL mode.

- [ ] **Step 0: Look up docs**

  Use Context7 or sqlite.org for `PRAGMA journal_mode=WAL` semantics with `mode=ro` URI readers.

- [ ] **Step 1: Write the failing tests**

```python
async def test_database_enables_wal_mode(db_path):
    async with Database(db_path) as db:
        async with db.conn.execute("PRAGMA journal_mode") as cur:
            assert (await cur.fetchone())[0] == "wal"

async def test_readonly_reader_not_blocked_by_exclusive_writer(db_path):
    async with Database(db_path) as db:
        await db.conn.execute("BEGIN EXCLUSIVE")
        await db.conn.execute("INSERT INTO episodes (podcast, title, guid) VALUES ('p','t','g')")
        async with ReadOnlyDatabase(db_path) as ro:
            async with ro.conn.execute("SELECT COUNT(*) FROM episodes") as cur:
                assert (await cur.fetchone())[0] == 0
        await db.conn.rollback()
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_database_connection.py -v -k "wal or exclusive"`

  Expected: the first test FAILS (`"delete"`). The second FAILS with `database is locked` after about 5 s.

- [ ] **Step 3: Implement**

  Add `await self.conn.execute("PRAGMA journal_mode=WAL")` immediately after `PRAGMA foreign_keys = ON`.

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean.

- [ ] **Step 5: Commit**

  Also update `ARCHITECTURE.md` → "Database Concurrency" with one line: WAL is enabled in `Database.__aenter__`.

```bash
git add database/connection.py tests/test_database_connection.py ARCHITECTURE.md
git commit -m "fix(db): enable WAL so API readers never block on pipeline writes"
```
