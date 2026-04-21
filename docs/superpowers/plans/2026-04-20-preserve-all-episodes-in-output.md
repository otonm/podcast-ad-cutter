# Preserve All Analysed Episodes in Output Folder — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every episode that completes ad analysis is copied to the local output folder and served from the RSS feed regardless of whether ads were detected, eliminating geo-CDN ad re-injection.

**Architecture:** Add an immutable `source_url` column to the episodes DB table (for re-download resilience). Wire the existing `EpisodeCopier` component into `Pipeline`. Replace the two Guard 2 early-return paths that preserved the original CDN URL with `EpisodeCopier.copy()` calls. Trim the output folder to `episodes_to_keep` files after each feed is processed.

**Tech Stack:** Python 3.12, aiosqlite, asyncio, slugify, pathlib, unittest.mock

---

## File Map

| Action | File | Change |
|---|---|---|
| Modify | `models/feed.py` | Add `source_url: str = ""` to `Episode` dataclass |
| Modify | `database/connection.py` | Add `source_url` migration (ALTER TABLE pattern) |
| Modify | `database/episode_store.py` | Include `source_url` in INSERT, SELECT, `_EpisodeRow`, `_row_to_episode` |
| Modify | `components/pipeline.py` | Import + wire `EpisodeCopier`; rework Guard 2; add `_trim_output_dir` |
| Modify | `tests/test_database_connection.py` | Add `source_url` column check |
| Modify | `tests/test_episode_store.py` | Add `source_url` round-trip tests |
| Modify | `tests/test_pipeline.py` | Update test infrastructure; replace/add/update Guard 2 and trim tests |

---

## Task 1: Add `source_url` field to the `Episode` model

**Files:**
- Modify: `models/feed.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_episode_store.py`, add this test at the bottom of the file:

```python
async def test_source_url_is_stored_and_retrieved(db_path: Path) -> None:
    """source_url round-trips through save/get unchanged."""
    ep = Episode(
        guid="src-url-ep",
        url="https://cdn.example.com/ep.mp3",
    )
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("Pod", [ep])
        result = await store.get_episodes_for_feed("Pod", limit=10)

    assert result[0].source_url == "https://cdn.example.com/ep.mp3"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/test_episode_store.py::test_source_url_is_stored_and_retrieved -v
```

Expected: `FAILED` — `Episode` has no `source_url` attribute.

- [ ] **Step 3: Add `source_url` to `Episode`**

In `models/feed.py`, add one field after `length`:

```python
    length: int = 0  # enclosure file size in bytes; updated after audio processing
    source_url: str = ""  # immutable original feed enclosure URL; never updated after first insert
```

- [ ] **Step 4: Run tests to confirm they still fail (model exists but DB column missing)**

```bash
uv run pytest tests/test_episode_store.py::test_source_url_is_stored_and_retrieved -v
```

Expected: `FAILED` — `source_url` column does not exist in DB yet.

---

## Task 2: Add `source_url` DB migration

**Files:**
- Modify: `database/connection.py`
- Modify: `tests/test_database_connection.py`

- [ ] **Step 1: Write two failing tests**

In `tests/test_database_connection.py`, add after the `test_length_column_migration_on_existing_db` test that already exists in `test_episode_store.py` (note: connection schema tests go in `test_database_connection.py`):

```python
async def test_episodes_table_has_source_url_column(db_path: Path) -> None:
    async with Database(db_path):
        pass
    assert "source_url" in await _column_names(db_path)


async def test_source_url_column_migration_on_existing_db(db_path: Path) -> None:
    """Database.__aenter__ must add source_url to a pre-existing episodes table that lacks it."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE episodes ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "podcast TEXT NOT NULL, title TEXT NOT NULL, pubdate TEXT, "
            "guid TEXT NOT NULL UNIQUE, url TEXT NOT NULL DEFAULT '', "
            "description TEXT, explicit INTEGER, duration TEXT, image_url TEXT, "
            "episode_type TEXT, itunes_author TEXT, itunes_subtitle TEXT, "
            "itunes_summary TEXT, content_encoded TEXT, link TEXT, author TEXT, "
            "itunes_title TEXT, episode_number INTEGER, season_number INTEGER, "
            "itunes_block INTEGER NOT NULL DEFAULT 0, "
            "length INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        await conn.commit()

    async with Database(db_path):
        pass

    assert "source_url" in await _column_names(db_path)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_database_connection.py::test_episodes_table_has_source_url_column tests/test_database_connection.py::test_source_url_column_migration_on_existing_db -v
```

