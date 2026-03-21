# Design: `--feed` CLI filter

**Date:** 2026-03-21

## Summary

Wire up the already-declared `--feed <name>` CLI argument so that it forces a
single, named feed through the pipeline — even if that feed is marked
`enabled: false` in the config.

## Background

`--feed` is already registered in `parse_args()` but is never forwarded
anywhere. `Pipeline.run()` currently selects feeds using a single filter:
`f.enabled`. Disabled feeds are therefore always skipped, with no escape hatch
from the command line.

## Goal

`uv run python main.py --feed "My Podcast"` should:

1. Ignore the `enabled` flag on the targeted feed.
2. Process only that one feed (all other feeds — enabled or not — are skipped).
3. Exit with a clear error message if no feed with that exact title exists.

## Design

### `Pipeline` constructor (Option A — constructor injection)

```python
class Pipeline:
    def __init__(self, config: Config, feed_name: str | None = None) -> None:
        ...
```

`feed_name` is stored as `self._feed_name`. The `run()` method remains a
zero-argument "go" button; filtering logic stays inside `run()`.

### `Pipeline.run()` filter logic

```
if feed_name is None:
    feeds = [f for f in all_feeds if f.enabled]   # existing behaviour
else:
    feeds = [f for f in all_feeds if f.title == feed_name]
    if not feeds:
        raise ValueError(
            f"No feed titled {feed_name!r}. "
            f"Available: {[f.title for f in all_feeds]}"
        )
```

### `main()` wiring

```python
pipeline = Pipeline(cfg, feed_name=args.feed)
```

`main()` catches `ValueError` from `pipeline.run()`, writes the message to
`stderr`, and calls `sys.exit(1)`.

## Error handling

| Situation | Behaviour |
|-----------|-----------|
| `--feed` not supplied | Existing behaviour unchanged (enabled feeds only) |
| `--feed` matches exactly one feed (any `enabled` value) | That feed processed alone |
| `--feed` matches no feed | `ValueError` → stderr message + exit 1 |

## Files changed

| File | Change |
|------|--------|
| `components/pipeline.py` | Add `feed_name` param to `__init__`; update `run()` filter |
| `main.py` | Pass `feed_name=args.feed` to `Pipeline`; catch `ValueError` |
| `tests/test_pipeline.py` | 3 new test cases (disabled feed forced, only target processed, unknown name) |
| `tests/test_main.py` | 2 new test cases: `test_feed_flag_sets_name` (`--feed myshow` → `args.feed == "myshow"`), `test_feed_defaults_to_none` (no flag → `args.feed is None`) |

## Out of scope

- Case-insensitive matching (exact match only, per spec)
- Partial / glob / regex matching
- Processing multiple feeds via repeated `--feed` flags
