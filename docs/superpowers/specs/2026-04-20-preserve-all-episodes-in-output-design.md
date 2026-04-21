# Design: Preserve All Analysed Episodes in Output Folder

**Date:** 2026-04-20
**Status:** Approved

---

## Context

Podcast feeds that use geo-aware CDNs (e.g. Acast) serve different audio files depending on the client's geographic location. The pipeline downloads and analyses the episode from one URL, but a podcast app in another region resolves that same enclosure URL to a different server — which may inject its own ads. Because the pipeline only copies audio to the output folder when qualifying ad cuts are found, episodes with no detected ads keep the original CDN URL in the RSS. Clients in other regions then download a different, ad-laden version of that file.

The fix: every episode that completes analysis must be published from the local output folder, regardless of whether ads were detected. The RSS enclosure URL always points to the server we control.

Additionally, the output folder must be trimmed to `episodes_to_keep` files per feed so that disk usage does not grow unboundedly.

---

## What Changes

| File | Change |
|---|---|
| `components/pipeline.py` | Wire `EpisodeCopier`; rework Guard 2; add output trimming |
| `database/connection.py` | Add `source_url` migration (existing `ALTER TABLE` pattern) |
| `database/episode_store.py` | Include `source_url` in INSERT / SELECT / `_row_to_episode` |
| `models/feed.py` | Add `source_url: str = ""` field to `Episode` |
| `components/episode_copier.py` | No change — already correct and fully tested |
| All other components | No change |

---

## Architecture

### `source_url` — Immutable Original URL

`Episode.source_url` stores the original feed enclosure URL as it appeared on first insert. Because `save_episodes` uses `INSERT OR IGNORE`, `source_url` is written once and never overwritten, even after `episode.url` is updated to a local path. This field serves two purposes:

1. **Re-download resilience.** If an episode's output file is missing (trimmed or manually deleted) and Guard 2 needs to re-download, the pipeline uses `source_url` instead of the potentially-dead local `url`.
2. **Fixes a pre-existing bug.** The current Guard 2 re-download uses `episode.url`, which is already a dead local URL for any previously-processed episode whose output file was removed.

**Migration:** added via `ALTER TABLE episodes ADD COLUMN source_url TEXT NOT NULL DEFAULT ''` wrapped in `contextlib.suppress(aiosqlite.OperationalError)` — the same pattern already used for the `length` column in `database/connection.py`.

**INSERT detail:** `save_episodes` writes `ep.url` into the `source_url` column (i.e. the original enclosure URL at first-seen time). Because `INSERT OR IGNORE` skips duplicate GUIDs, this value is never overwritten even as `url` is later mutated to the local path. The `source_url` field on the `Episode` dataclass (default `""`) is populated when rows are read back from the DB via `get_episodes_for_feed`.

### Guard 2 Rework

Current Guard 2 has two early `return` paths that preserve the original URL when no qualifying ads exist. Both are replaced with a unified copy path.

New Guard 2 flow:

```
Guard 2 fires (ad detection cached in DB):
  1. Load ad_segments from AdStore
  2. If ad_segments → parse cut_ranges via AdParser (confidence + duration filters)
  3. Ensure audio is on disk:
       if raw_path is None:
           download_url = fresh_url from feed.episodes
                          OR episode.source_url
                          OR episode.url   ← legacy fallback for pre-migration rows
           raw_path = await EpisodeDownloader.download(guid, download_url)
           meta     = await AudioProber.probe(guid, raw_path)
           await AudioMetadataStore.save_all([meta])
  4. If cut_ranges:
       output_path = await AudioEditor.edit(...)
       if output_path is not None:
           → update URL in DB + RSS to edited file → return
       # else: all audio classified as ads → fall through to copy
  5. No qualifying cuts (or all-audio-is-ads):
       _, dest, new_url = await EpisodeCopier.copy(guid, raw_path, feed_slug, pub_date, title)
       → update URL in DB + RSS to copied file → return
```

**Removed dead code:**
- `if not ad_segments: ... return` (keep original URL)
- `if output_path is None: ... return` (keep original URL after failed edit)

### Output Trimming

A new private method `_trim_output_dir(output_feed_dir, episodes)` runs once per feed after all episodes in Phase 3 are processed:

```python
expected_stems = {
    f"{ep.pub_date.strftime('%d.%m.%Y')}-{slugify(ep.title)}"
    for ep in episodes
}
for file in output_feed_dir.glob("*"):
    if file.is_file() and file.stem not in expected_stems:
        file.unlink()
        logger.info(f"[trim] removed orphaned episode file: {file}")
```

After a complete run, the output folder contains exactly the N files that correspond to the N episodes in the RSS.

---

## Error Handling

- Re-download failure and copy failure both raise; the outer `except Exception` handler in `Pipeline.run()` skips the episode and logs the error — unchanged behaviour.
- Trim `unlink` failures are logged and swallowed; an orphaned file is non-critical.

---

## Tests

| Test file | What changes |
|---|---|
| `tests/test_episode_store.py` | `save_episodes` and `get_episodes_for_feed` assertions include `source_url` |
| `tests/test_database_connection.py` | Assert `source_url` column present after schema initialisation |
| `tests/test_pipeline.py` | Guard 2 now expects `EpisodeCopier.copy()` for no-ad episodes; add trim assertions |
| `tests/test_episode_copier.py` | No changes — already complete |

---

## Verification

1. Run `uv run pytest --cov=.` — all tests pass, 100% coverage.
2. Run `uv run ruff` — no errors.
3. Manual smoke test:
   - Run pipeline against a feed that uses Acast (or any geo-CDN)
   - Confirm every episode in the RSS has a local URL (not a CDN URL)
   - Confirm the output folder has exactly `episodes_to_keep` audio files
   - Confirm the RSS and output folder are in sync
4. Re-run pipeline — Guard 1 fires for all episodes (output files exist), no re-processing.
5. Decrease `episodes_to_keep` by 1, re-run — oldest file deleted, RSS drops that episode.