Expected: both `FAILED`.

- [ ] **Step 3: Add migration to `database/connection.py`**

In `database/connection.py`, the `__aenter__` method already suppresses `OperationalError` to add the `length` column. Add a second suppressed ALTER immediately after it (lines 159–163 area):

```python
        with contextlib.suppress(aiosqlite.OperationalError):
            await self.conn.execute(
                "ALTER TABLE episodes ADD COLUMN length INTEGER NOT NULL DEFAULT 0"
            )
        with contextlib.suppress(aiosqlite.OperationalError):
            await self.conn.execute(
                "ALTER TABLE episodes ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"
            )
        await self.conn.commit()
```

- [ ] **Step 4: Run migration tests to confirm they pass**

```bash
uv run pytest tests/test_database_connection.py::test_episodes_table_has_source_url_column tests/test_database_connection.py::test_source_url_column_migration_on_existing_db -v
```

Expected: both `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add models/feed.py database/connection.py tests/test_database_connection.py tests/test_episode_store.py
git commit -m "feat(db): add source_url column to episodes — immutable original enclosure URL"
```

---

## Task 3: Include `source_url` in `EpisodeStore` INSERT and SELECT

**Files:**
- Modify: `database/episode_store.py`
- Modify: `tests/test_episode_store.py`

- [ ] **Step 1: Add a test that `update_episode_url` does NOT change `source_url`**

In `tests/test_episode_store.py`, add:

```python
async def test_update_episode_url_does_not_change_source_url(
    db_path: Path, episodes: list[Episode]
) -> None:
    """source_url must stay immutable even after url is updated."""
    async with Database(db_path) as db:
        store = EpisodeStore(db.conn)
        await store.save_episodes("My Podcast", episodes)
        await store.update_episode_url("guid-1", "https://local/processed.mp3")
        result = await store.get_episodes_for_feed("My Podcast", limit=10)

    ep1 = next(e for e in result if e.guid == "guid-1")
    assert ep1.url == "https://local/processed.mp3"
    assert ep1.source_url == "https://example.com/ep1.mp3"
```

- [ ] **Step 2: Run all episode store tests to see current state**

```bash
uv run pytest tests/test_episode_store.py -v
```

Expected: `test_source_url_is_stored_and_retrieved` FAILS (column exists now but INSERT/SELECT not updated yet). All other existing tests PASS.

- [ ] **Step 3: Update `_EpisodeRow` type alias**

In `database/episode_store.py`, replace the `_EpisodeRow` type alias. Add `str` at position 20:

```python
type _EpisodeRow = tuple[
    str,        # 0  guid
    str,        # 1  url
    str,        # 2  title
    str | None, # 3  pubdate
    str | None, # 4  description
    int | None, # 5  explicit_int
    str | None, # 6  duration
    str | None, # 7  image_url
    str | None, # 8  episode_type
    str | None, # 9  itunes_author
    str | None, # 10 itunes_subtitle
    str | None, # 11 itunes_summary
    str | None, # 12 content_encoded
    str | None, # 13 link
    str | None, # 14 author
    str | None, # 15 itunes_title
    int | None, # 16 episode_number
    int | None, # 17 season_number
    int,        # 18 itunes_block
    int,        # 19 length
    str,        # 20 source_url
]
```

- [ ] **Step 4: Update `save_episodes` INSERT**

In `database/episode_store.py`, `save_episodes` method — replace the `executemany` call and its SQL. The `rows` list building adds `ep.url` as the last item (the value to store in `source_url`):

