# 05 — Read Endpoints for the GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The GUI can show one episode's full detail (including topic extraction), search episodes by title, filter by skipped, and paginate with a known total.

**Architecture:** Additions to `api/routes/db.py` only, all on `ReadOnlyDatabase`. The list response body stays a JSON array, so the change is backward compatible; the total goes in an `X-Total-Count` header.

**Tech Stack:** aiohttp, aiosqlite.

**Spec:** [plans/README.md](README.md) — Decisions → Behaviour → G5.

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan).

## Review Focus

1. **`q` containing `%` or `_`:** it matches literally, not as a wildcard. We use `instr(lower(title), lower(?))`, not `LIKE`. Tested in Task 3.
2. **`q` combined with `feed`:** both filters apply, and `X-Total-Count` reflects both. Tested in Task 3.
3. **An unknown feed slug:** the body is `[]` and `X-Total-Count: 0`. Tested in Task 3.
4. **Single episode lookup for a skipped episode:** `pipeline_state` is `"skipped"`, the same as in the list. Tested in Task 2.
5. **A GUID containing `/` or URL-reserved characters** (common in RSS GUIDs, which are often URLs): the GUI must `encodeURIComponent` it, and aiohttp decodes `{guid}`. aiohttp's `{guid}` matches no `/`, so a GUID containing `/` 404s on every guid route, existing ones included. Test `GET /api/v1/db/episodes/{quote(guid, safe='')}` with an `http://…` GUID in Task 2.
   - If it fails, change all `{guid}` route patterns to `{guid:.+}` in one follow-up commit. They are spread across `control.py`, `db.py`, and this plan's new routes.

---

### Task 1: `GET /api/v1/db/topics/{guid}`

**Files:**
- Modify: `api/routes/db.py`
- Test: `tests/test_api_db.py`

**Interfaces:**
- Produces: `GET /api/v1/db/topics/{guid}`, with responses:
  - 200 `{"guid", "topic", "hosts", "show"}` (strings as stored)
  - 404 `{"error": "not found"}`

- [ ] **Step 1: Write the failing tests**

  - `test_topics_returns_row`: seed `topic_extractions`, assert all four fields.
  - `test_topics_returns_404_when_missing`.

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_api_db.py -v -k topics`

  Expected: FAIL (404 from the router for the unknown route, so the field assertions fail).

- [ ] **Step 3: Implement**

  Add a handler that does a `SELECT topic, hosts, show FROM topic_extractions WHERE guid = ?`. Same shape as `get_transcription`.

- [ ] **Step 4: Verify**

  Run: `uv run pytest tests/test_api_db.py -v`

  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/db.py tests/test_api_db.py
git commit -m "feat(api): add GET /db/topics/{guid}"
```

### Task 2: `GET /api/v1/db/episodes/{guid}`

**Files:**
- Modify: `api/routes/db.py`
- Test: `tests/test_api_db.py`

**Interfaces:**
- Produces:
  - `def _row_to_episode_dict(row: Sequence, output_dir: Path) -> dict`, extracted from the list handler's loop body. It returns exactly the current list item shape (including `feed_slug` and `pipeline_state`).
  - `GET /api/v1/db/episodes/{guid}`, with responses:
    - 200 a single object of that shape
    - 404 `{"error": "not found"}`

- [ ] **Step 1: Write the failing tests**

  - `test_single_episode_returns_same_shape_as_list_item`: seed one episode; assert `GET /db/episodes/g1` equals `GET /db/episodes` `[0]`.
  - `test_single_episode_skipped_state`
  - `test_single_episode_404`
  - `test_single_episode_guid_with_slashes`: GUID `"http://x/ep/1"`, request path `quote(guid, safe="")`.

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_api_db.py -v -k single_episode`

  Expected: FAIL.

- [ ] **Step 3: Implement**

  - Extract the helper.
  - Change `_SQL_EPISODES`' `{where}` placeholder usage so the single-episode query passes `WHERE e.guid = ?` with `LIMIT 1 OFFSET 0`.
  - Register the route **after** `/api/v1/db/episodes` (no conflict, but keep the order readable).

- [ ] **Step 4: Verify**

  Run: `uv run pytest tests/test_api_db.py -v`

  Expected: PASS. If the slash test fails, apply the `{guid:.+}` change from Review Focus 5 to every guid route, and re-run the full suite.

- [ ] **Step 5: Commit**

```bash
git add api/ tests/test_api_db.py
git commit -m "feat(api): add GET /db/episodes/{guid}"
```

### Task 3: Title search and `X-Total-Count`

**Files:**
- Modify: `api/routes/db.py` (`get_episodes`)
- Modify: `API.md` §2.5 (`q` param, `X-Total-Count` header, new endpoints; §7 quick reference)
- Test: `tests/test_api_db.py`

**Interfaces:**
- Produces:
  - `GET /api/v1/db/episodes?q=<text>` does a case-insensitive substring match on `e.title`.
  - `GET /api/v1/db/episodes?skipped=true|false` filters on `e.skipped`. Any other value → 400 `{"error": "skipped must be true or false"}`. The GUI sidebar's "Skipped" entry uses it.
  - Every 200 response carries the header `X-Total-Count: <int>`: the count of rows matching all filters, ignoring `limit`/`offset`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_episodes_q_filters_by_title_case_insensitive(...)   # "news" matches "Daily NEWS"
async def test_episodes_q_treats_percent_literally(...)             # q="%" matches only titles containing "%"
async def test_episodes_q_and_feed_combine(...)
async def test_episodes_total_count_header_ignores_pagination(...)  # 5 rows, limit=2 → header "5", body len 2
async def test_episodes_unknown_feed_total_count_zero(...)          # header "0", body []
async def test_episodes_skipped_true_returns_only_skipped(...)      # 1 skipped of 3 → body len 1, header "1"
async def test_episodes_skipped_false_excludes_skipped(...)
async def test_episodes_skipped_invalid_returns_400(...)            # skipped=yes → 400
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_api_db.py -v -k "q_ or total_count or skipped_"`

  Expected: FAIL.

- [ ] **Step 3: Implement**

  - Build `conditions: list[str]` and `params: list` from `feed`, `q` and `skipped`. For `q`, use `instr(lower(e.title), lower(?)) > 0`. For `skipped`, use `e.skipped = ?` with `1`/`0`.
  - Join the conditions into `WHERE …`, used by both the page query and a `SELECT COUNT(*) FROM episodes e {where}` query.
  - Set the header on the existing `json_response`.

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean.

- [ ] **Step 5: Update `API.md` and commit**

```bash
git add api/routes/db.py tests/test_api_db.py API.md
git commit -m "feat(api): episode title search and X-Total-Count header"
```
