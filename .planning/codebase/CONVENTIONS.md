# Coding Conventions

**Analysis Date:** 2026-03-28

## Naming Patterns

**Files:**
- Lowercase with underscores (snake_case): `episode_downloader.py`, `cost_tracking_store.py`
- Test files follow `test_{module_name}.py` pattern
- Module-level constants use UPPERCASE: `_EPISODES_SCHEMA`, `_CONTENT_TYPE_TO_EXT`, `PROVIDER_KEY_MAP`

**Functions:**
- Async functions use `async def`: `async def download(...)`, `async def probe(...)`
- Private helper functions prefix with single underscore: `_make_proc()`, `_ffprobe_json()`, `_branch_config()`
- Regular functions and methods use snake_case: `download_all()`, `save_cost()`, `get_episodes_for_feed()`
- Callback-style functions denoted by `_on_` prefix: `_on_download_progress()`, `_on_preprocess_progress()`

**Variables:**
- Local variables and parameters use snake_case: `guid`, `feed_slug`, `output_feed_dir`, `transcribed_guids`
- Instance attributes prefixed with underscore for "private" intent: `self._db_path`, `self._config`, `self._feed_downloader`
- Set types commonly used for lookups: `transcribed_guids: set[str]`, `extracted_guids: set[str]`

**Classes:**
- PascalCase for all classes: `Pipeline`, `AudioProber`, `EpisodeDownloader`, `CostTrackingStore`
- Exception classes inherit from base `PodcastAdCutterError`: `ConfigError`, `FfmpegError`, `TranscriptionError`, `AudioProbeError`
- Protocol/Structural types use descriptive PascalCase: `CostRecord`, `_ChannelExtras`, `_EpisodeExtras`

**Types:**
- Use `from __future__ import annotations` at file top for deferred evaluation
- Union types use `|` syntax (Python 3.10+): `feed_name: str | None`, `exc_type: type[BaseException] | None`
- Generic types spelled out: `list[FeedConfig]`, `dict[str, str]`, `set[str]`
- `TYPE_CHECKING` blocks protect circular imports: imports in `if TYPE_CHECKING:` block
- Type aliases declared with lowercase `type` keyword (3.12): `type ProgressCallback = Callable[[str, float], Awaitable[None]]`

## Code Style

**Formatting:**
- Line length: 120 characters (enforced by ruff)
- Target: Python 3.12 (ruff config specifies `target-version = "py312"`)
- Indentation: 4 spaces

**Linting:**
- Tool: ruff (with strict configuration)
- Config location: `pyproject.toml` under `[tool.ruff]` section
- Enabled: `select = ["ALL"]` — nearly all rules active
- Notable ignores:
  - `D203`: blank line before class docstring
  - `D213`: multi-line docstring summary on second line
  - `TRY003`: avoid long exception messages (allow descriptive messages)
  - `EM102`: avoid `.format()` for string formatting
  - `G004`: allow f-strings in logging (CLAUDE.md mandates f-strings)
  - `PLR0913`: allow functions with many parameters (config objects have multiple params)
  - `T201`: allow print statements (not applicable to async logging approach)

**Per-file relaxations** in `ruff.lint.per-file-ignores`:
  - Config loader: Relaxes `TC003`, `TC001` (type checking at runtime in Pydantic fields)
  - Test files: Relaxes `S101` (assert OK in tests), `ANN` (type hints optional), docstring rules, `SLF001` (direct access to private methods for testing), `PLR2004` (magic values in assertions are idiomatic)

**Import Organization:**
- Groups in order:
  1. `from __future__ import annotations` (always first)
  2. Standard library imports
  3. Third-party imports (`aiohttp`, `aiosqlite`, `pydantic`, etc.)
  4. Local imports (relative or absolute)
  5. `if TYPE_CHECKING:` imports block (optional type-only imports)

**Import style:**
- Absolute imports from project root: `from components.pipeline import Pipeline`, `from database.connection import Database`
- No relative imports (`.` or `..`)
- Standard library imports grouped and sorted
- Use `as` for renaming when necessary, but sparingly

## Error Handling

**Patterns:**
- All custom exceptions inherit from `PodcastAdCutterError` base class: `class FfmpegError(PodcastAdCutterError):`
- Exception classes accept `message: str` parameter and optional metadata (e.g., `stderr` in `FfmpegError`)
- Exceptions stored as attributes: `exc.message`, `exc.stderr`
- Raised with descriptive messages using f-strings: `raise ConfigError(f"No feed titled {self._feed_name!r}")`
- Caught broadly at orchestration level with `logger.exception()`: `except Exception: logger.exception(f"Episode '{episode.guid}': error, skipping")`
- Return-value approach for non-exceptional failures (e.g., HTTP 404 in `FeedDownloader` returns empty list rather than raising)