```python
        rows = [
            (
                podcast,
                ep.title,
                ep.pub_date.isoformat() if ep.pub_date is not None else None,
                ep.guid,
                ep.url,
                ep.description,
                int(ep.explicit) if ep.explicit is not None else None,
                ep.duration,
                ep.image_url,
                ep.episode_type,
                ep.itunes_author,
                ep.itunes_subtitle,
                ep.itunes_summary,
                ep.content_encoded,
                ep.link,
                ep.author,
                ep.itunes_title,
                ep.episode_number,
                ep.season_number,
                int(ep.itunes_block),
                ep.length,
                ep.url,   # source_url — always the original URL; INSERT OR IGNORE keeps it immutable
            )
            for ep in episodes
        ]
        await self._conn.executemany(
            "INSERT OR IGNORE INTO episodes "
            "(podcast, title, pubdate, guid, url, description, explicit, duration, image_url, "
            "episode_type, itunes_author, itunes_subtitle, itunes_summary, content_encoded, "
            "link, author, itunes_title, episode_number, season_number, itunes_block, length, source_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
```

- [ ] **Step 5: Update `get_episodes_for_feed` SELECT**

In `database/episode_store.py`, `get_episodes_for_feed` — add `source_url` to the SELECT column list:

```python
        async with self._conn.execute(
            "SELECT guid, url, title, pubdate, description, explicit, duration, image_url, "
            "episode_type, itunes_author, itunes_subtitle, itunes_summary, content_encoded, "
            "link, author, itunes_title, episode_number, season_number, itunes_block, length, source_url "
            "FROM episodes WHERE podcast = ? ORDER BY pubdate DESC LIMIT ?",
            (podcast, limit),
        ) as cursor:
```

- [ ] **Step 6: Update `_row_to_episode`**

In `database/episode_store.py`, update the `_row_to_episode` function — add `source_url` to the destructuring tuple and to the `Episode(...)` constructor call:

```python
def _row_to_episode(row: _EpisodeRow) -> Episode:
    (
        guid,
        url,
        title,
        pubdate,
        description,
        explicit_int,
        duration,
        image_url,
        episode_type,
        itunes_author,
        itunes_subtitle,
        itunes_summary,
        content_encoded,
        link,
        author,
        itunes_title,
        episode_number,
        season_number,
        itunes_block_int,
        length,
        source_url,
    ) = row

    pub_date = datetime.fromisoformat(pubdate) if pubdate else datetime.now().astimezone()
    explicit: bool | None = None if explicit_int is None else bool(explicit_int)

    return Episode(
        guid=guid,
        url=url,
        title=title,
        pub_date=pub_date,
        description=description,
        explicit=explicit,
        duration=duration,
        image_url=image_url,
        episode_type=episode_type,
        itunes_author=itunes_author,
        itunes_subtitle=itunes_subtitle,
        itunes_summary=itunes_summary,
        content_encoded=content_encoded,
        link=link,
        author=author,
        itunes_title=itunes_title,
        episode_number=episode_number,
        season_number=season_number,
        itunes_block=bool(itunes_block_int),
        length=length,
        source_url=source_url,
    )
```

- [ ] **Step 7: Run all episode store tests**

