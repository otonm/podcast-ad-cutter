# 04 — Episode Enable / Disable / Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The GUI can disable an episode, re-enable it, and delete it (data + output file) so that it stays gone.

**Architecture:**
- Reuse the existing `episodes.skipped` flag as the single "disabled" state.
- Exclude skipped episodes from the published RSS.
- Add an un-skip store method and route.
- Add a delete route that combines three existing operations: skip, `reset_episode` (full cascade), and removing the output audio file.

**Tech Stack:** aiohttp, aiosqlite, python-slugify.

**Spec:** [plans/README.md](README.md) — Decisions → Behaviour → G4.

**Depends on:** plan 01 (`create_control_router(config_path, db_path, event_bus, run_state)` signature).

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan).

## Review Focus

1. **A skipped episode reappears in the RSS with the original (ad-containing) URL:** today `get_episodes_for_feed` doesn't filter on `skipped`. Tested in Task 1.
2. **`episodes_to_keep=N` with one of the N newest skipped:** the feed still lists N episodes, filled from older ones. Tested in Task 1.
3. **Delete when the output file doesn't exist** (episode never processed): expect 200 with `files_removed: []`. Tested in Task 3.
4. **Delete or un-skip while a run is active:** expect 409, consistent with skip and reprocess. Tested in Tasks 2 and 3.
5. **The RSS still lists a deleted episode until the next run**, and its enclosure 404s meanwhile. Accepted ceiling (the next cron run fixes it). Mark it in the handler with a `ponytail:` comment; no test.

---

### Task 1: Store — un-skip, and exclude skipped episodes from publication

**Files:**
- Modify: `database/episode_store.py` (`get_episodes_for_feed` SQL around :163; new methods after `skip_episode` :207)
- Test: `tests/test_episode_store.py`

**Interfaces:**
- Produces:
  - `EpisodeStore.unskip_episode(guid: str) -> bool` — sets `skipped = 0`, commits, returns `False` if the GUID is unknown. Logs like `skip_episode`.
  - `EpisodeStore.get_episode_location(guid: str) -> tuple[str, str, str | None] | None` — returns `(podcast, title, pubdate)`, or `None` if unknown.
  - `get_episodes_for_feed(podcast, limit)` now returns only `skipped = 0` rows.

- [ ] **Step 1: Write the failing tests**

```python
async def test_unskip_episode_clears_flag(store):
    <save one episode "g1">; await store.skip_episode("g1")
    assert await store.unskip_episode("g1") is True
    assert await store.is_skipped("g1") is False

async def test_unskip_unknown_guid_returns_false(store):
    assert await store.unskip_episode("nope") is False

async def test_get_episodes_for_feed_excludes_skipped_and_backfills(store):
    <save 3 episodes newest→oldest g3,g2,g1>; await store.skip_episode("g3")
    eps = await store.get_episodes_for_feed("Pod", 2)
    assert [e.guid for e in eps] == ["g2", "g1"]

async def test_get_episode_location(store):
    <save episode g1 podcast "Pod" title "T" pubdate X>
    assert await store.get_episode_location("g1") == ("Pod", "T", <stored pubdate str>)
    assert await store.get_episode_location("nope") is None
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_episode_store.py -v -k "unskip or excludes_skipped or location"`

  Expected: FAIL (missing methods; skipped episode returned).

- [ ] **Step 3: Implement**

  - Add `AND skipped = 0` to the `get_episodes_for_feed` WHERE clause.
  - Add the two methods.

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean. Check `tests/test_pipeline.py` still passes, because publication now excludes skipped episodes.

- [ ] **Step 5: Commit**

```bash
git add database/episode_store.py tests/test_episode_store.py
git commit -m "feat(db): exclude skipped episodes from published feed; add unskip"
```

### Task 2: Route — `DELETE /api/v1/episodes/{guid}/skip` (re-enable)

**Files:**
- Modify: `api/routes/control.py`
- Test: `tests/test_api_control.py` (extend `_make_db_patch` with `unskip_episode_return: bool = True`)

**Interfaces:**
- Consumes: `EpisodeStore.unskip_episode` (Task 1).
- Produces: `DELETE /api/v1/episodes/{guid}/skip`, with responses:
  - 200 `{"status": "unskipped", "guid": guid}`
  - 404 `{"error": "episode not found: <guid>"}`
  - 409 while a run is active.