**Error context:**
- Include relevant identifiers in messages: `f"Episode '{guid}': {description}"`, `f"Feed '{feed.config_title}': ..."`
- Multi-line error details formatted with newlines: `detail = f"{message}\n{stderr.strip()}" if stderr.strip() else message`

## Logging

**Framework:** `logging` module (stdlib)

**Patterns:**
- Loggers created at module level: `logger = logging.getLogger(__name__)`
- All log messages use f-strings (never modulo operator %): `logger.info(f"Feed download complete: {len(results)} feed(s) retrieved")`
- Log levels by use:
  - `logger.debug()`: Progress updates, intermediate results, internal state: `logger.debug(f"Database opened: {self._db_path}")`
  - `logger.info()`: Significant milestones and user-facing progress: `logger.info(f"Pipeline starting: {len(selected)} enabled feed(s)")`
  - `logger.exception()`: Error context with full traceback: `logger.exception(f"Episode '{episode.guid}': error, skipping")`
- Progress callbacks use stderr directly for real-time updates without logging: `sys.stderr.write(f"\r  Episode '{guid}': {percent:.0%}")`

## Comments

**When to Comment:**
- Docstrings on all public classes and functions (module docstring + method docstring)
- Comment complex decision logic, especially branching: `# Branch A: both exist — reconstruct the URL from the existing file.`
- No redundant comments that restate code: `x = x + 1  # increment x` is bad
- TODOs and FIXMEs placed when workarounds exist or future work is blocked

**JSDoc/TSDoc:**
- Uses standard Python docstrings (not triple-quoted type hints)
- Format: Google-style docstrings
- Structure:
  ```
  """One-line summary.

  Extended description if needed.

  Args:
      param1: Description.
      param2: Description.

  Returns:
      Description of return value.

  Raises:
      ExceptionType: When this is raised.
  """
  ```

**Examples from codebase:**
```python
"""Pipeline — top-level orchestrator for the podcast ad-cutting workflow."""

class Pipeline:
    """Coordinates each stage of the podcast ad-cutting workflow.

    Pipeline is the sole owner of :class:`Config`.  It extracts the plain
    data each component needs and passes it through their APIs — no component
    below Pipeline imports from the config module.

    Args:
        config: Validated application config.
        feed_name: When set, process only the feed whose title matches this
            string exactly, regardless of its ``enabled`` flag.

    """

async def run(self) -> list[ParsedFeed]:
    """Execute the pipeline for the selected feeds.

    Returns:
        List of parsed feeds for every feed that was downloaded and
        parsed successfully, in config order.

    Raises:
        ValueError: If ``feed_name`` was supplied but no feed with that
            exact title exists in the config.

    """
```

## Function Design

**Size:**
- Prefer focused functions (typically < 50 lines)
- Long methods broken into private helpers with descriptive names
- Example in `Pipeline`: Main `run()` delegates to `_download()`, `_build_parse_inputs()`, `_select_feeds()`, `_process_episode()`

**Parameters:**
- Use keyword-only arguments for clarity in complex calls: `await self._episode_downloader.download(episode.guid, episode.url, on_progress=self._on_download_progress)`
- Methods requiring many parameters accept structured objects (config, models): `FeedConfig`, `ParsedFeed`, `PublisherInput`

**Return Values:**
- Return tuples for multiple related values: `(guid, transcription, segments, cost)` from `transcribe()`
- Return structured dataclasses/Pydantic models rather than untyped dicts: `Transcription`, `AudioMetadata`, `TopicExtraction`
- Async functions are coroutines returning awaitable types: `async def transcribe(...) -> tuple[str, Transcription, list[TranscriptionSegment], TranscriptionCost]:`

## Module Design

**Exports:**
- Top-level public API in each module clearly documented in docstrings
- Classes are main exports; helper functions are prefixed with underscore
- Example `CostTrackingStore`: Exports class and `CostRecord` protocol, keeps implementation details private

**Barrel Files:**
- Not used; imports are explicit and direct (`from components.pipeline import Pipeline`)
- Allows tools like IDEs and type checkers to trace imports accurately

**Protocols and Structural Types:**
- Use Python's `Protocol` from `typing` for structural subtyping: `class CostRecord(Protocol): ...`
- Allows flexible implementations without explicit inheritance
- Example: `CostTrackingStore.save_cost()` accepts any object with `provider`, `model`, `cost` fields

---

*Convention analysis: 2026-03-28*