```bash
uv run pytest tests/test_episode_store.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 8: Run full test suite**

```bash
uv run pytest --cov=. -q
```

Expected: all tests pass, 100% coverage.

- [ ] **Step 9: Commit**

```bash
git add database/episode_store.py tests/test_episode_store.py
git commit -m "feat(db): persist and retrieve source_url in EpisodeStore"
```

---

## Task 4: Wire `EpisodeCopier` and rework Guard 2

**Files:**
- Modify: `components/pipeline.py`
- Modify: `tests/test_pipeline.py`

### 4a — Update test infrastructure first (TDD: write failing tests before touching production code)

- [ ] **Step 1: Add `EpisodeCopier` to `_PATCHES` in `tests/test_pipeline.py`**

Find the `_PATCHES` tuple (around line 2205). Add `"components.pipeline.EpisodeCopier"` as the 19th entry:

```python
_PATCHES = (
    "components.pipeline.FeedDownloader",       # [0]
    "components.pipeline.FeedParser",            # [1]
    "components.pipeline.FeedPublisher",         # [2]
    "components.pipeline.Database",              # [3]
    "components.pipeline.EpisodeStore",          # [4]
    "components.pipeline.TranscriptionStore",    # [5]
    "components.pipeline.AudioMetadataStore",    # [6]
    "components.pipeline.CostTrackingStore",     # [7]
    "components.pipeline.EpisodeDownloader",     # [8]
    "components.pipeline.AudioProber",           # [9]
    "components.pipeline.AudioPreprocessor",     # [10]
    "components.pipeline.EpisodeTranscriptor",   # [11]
    "components.pipeline.AdStore",               # [12]
    "components.pipeline.TopicExtractor",        # [13]
    "components.pipeline.TopicStore",            # [14]
    "components.pipeline.AdDetector",            # [15]
    "components.pipeline.AdParser",              # [16]
    "components.pipeline.AudioEditor",           # [17]
    "components.pipeline.EpisodeCopier",         # [18]  ← NEW
)
```

- [ ] **Step 2: Add `m_episode_copier` to `_wire_branch_mocks`**

Find the `_wire_branch_mocks` function signature (around line 100). Add `m_episode_copier` as the 19th positional parameter and add its setup inside the function body:

```python
def _wire_branch_mocks(
    m_dl: MagicMock,
    m_fp: MagicMock,
    m_pub: MagicMock,
    m_db: MagicMock,
    m_store: MagicMock,
    m_ts: MagicMock,
    m_ams: MagicMock,
    m_cs: MagicMock,
    m_ep_dl: MagicMock,
    m_prober: MagicMock,
    m_prep: MagicMock,
    m_trans: MagicMock,
    m_ad_store: MagicMock,
    m_topic_ext: MagicMock,
    m_topic_store: MagicMock,
    m_ad_detector: MagicMock,
    m_ad_parser: MagicMock,
    m_audio_editor: MagicMock,
    m_episode_copier: MagicMock,  # ← NEW
    *,
    episodes: list[Episode],
    parsed: ParsedFeed,
    transcribed_guids: set[str],
    extracted_guids: set[str] | None = None,
    ad_segments: list[AdSegment] | None = None,
) -> None:
```

Inside the function body, add the `EpisodeCopier` mock setup after the `m_audio_editor` setup:

```python
    mock_copy_dest = MagicMock()
    mock_copy_dest.stat.return_value.st_size = 1024
    m_episode_copier.return_value.copy = AsyncMock(
        return_value=("ep-1", mock_copy_dest, "http://localhost/my-podcast/22.03.2026-my-episode.mp3")
    )
```

- [ ] **Step 3: Update every test that uses `_PATCHES` + `_wire_branch_mocks`**

Every test that currently does:
```python
with patch(_PATCHES[0]) as m_dl, ..., patch(_PATCHES[17]) as m_audio_editor:
    _wire_branch_mocks(
        m_dl, ..., m_audio_editor,
        episodes=..., parsed=..., transcribed_guids=...
    )
```

Must become:
```python
with patch(_PATCHES[0]) as m_dl, ..., patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:
    _wire_branch_mocks(
        m_dl, ..., m_audio_editor, m_episode_copier,
        episodes=..., parsed=..., transcribed_guids=...
    )
```

Apply this to all occurrences throughout the file. There are ~41 calls. The pattern is mechanical: add `patch(_PATCHES[18]) as m_episode_copier` to the `with` block and `m_episode_copier` as the 19th positional arg to `_wire_branch_mocks`.

- [ ] **Step 4: Update tests that use explicit `patch(...)` lists (not `_PATCHES`)**

Several tests list patches individually (e.g. the `test_per_episode_log_*` tests around line 2597). Each of those `with` blocks needs `patch("components.pipeline.EpisodeCopier")` added. These tests don't need the copier's return value configured (the mock default is fine) — just add the patch so `Pipeline.__init__` doesn't instantiate the real class.

Example — add one line to each such `with` block:
```python
        patch("components.pipeline.EpisodeCopier"),
```

- [ ] **Step 5: Replace `test_pipeline_does_not_instantiate_episode_copier`**

Find and delete `test_pipeline_does_not_instantiate_episode_copier` (around line 1484). Replace it with:

```python
async def test_pipeline_constructs_episode_copier() -> None:
    """Pipeline.__init__ must instantiate EpisodeCopier with output_dir and base_url."""
    config = _make_wiring_config()
    config.app.base_url = "https://example.com"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier") as mock_copier_cls,
    ):
        Pipeline(config)

    mock_copier_cls.assert_called_once_with(
        output_dir=config.app.paths.output_dir,
        base_url="https://example.com",
    )
