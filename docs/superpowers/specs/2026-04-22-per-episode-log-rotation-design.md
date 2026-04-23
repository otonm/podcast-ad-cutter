# Per-Episode Log Rotation Design

**Date:** 2026-04-22

## Context

Per-episode logs were introduced to capture full debug output for a single episode's pipeline run, useful for diagnosing missed ad cuts. They are written to a flat directory (`logs/episodes/`) using a filename that combines timestamp, podcast slug, and episode slug. Two problems exist:

1. **No rotation** — logs accumulate indefinitely; the existing `rotate`/`keep_last` config only prunes top-level run logs (`logs/*.log`), not episode logs.
2. **Messy naming** — all feeds and episodes are mixed in one directory, making it hard to find logs for a specific episode.

## Goal

- Restructure per-episode log paths to `logs/episodes/<feed-slug>/<episode-slug>.<timestamp>.log` so each feed gets its own subdirectory and episode runs are sorted chronologically within that slug.
- Add rotation: after each episode log closes, prune logs in the feed subdirectory so each episode slug retains at most `keep_last` files. Gated by the existing `rotate: true` config flag.

## Scope

This change touches three files:

| File | Change |
|------|--------|
| `utils/episode_log.py` | New path structure; new `rotate_episode_logs` function |
| `components/pipeline.py` | Two new attrs; call rotation after close |
| `config.example.yaml` | Update `per_episode` comment to show new path |
| `tests/test_episode_log.py` | Update path tests; add rotation tests |

Config schema (`config/config_loader.py`) is **unchanged** — `rotate` and `keep_last` already exist.

## Architecture

### Path change

```
# Before
logs/episodes/<timestamp>.<podcast-slug>.<episode-slug>.log

# After
logs/episodes/<podcast-slug>/<episode-slug>.<timestamp>.log
```

`open_episode_log` creates the feed subdirectory (`episodes_dir / podcast_slug /`) and writes the log there. The `podcast_title` parameter is slugified as before.

### Return value change for `open_episode_log`

The function now returns a 3-tuple `(episode_logger, handler, log_path)`.

`log_path` is the `Path` of the written log file. `log_path.parent` is the feed subdirectory and is the authoritative source for the rotation call — no slug recalculation needed in the pipeline.

> **Why this matters:** `pipeline.py` computes `feed_slug = slugify(feed.title)` (the RSS channel title), but `open_episode_log` receives `podcast_title=feed.config_title` (the config YAML title) and slugifies that internally. These two slugs can differ. Returning `log_path` eliminates the ambiguity entirely.

### New function: `rotate_episode_logs`

```python
def rotate_episode_logs(feed_dir: Path, keep_last: int) -> None:
    """Prune per-episode logs in feed_dir, keeping keep_last per episode slug."""
```

- Groups `*.log` files in `feed_dir` by episode slug (derived as `f.stem.rsplit(".", 1)[0]`)
- Slugs produced by `slugify` never contain dots, so splitting on the last dot safely isolates the timestamp suffix
- For each group, sorts by mtime (ascending), deletes all but the `keep_last` most recent
- `keep_last == 0` deletes all files in the group
- No-ops silently if `feed_dir` does not exist

### Pipeline changes (`components/pipeline.py`)

Two new attrs added in `__init__`:

```python
self._log_rotate: bool = config.app.log.rotate
self._log_keep_last: int = config.app.log.keep_last
```

In the per-episode loop:

```python
log_path = None
if self._per_episode_log:
    _, handler, log_path = open_episode_log(...)
try:
    ...
finally:
    if handler is not None:
        close_episode_log(handler)
    if log_path is not None and self._log_rotate:
        rotate_episode_logs(log_path.parent, self._log_keep_last)
```

## Data Flow

```
pipeline.run()
  for feed:
    for episode:
      _, handler, log_path = open_episode_log(podcast_title=feed.config_title, ...)
        → writes to log_dir/episodes/<config-title-slug>/<episode-slug>.<ts>.log
        → returns log_path so caller has the exact feed dir
      ... episode processing ...
      close_episode_log(handler)
      if rotate:
        rotate_episode_logs(log_path.parent, keep_last)
          → groups by episode slug, deletes oldest exceeding keep_last
```

## Example

Config: `keep_last: 3`, 2 feeds, 5 episodes each, 10 pipeline runs:

```
logs/episodes/
  my-podcast/
    episode-one.2026-04-20T10-00-00.log   ← kept (3rd most recent)
    episode-one.2026-04-21T10-00-00.log   ← kept
    episode-one.2026-04-22T10-00-00.log   ← kept
    episode-two.2026-04-20T10-00-00.log   ← kept
    ...
  another-show/
    ...
```

Max files: `keep_last × episodes_per_feed × num_feeds`.

## Error Handling

- `feed_dir` missing → no-op (no episodes have been logged for this feed yet)
- File deletion errors → propagate (unexpected; likely a permissions issue worth surfacing)

## Testing

`TestRotateEpisodeLogs` class in `tests/test_episode_log.py`:

- `test_keeps_keep_last_most_recent_per_episode_slug` — creates N files per slug, asserts only keep_last survive
- `test_deletes_oldest_by_mtime` — verifies mtime ordering drives deletion, not filename order
- `test_keep_last_zero_deletes_all` — keep_last=0 removes every file in the group
- `test_does_not_affect_other_episode_slugs` — rotation for slug A does not touch slug B files
- `test_noop_when_feed_dir_missing` — no exception raised
- `test_noop_when_fewer_files_than_keep_last` — nothing deleted when count ≤ keep_last

Existing `TestOpenEpisodeLog` tests updated:
- `test_log_filename_matches_expected_pattern` → new pattern `<episode-slug>.<timestamp>.log`
- `test_creates_episodes_subdirectory` → now checks feed subdirectory exists
- All callers updated from 2-tuple to 3-tuple unpack (`_, handler, log_path = open_episode_log(...)`)

## Verification

```bash
uv run pytest tests/test_episode_log.py -v   # unit tests pass
uv run pytest --cov=.                         # coverage 100%
uv run ruff                                   # no lint errors
```

Manual smoke test: enable `per_episode: true`, `rotate: true`, `keep_last: 2` in config, run the pipeline twice on the same feed. Confirm only 2 log files per episode slug remain under `logs/episodes/<feed-slug>/`.
