# `--feed` CLI Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up the already-declared `--feed <name>` CLI argument so that it forces a single named feed through the pipeline, ignoring the `enabled` flag.

**Architecture:** `Pipeline.__init__` gains an optional `feed_name: str | None = None` parameter stored as `self._feed_name`. `Pipeline.run()` uses it to select the target feed (bypassing the `enabled` filter); if the name is not found it raises `ValueError`. `main()` passes `args.feed` to `Pipeline` and catches `ValueError` to exit cleanly.

**Tech Stack:** Python 3.12, argparse (stdlib), pytest, ruff

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `components/pipeline.py` | Modify | Add `feed_name` to `__init__`; update `run()` filter logic |
| `main.py` | Modify | Pass `feed_name=args.feed` to `Pipeline`; catch `ValueError` |
| `tests/test_pipeline.py` | Modify | Add 3 tests for name-based filtering |
| `tests/test_main.py` | Modify | Add 2 tests for `--feed` argument parsing |

---

## Task 1: Pipeline — name-based feed filtering

**Files:**
- Modify: `components/pipeline.py`
- Test: `tests/test_pipeline.py`

### Step 1.1 — Write failing tests

Add these three test functions to `tests/test_pipeline.py`, after the existing tests:

```python
async def test_run_with_feed_name_forces_disabled_feed() -> None:
    """--feed must process a disabled feed, ignoring enabled=False."""
    disabled = make_feed("target", enabled=False)
    other = make_feed("other", enabled=True)
    config = make_config([disabled, other])

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[(disabled, "<xml/>")])
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([disabled])


async def test_run_with_feed_name_excludes_other_feeds() -> None:
    """--feed must pass only the named feed, even when others are enabled."""
    target = make_feed("target", enabled=True)
    other = make_feed("other", enabled=True)
    config = make_config([target, other])

    with patch("components.pipeline.FeedDownloader") as mock_downloader_cls:
        mock_dl = mock_downloader_cls.return_value
        mock_dl.download_all = AsyncMock(return_value=[(target, "<xml/>")])
        pipeline = Pipeline(config, feed_name="target")
        await pipeline.run()

    mock_dl.download_all.assert_called_once_with([target])


async def test_run_with_unknown_feed_name_raises() -> None:
    """--feed with a title that matches no feed must raise ValueError."""
    feed = make_feed("existing")
    config = make_config([feed])

    with patch("components.pipeline.FeedDownloader"):
        pipeline = Pipeline(config, feed_name="nonexistent")
        with pytest.raises(ValueError, match="nonexistent"):
            await pipeline.run()
```

Add `import pytest` to the imports at the top of `tests/test_pipeline.py` (it is not currently imported there).

- [ ] **Step 1.1: Add the three test functions and the `pytest` import to `tests/test_pipeline.py`**

- [ ] **Step 1.2: Run the new tests to confirm they all fail**

```bash
uv run pytest tests/test_pipeline.py::test_run_with_feed_name_forces_disabled_feed tests/test_pipeline.py::test_run_with_feed_name_excludes_other_feeds tests/test_pipeline.py::test_run_with_unknown_feed_name_raises -v
```

Expected: 3 FAILs (TypeError — `Pipeline` does not accept `feed_name`).

### Step 1.3 — Implement in `components/pipeline.py`

Replace the current `Pipeline` class with:

```python
class Pipeline:
    """Coordinates each stage of the podcast ad-cutting workflow.

    Currently the pipeline performs a single stage: downloading the RSS/Atom
    XML for the selected feeds, in the order they appear in the config.
    Further stages (transcription, ad detection, audio cutting) will be
    added here as new components.

    Args:
        config: Validated application config.
        feed_name: When set, process only the feed whose title matches this
            string exactly, regardless of its ``enabled`` flag.  When
            ``None`` (default), only feeds marked ``enabled: true`` are
            processed.

    """

    def __init__(self, config: Config, feed_name: str | None = None) -> None:
        self._config = config
        self._feed_name = feed_name
        self._feed_downloader = FeedDownloader(config)

    async def run(self) -> list[tuple[FeedConfig, str]]:
        """Execute the pipeline for the selected feeds.

        Returns:
            List of ``(feed_config, xml_text)`` for every feed that was
            downloaded successfully, in config order.

        Raises:
            ValueError: If ``feed_name`` was supplied but no feed with that
                exact title exists in the config.

        """
        all_feeds = self._config.app.feeds

        if self._feed_name is not None:
            # Force a specific feed through regardless of its enabled flag.
            selected = [f for f in all_feeds if f.title == self._feed_name]
            if not selected:
                available = [f.title for f in all_feeds]
                msg = (
                    f"No feed titled {self._feed_name!r}. "
                    f"Available titles: {available}"
                )
                raise ValueError(msg)
            logger.info(f"Pipeline starting: forcing feed '{self._feed_name}' (enabled override)")
        else:
            selected = [f for f in all_feeds if f.enabled]
            logger.info(
                f"Pipeline starting: {len(selected)} enabled feed(s) of "
                f"{len(all_feeds)} total"
            )

        results = await self._feed_downloader.download_all(selected)
        logger.info(f"Feed download complete: {len(results)} feed(s) retrieved")
        return results
```

- [ ] **Step 1.3: Replace the `Pipeline` class in `components/pipeline.py` with the implementation above**

- [ ] **Step 1.4: Run new tests to confirm they pass**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: all tests pass, including the 3 new ones and the 4 existing ones.

- [ ] **Step 1.5: Run ruff**

```bash
uv run ruff check components/pipeline.py tests/test_pipeline.py
```

Expected: no errors.

- [ ] **Step 1.6: Commit**

```bash
git add components/pipeline.py tests/test_pipeline.py
git commit -m "feat: add feed_name filter to Pipeline, bypass enabled flag"
```

---

## Task 2: `main()` — wire CLI argument and handle error

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

### Step 2.1 — Write failing tests

Add these two test methods to the `TestParseArgs` class in `tests/test_main.py`:

```python
def test_feed_flag_sets_name(self) -> None:
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["main.py", "--feed", "My Podcast"])
        args = parse_args()
    assert args.feed == "My Podcast"

def test_feed_defaults_to_none(self) -> None:
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(sys, "argv", ["main.py"])
        args = parse_args()
    assert args.feed is None
```

- [ ] **Step 2.1: Add the two test methods to `TestParseArgs` in `tests/test_main.py`**

- [ ] **Step 2.2: Run the new tests to confirm they pass immediately**

```bash
uv run pytest tests/test_main.py::TestParseArgs::test_feed_flag_sets_name tests/test_main.py::TestParseArgs::test_feed_defaults_to_none -v
```

Expected: both PASS — `--feed` is already declared in `parse_args()`, so no implementation change is needed here. These tests document the existing behaviour.

### Step 2.3 — Wire `args.feed` into `Pipeline` and handle `ValueError`

In `main.py`, update the `main()` coroutine. The `pipeline` construction and `run` call currently look like:

```python
pipeline = Pipeline(cfg)
await pipeline.run()
```

Replace those two lines with:

```python
pipeline = Pipeline(cfg, feed_name=args.feed)
try:
    await pipeline.run()
except ValueError as exc:
    sys.stderr.write(f"Error: {exc}\n")
    sys.exit(1)
```

- [ ] **Step 2.3: Update `main()` in `main.py` as shown above**

- [ ] **Step 2.4: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 2.5: Run ruff**

```bash
uv run ruff check main.py tests/test_main.py
```

Expected: no errors.

- [ ] **Step 2.6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "feat: wire --feed CLI flag into Pipeline, handle unknown feed error"
```

---

## Done

After both tasks, the feature is complete:

- `uv run python main.py --feed "My Podcast"` processes only that feed, enabled or not.
- `uv run python main.py --feed "Unknown"` prints a clear error and exits 1.
- `uv run python main.py` behaves exactly as before.