```

- [ ] **Step 6: Replace the existing Guard 2 "skips download" test**

Find `test_guard2_empty_ad_segments_skips_download` (around line 2563). Replace it with two new tests that describe the new behavior:

```python
async def test_guard2_no_ad_segments_copies_to_output() -> None:
    """Guard 2: when no ad segments were detected, episode is copied to output folder."""
    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[],  # detection ran, found nothing
        )
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        await Pipeline(config).run()

    m_audio_editor.return_value.edit.assert_not_called()
    m_episode_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()


async def test_guard2_no_qualifying_cuts_copies_to_output() -> None:
    """Guard 2: when ad segments exist but all fall below thresholds, episode is copied."""
    config, ep, parsed = _branch_config(MagicMock())

    with patch(_PATCHES[0]) as m_dl, patch(_PATCHES[1]) as m_fp, patch(_PATCHES[2]) as m_pub, patch(_PATCHES[3]) as m_db, patch(_PATCHES[4]) as m_store, patch(_PATCHES[5]) as m_ts, patch(_PATCHES[6]) as m_ams, patch(_PATCHES[7]) as m_cs, patch(_PATCHES[8]) as m_ep_dl, patch(_PATCHES[9]) as m_prober, patch(_PATCHES[10]) as m_prep, patch(_PATCHES[11]) as m_trans, patch(_PATCHES[12]) as m_ad_store, patch(_PATCHES[13]) as m_topic_ext, patch(_PATCHES[14]) as m_topic_store, patch(_PATCHES[15]) as m_ad_detector, patch(_PATCHES[16]) as m_ad_parser, patch(_PATCHES[17]) as m_audio_editor, patch(_PATCHES[18]) as m_episode_copier:  # noqa: E501
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ad_store.return_value.get_detected_guids = AsyncMock(return_value={"ep-1"})
        m_ad_parser.return_value.parse = MagicMock(return_value=[])  # all below threshold
        await Pipeline(config).run()

    m_audio_editor.return_value.edit.assert_not_called()
    m_episode_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()
```

- [ ] **Step 7: Update `test_branch_b_transcription_exists_no_output_no_ads_keeps_original_url`**

Find this test (around line 1748). It tests that when `AudioEditor.edit()` returns `None`, the URL is NOT updated. Under the new design the URL IS always updated. Rename the test and flip the assertions:

Replace the existing test with:

```python
async def test_branch_b_audio_editor_returns_none_copies_original_to_output() -> None:
    """Branch B: AudioEditor returns None (all audio classified as ads) — original file is copied."""
    config, ep, parsed = _branch_config(MagicMock())

    with (
        patch("components.pipeline.FeedDownloader") as m_dl,
        patch("components.pipeline.FeedParser") as m_fp,
        patch("components.pipeline.FeedPublisher") as m_pub,
        patch("components.pipeline.Database") as m_db,
        patch("components.pipeline.EpisodeStore") as m_store,
        patch("components.pipeline.TranscriptionStore") as m_ts,
        patch("components.pipeline.AudioMetadataStore") as m_ams,
        patch("components.pipeline.CostTrackingStore") as m_cs,
        patch("components.pipeline.EpisodeDownloader") as m_ep_dl,
        patch("components.pipeline.AudioProber") as m_prober,
        patch("components.pipeline.AudioPreprocessor") as m_prep,
        patch("components.pipeline.EpisodeTranscriptor") as m_trans,
        patch("components.pipeline.AdStore") as m_ad_store,
        patch("components.pipeline.TopicExtractor") as m_topic_ext,
        patch("components.pipeline.TopicStore") as m_topic_store,
        patch("components.pipeline.AdDetector") as m_ad_detector,
        patch("components.pipeline.AdParser") as m_ad_parser,
        patch("components.pipeline.AudioEditor") as m_audio_editor,
        patch("components.pipeline.EpisodeCopier") as m_episode_copier,
    ):
        mock_copy_dest = MagicMock()
        mock_copy_dest.stat.return_value.st_size = 2048
        m_episode_copier.return_value.copy = AsyncMock(
            return_value=("ep-1", mock_copy_dest, "http://localhost/my-podcast/22.03.2026-my-episode.mp3")
        )
        _wire_branch_mocks(
            m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
            m_ep_dl, m_prober, m_prep, m_trans, m_ad_store, m_topic_ext, m_topic_store,
            m_ad_detector, m_ad_parser, m_audio_editor, m_episode_copier,
            episodes=[ep], parsed=parsed, transcribed_guids=set(),
            ad_segments=[_DEFAULT_AD_SEGMENT],
        )
        m_ts.return_value.get_segments_for_guid = AsyncMock(return_value=[])
        m_topic_store.return_value.get_topic_for_guid = AsyncMock(return_value=None)
        m_audio_editor.return_value.edit = AsyncMock(return_value=None)
        pipeline = Pipeline(config)
        await pipeline.run()

    m_episode_copier.return_value.copy.assert_awaited_once()
    m_store.return_value.update_episode_url.assert_awaited_once()
    m_pub.return_value.update_episode_url.assert_awaited_once()