- [ ] **Step 1: Write the failing tests**

  - `test_unskip_returns_200_on_success`
  - `test_unskip_returns_404_when_not_found`
  - `test_unskip_returns_409_when_active`

  Mirror the three `TestSkipEpisode` tests, using `client.delete(...)`.

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_api_control.py -v -k unskip`

  Expected: FAIL (405 Method Not Allowed).

- [ ] **Step 3: Implement the handler next to `skip_episode_handler`, same structure**

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean.

- [ ] **Step 5: Commit**

```bash
git add api/routes/control.py tests/test_api_control.py
git commit -m "feat(api): add DELETE /episodes/{guid}/skip to re-enable an episode"
```

### Task 3: Route — `DELETE /api/v1/episodes/{guid}` (delete)

**Files:**
- Modify: `components/feed_publisher.py` (new static method next to `episode_filename` :72)
- Modify: `api/routes/db.py` (`_is_complete` uses the new glob helper)
- Modify: `api/routes/control.py` (router factory gains `output_dir`; new handler)
- Modify: `api/server.py` (pass `config.app.paths.output_dir`)
- Modify: `API.md` (§2.2 new endpoints, §7 quick reference, §1.3 note that skipped episodes are excluded from the RSS)
- Test: `tests/test_feed_publisher.py`, `tests/test_api_control.py`

**Interfaces:**
- Consumes:
  - `EpisodeStore.skip_episode`
  - `EpisodeStore.reset_episode(guid)` (full cascade)
  - `EpisodeStore.get_episode_location` (Task 1)
- Produces:
  - `FeedPublisher.episode_glob(pub_date: datetime, title: str) -> str` returns `"DD.MM.YYYY-<title-slug>.*"`. Use the same date formatting as `episode_filename` (`.astimezone()`).
  - `create_control_router(config_path: Path, db_path: Path, output_dir: Path, event_bus: EventBus, run_state: RunState)`
  - `DELETE /api/v1/episodes/{guid}`, with responses:
    - 200 `{"status": "deleted", "guid": guid, "files_removed": [<filename>, ...]}`
    - 404 if unknown
    - 409 while a run is active.

- [ ] **Step 1: Write the failing tests**

```python
def test_episode_glob_matches_episode_filename():
    d = datetime(2026, 3, 21, 12, tzinfo=UTC)
    assert fnmatch(FeedPublisher.episode_filename(d, "My Ep!", "mp3"),
                   FeedPublisher.episode_glob(d, "My Ep!"))

# tests/test_api_control.py — real SQLite in tmp_path (use database.connection.Database
# to create + seed), not the _make_db_patch mock, so the cascade is exercised for real:
async def test_delete_skips_resets_and_removes_output(tmp_path, monkeypatch):
    <seed episode g1 podcast "My Show" title "Ep" pubdate; seed transcriptions row for g1>
    out = tmp_path / "output" / "my-show"; out.mkdir(parents=True)
    f = out / FeedPublisher.episode_filename(<pubdate>, "Ep", "mp3"); f.write_bytes(b"x")
    resp = await client.delete("/api/v1/episodes/g1")
    assert resp.status == 200
    assert (await resp.json())["files_removed"] == [f.name]
    assert not f.exists()
    <assert episodes.skipped == 1 and no transcriptions row for g1>

async def test_delete_without_output_file_returns_empty_list(...)
    assert (await resp.json())["files_removed"] == []

async def test_delete_unknown_guid_returns_404(...)
async def test_delete_returns_409_when_active(...)
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_feed_publisher.py tests/test_api_control.py -v -k "glob or delete"`

  Expected: FAIL (no `episode_glob`; 405 on DELETE).

- [ ] **Step 3: Implement**

  - Add `episode_glob`. Switch `_is_complete` in `api/routes/db.py` to use it.
  - Handler order: 409 check → `get_episode_location` (404 if `None`) → `skip_episode` → `reset_episode` → glob `output_dir / slugify(podcast)` and `unlink()` the matches.
  - If `pubdate` is `None`, remove no files.
  - Wrap file removal in `asyncio.to_thread` or keep it sync; there are a handful of files at most, and sync matches `_trim_output_dir`.
  - Add the comment `# ponytail: RSS still lists the item until the next run regenerates it; add FeedPublisher.remove_episode if the gap matters`.

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean.

- [ ] **Step 5: Update `API.md` and commit**

```bash
git add components/feed_publisher.py api/ tests/ API.md
git commit -m "feat(api): add DELETE /episodes/{guid} — skip, purge data and output file"
```