```

- [ ] **Step 8: Run all pipeline tests to see the current failures**

```bash
uv run pytest tests/test_pipeline.py -v 2>&1 | head -60
```

Expected: multiple failures — `EpisodeCopier` not found in `components.pipeline`, and `_wire_branch_mocks` arg count mismatch.

### 4b — Implement the production code changes

- [ ] **Step 9: Import `EpisodeCopier` in `pipeline.py`**

In `components/pipeline.py`, add the import alongside the other component imports (around line 16):

```python
from components.episode_copier import EpisodeCopier
```

- [ ] **Step 10: Instantiate `EpisodeCopier` in `Pipeline.__init__`**

In `components/pipeline.py`, `Pipeline.__init__`, add after the `_audio_editor` instantiation (around line 135):

```python
        self._episode_copier = EpisodeCopier(
            output_dir=config.app.paths.output_dir,
            base_url=config.app.base_url,
        )
```

- [ ] **Step 11: Rework Guard 2 in `_process_episode_until_final`**

In `components/pipeline.py`, replace the entire Guard 2 block (lines 444–502) with:

```python
                # ── Guard 2: ad detection result cached → export audio ─────────
                if episode.guid in stores.ad_detected_guids:
                    ad_segments = await stores.ad.get_segments_for_guid(episode.guid)
                    logger.info(
                        f"Episode '{episode.guid}': ad detection cached, "
                        f"loading {len(ad_segments)} segment(s) from DB"
                    )

                    cut_ranges = (
                        self._ad_parser.parse(
                            ad_segments,
                            min_duration_ms=self._config.app.ad_detection.min_duration,
                            min_confidence=self._config.app.ad_detection.min_confidence,
                        )
                        if ad_segments
                        else []
                    )

                    # Audio may not be on disk if detection was cached from a previous run.
                    if raw_path is None:
                        logger.info(f"Episode '{episode.guid}': no cached audio; re-downloading")
                        fresh_urls = {ep.guid: ep.url for ep in feed.episodes}
                        download_url = (
                            fresh_urls.get(episode.guid)
                            or episode.source_url
                            or episode.url
                        )
                        raw_path = await self._episode_downloader.download(
                            episode.guid, download_url, on_progress=self._on_download_progress
                        )
                        meta = await self._audio_prober.probe(episode.guid, raw_path)
                        await stores.audio_metadata.save_all([meta])

                    if cut_ranges:
                        assert meta is not None
                        output_path = await self._audio_editor.edit(
                            episode.guid,
                            raw_path,
                            cut_ranges,
                            feed_slug,
                            episode.pub_date,
                            episode.title,
                            total_duration_s=meta.duration,
                        )
                        if output_path is not None:
                            new_url = FeedPublisher.episode_url(
                                self._config.app.base_url, feed_slug, episode.pub_date, episode.title,
                                self._config.app.output.file_type,
                            )
                            file_size = output_path.stat().st_size
                            await stores.episode.update_episode_url(episode.guid, new_url, file_size)
                            await self._feed_publisher.update_episode_url(
                                feed.title, episode.guid, new_url, file_size
                            )
                            return

                    # No qualifying cuts (or all audio classified as ads) — copy original.
                    logger.info(
                        f"Episode '{episode.guid}': no qualifying ad cuts — copying original audio to output"
                    )
                    _, dest_path, new_url = await self._episode_copier.copy(
                        episode.guid, raw_path, feed_slug, episode.pub_date, episode.title
                    )
                    file_size = dest_path.stat().st_size
                    await stores.episode.update_episode_url(episode.guid, new_url, file_size)
                    await self._feed_publisher.update_episode_url(
                        feed.title, episode.guid, new_url, file_size
                    )
                    return
```

- [ ] **Step 12: Run pipeline tests**

```bash
uv run pytest tests/test_pipeline.py -v 2>&1 | tail -30
```

Expected: all tests pass (or only trim tests fail, which are added in Task 5).

- [ ] **Step 13: Run full test suite**

```bash
uv run pytest --cov=. -q
```

Expected: all tests pass, 100% coverage.

- [ ] **Step 14: Commit**

```bash
git add components/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): wire EpisodeCopier and always export to output folder"
```

---

## Task 5: Add output folder trimming

**Files:**
- Modify: `components/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests for `_trim_output_dir`**

In `tests/test_pipeline.py`, add these tests at the end of the file:

```python
# ---------------------------------------------------------------------------
# Output folder trimming
# ---------------------------------------------------------------------------


async def test_trim_output_dir_removes_orphaned_files(tmp_path: Path) -> None:
    """Files whose stem does not match any current episode are deleted."""
    from datetime import UTC, datetime

    from components.pipeline import Pipeline
    from models.feed import Episode

    feed_dir = tmp_path / "my-feed"
    feed_dir.mkdir()
    # File for an episode still in the active window
    kept = feed_dir / "22.03.2026-my-episode.mp3"
    kept.write_bytes(b"keep me")
    # File for an old episode that has rolled out of the window
    orphan = feed_dir / "01.01.2020-old-episode.mp3"
    orphan.write_bytes(b"delete me")

    ep = Episode(
        guid="ep-1",
        url="https://example.com/ep.mp3",
        title="My Episode",
        pub_date=datetime(2026, 3, 22, tzinfo=UTC),
    )

    config = MagicMock()
    config.app.feeds = []
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "https://example.com"
    config.app.paths.output_dir = tmp_path
    config.app.paths.cache_dir = tmp_path / "cache"
    config.app.paths.data_dir = tmp_path / "data"
    config.app.paths.log_dir = tmp_path / "logs"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        pipeline = Pipeline(config)
        await pipeline._trim_output_dir(feed_dir, [ep])

    assert kept.exists()
    assert not orphan.exists()


async def test_trim_output_dir_keeps_all_current_episodes(tmp_path: Path) -> None:
    """No files are deleted when all files match current episodes."""
    from datetime import UTC, datetime

    from components.pipeline import Pipeline
    from models.feed import Episode

    feed_dir = tmp_path / "my-feed"
    feed_dir.mkdir()
    f1 = feed_dir / "22.03.2026-episode-one.mp3"
    f2 = feed_dir / "21.03.2026-episode-two.mp3"
    f1.write_bytes(b"a")
    f2.write_bytes(b"b")

    episodes = [
        Episode(guid="e1", url="x", title="Episode One", pub_date=datetime(2026, 3, 22, tzinfo=UTC)),
        Episode(guid="e2", url="x", title="Episode Two", pub_date=datetime(2026, 3, 21, tzinfo=UTC)),
    ]

    config = MagicMock()
    config.app.feeds = []
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "https://example.com"
    config.app.paths.output_dir = tmp_path
    config.app.paths.cache_dir = tmp_path / "cache"
    config.app.paths.data_dir = tmp_path / "data"
    config.app.paths.log_dir = tmp_path / "logs"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        pipeline = Pipeline(config)
        await pipeline._trim_output_dir(feed_dir, episodes)

    assert f1.exists()
    assert f2.exists()


async def test_trim_output_dir_noop_when_dir_missing(tmp_path: Path) -> None:
    """No error when the output feed directory does not exist yet."""
    from components.pipeline import Pipeline

    config = MagicMock()
    config.app.feeds = []
    config.app.models.transcription.provider = "groq"
    config.app.models.transcription.model = "whisper-large-v3-turbo"
    config.app.models.context_extraction.provider = "openai"
    config.app.models.context_extraction.model = "gpt-4o-mini"
    config.app.models.context_extraction.context_window = None
    config.app.models.ad_detection.provider = "openai"
    config.app.models.ad_detection.model = "gpt-4o-mini"
    config.app.models.ad_detection.context_window = None
    config.app.output.file_type = "mp3"
    config.app.output.bitrate = "128k"
    config.credentials.groq_api_key = "sk-test"
    config.credentials.openai_api_key = "sk-openai-test"
    config.app.base_url = "https://example.com"
    config.app.paths.output_dir = tmp_path
    config.app.paths.cache_dir = tmp_path / "cache"
    config.app.paths.data_dir = tmp_path / "data"
    config.app.paths.log_dir = tmp_path / "logs"

    with (
        patch("components.pipeline.FeedDownloader"),
        patch("components.pipeline.EpisodeDownloader"),
        patch("components.pipeline.AudioPreprocessor"),
        patch("components.pipeline.EpisodeTranscriptor"),
        patch("components.pipeline.TopicExtractor"),
        patch("components.pipeline.AdDetector"),
        patch("components.pipeline.AdParser"),
        patch("components.pipeline.AudioEditor"),
        patch("components.pipeline.EpisodeCopier"),
    ):
        pipeline = Pipeline(config)
        # Must not raise
        await pipeline._trim_output_dir(tmp_path / "nonexistent-feed", [])
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
uv run pytest tests/test_pipeline.py::test_trim_output_dir_removes_orphaned_files tests/test_pipeline.py::test_trim_output_dir_keeps_all_current_episodes tests/test_pipeline.py::test_trim_output_dir_noop_when_dir_missing -v
```

Expected: all three `FAILED` — `Pipeline` has no `_trim_output_dir` method.

- [ ] **Step 3: Implement `_trim_output_dir` in `pipeline.py`**

In `components/pipeline.py`, add this private method after `_on_preprocess_progress` (at the bottom of the class):

```python
    async def _trim_output_dir(self, output_feed_dir: Path, episodes: list[Episode]) -> None:
        """Delete output files for episodes no longer in the active window.

        Compares files in output_feed_dir against the expected stems derived from
        the current episodes list.  Any file whose stem is not in the expected set
        is an orphan from a previous run and is deleted.

        """
        if not output_feed_dir.is_dir():  # noqa: ASYNC240
            return
        expected_stems = {
            f"{ep.pub_date.strftime('%d.%m.%Y')}-{slugify(ep.title)}"
            for ep in episodes
        }
        for file in output_feed_dir.iterdir():  # noqa: ASYNC240
            if file.is_file() and file.stem not in expected_stems:  # noqa: ASYNC240
                file.unlink()
                logger.info(f"[{output_feed_dir.name}] trimmed orphaned episode file: {file.name}")
```

- [ ] **Step 4: Call `_trim_output_dir` after the episode loop in `run()`**

In `components/pipeline.py`, in `run()`, after the `for episode in episodes:` loop (after the `try/except/finally` block that wraps `_process_episode_until_final`), add:

```python
                # ── Trim output folder to episodes_to_keep ────────────────────
                await self._trim_output_dir(output_feed_dir, episodes)
```

The placement is: after the closing `finally:` block of the per-episode loop, still inside the `for feed in parsed_feeds:` loop.

- [ ] **Step 5: Run new trim tests**

```bash
uv run pytest tests/test_pipeline.py::test_trim_output_dir_removes_orphaned_files tests/test_pipeline.py::test_trim_output_dir_keeps_all_current_episodes tests/test_pipeline.py::test_trim_output_dir_noop_when_dir_missing -v
```

Expected: all three `PASSED`.

- [ ] **Step 6: Run full test suite + coverage + lint**

```bash
uv run pytest --cov=. -q && uv run ruff
```

Expected: all tests pass, 100% coverage, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add components/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): trim output folder to episodes_to_keep after each feed run"
```

---

## Verification

1. Run the complete test suite: `uv run pytest --cov=. -q` — all tests pass, 100% coverage.
2. Run the linter: `uv run ruff` — no errors.
3. Manual smoke test:
   - Run against a geo-CDN feed (e.g. Acast)
   - Confirm every RSS enclosure URL points to `{base_url}/{feed_slug}/...` (never the CDN)
   - Confirm the output folder has exactly `episodes_to_keep` audio files
   - Re-run — Guard 1 fires for all episodes (no re-processing)
   - Decrease `episodes_to_keep` by 1 in config, re-run — oldest file deleted, RSS drops that episode
