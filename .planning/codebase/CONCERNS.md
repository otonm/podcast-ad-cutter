# Codebase Concerns

**Analysis Date:** 2026-05-14

---

## High

### Broad `except Exception` silently skips failed episodes

**Issue:** `Pipeline._process_episode` wraps each `_process_episode_until_final` call in `except Exception` with only a log line. Any raised exception — including programming errors, data corruption, or transient infrastructure failures — causes the episode to be silently skipped for that run.
**Files:** `components/pipeline.py:237`
**Impact:** Bugs introduced in inner components produce no actionable failure signal. Operators cannot distinguish "skipped due to prior processing" from "skipped due to crash."
**Fix approach:** Re-raise after logging (or raise a domain-specific `EpisodeProcessingError`) and let the caller decide whether to continue to the next episode. At minimum, add a counter/metric for skipped episodes.

### Global GUID sets load all-feeds data with no per-feed filter

**Issue:** `TranscriptionStore.get_transcribed_guids()`, `TopicStore.get_extracted_guids()`, and `AdStore.get_detected_guids()` issue unfiltered `SELECT guid FROM <table>` queries and return every GUID in the database, regardless of which podcast feed it belongs to. The `_Stores` dataclass is constructed fresh for each feed but always receives the full cross-feed set.
**Files:** `components/pipeline.py:202-214`, `database/transcription_store.py:41`, `database/topic_store.py:31`, `database/ad_store.py:33`
**Impact:** On databases with many feeds and thousands of episodes, the in-memory sets grow without bound and DB reads become proportionally expensive at every pipeline invocation.
**Fix approach:** Add a `podcast` parameter to each `get_*_guids()` method and filter by the feed's config title, mirroring the `podcast` column already present in `topic_extractions` and `episodes` tables.

---

## Medium

### Duplicated LLM retry state machine

**Issue:** `AdDetector.detect()` and `TopicExtractor.extract()` each implement their own retry loop including: context-window exceeded fallback, JSON schema fallback, single-index retry, reasoning toggle, and cost accumulation. The logic is near-identical but maintained independently.
**Files:** `components/ad_detector.py:321-440`, `components/topic_extractor.py:213-330`
**Impact:** Any bug fix or enhancement (e.g., adding 429 backoff) must be applied in two places. Current divergence risk is low but grows with each addition to either component.
**Fix approach:** Extract a shared `LLMRetryLoop` helper (likely in `utils/llm.py`) accepting strategy callbacks for prompt-building, response-parsing, and fallback decisions.

### Schema migration via `contextlib.suppress` — no version tracking

**Issue:** Two `ALTER TABLE` migrations are applied speculatively by suppressing `aiosqlite.OperationalError`. A genuine error (disk full, permissions issue, schema corruption) is indistinguishable from the expected "column already exists" error and is silently swallowed.
**Files:** `database/connection.py:160-167`
**Impact:** A failed migration is invisible at startup. The column is absent at runtime but no exception surfaces until a write attempt fails later.
**Fix approach:** Introduce a `schema_version` integer table. Apply each migration only when the tracked version is lower than the target, raise on any error, and increment the version on success.

### Non-atomic RSS file write in `FeedPublisher.update_episode_url`

**Issue:** The `.rss` file is already written atomically via `write_text` + `replace` (temp file pattern). This concern was addressed in commit `a08feaf`. Current code at `components/feed_publisher.py:181-183` uses `tmp_path.write_text` then `tmp_path.replace`.
**Files:** `components/feed_publisher.py:181-183`
**Status:** Resolved — write is atomic. No action needed.

### LLM 429 rate-limit errors not retried within a single run

**Issue:** A transient HTTP 429 from the LLM provider propagates as an untyped exception through `_call_llm`, is not caught by the retry loop's specific exception branches, and reaches `_process_episode_until_final`, where the outer `except Exception` skips the episode. The next run re-attempts from scratch (no state is persisted for a mid-run 429 failure), but within the failing run there is no backoff.
**Files:** `components/ad_detector.py:360-410`, `components/topic_extractor.py:257-330`
**Impact:** In a multi-episode run, a brief rate-limit window causes all subsequent episodes to be skipped rather than retried after a delay.
**Fix approach:** Catch `litellm.RateLimitError` (or its equivalent) explicitly in `_call_llm` and add exponential backoff before re-raising or retrying.

### `podcast_guid` derived from slugified feed title

**Issue:** `podcast_guid` in the published RSS is computed as `uuid.uuid5(uuid.NAMESPACE_DNS, slugify(feed.title))`. Renaming a feed's `title` in `config.yaml` silently generates a different UUID on the next run.
**Files:** `components/pipeline.py:292`
**Impact:** Podcast directory services (Pocket Casts, Apple Podcasts) that track feeds by `<podcast:guid>` will treat the renamed feed as a new podcast, breaking subscriptions. This is a known limitation documented in `.claude/` memory.
**Fix approach:** Store the `podcast_guid` in the database on first publish and reuse it on subsequent runs, falling back to the slug-derived UUID only when no persisted value exists.

### Chunked transcription joins text with a plain space — no boundary handling

**Issue:** When `EpisodeTranscriptor._transcribe_chunked` joins chunk transcriptions, it uses `" ".join(all_text)`. If a sentence is split across a chunk boundary, the joined text may have doubled spaces or missing punctuation. Segment timestamps are correctly offset but the raw text field is not corrected.
**Files:** `components/episode_transcriptor.py:169`
**Impact:** The topic extractor and ad detector receive transcript text with potential word-boundary artifacts at every chunk join point (~every 98 minutes of audio). LLM context quality degrades slightly for very long episodes.
**Fix approach:** Strip trailing/leading whitespace from each chunk text before joining, or use a sentence-boundary-aware join strategy.

### `_process_episode_until_final` exceeds complexity thresholds

**Issue:** The episode state machine is ~230 lines with nested try/finally, five conditional guards, and multiple in-line DB interactions. Three ruff complexity rules (`C901`, `PLR0912`, `PLR0915`) are suppressed via `# noqa`.
**Files:** `components/pipeline.py:370` (suppression comment on function signature)
**Impact:** Low immediate risk due to full test coverage, but the function is difficult to extend safely. Adding a new pipeline stage (e.g., chapter injection) requires careful insertion into the guard chain.
**Fix approach:** Refactor into a state enum + handler dispatch table where each state's handler is a separate method. Not a blocker given current test coverage.

---

## Low

### `AdDetector.detect` suppresses all broad exceptions on model-info lookup

**Issue:** `AdDetector._get_context_window()` catches `Exception` broadly when calling `litellm.get_model_info()`, then falls back to a hardcoded 16384 token limit. An unexpected error (e.g., network issue in litellm's internal model DB lookup) silently degrades to an incorrect context window.
**Files:** `components/ad_detector.py:195`
**Impact:** If the true context window is smaller than 16384 tokens, the LLM call will exceed it and trigger the context-window retry path. If larger, tokens are wasted.
**Fix approach:** Log the specific exception type so operators can distinguish a missing model entry from a genuine error.

### iTunes category hierarchy flattened on round-trip

**Issue:** If a source RSS feed has two separate top-level `<itunes:category>` elements (e.g., `Technology` and `Arts`), the publisher collapses them. The second top-level category is lost on republish.
**Files:** `components/feed_publisher.py` (category builder section, approximately `_add_categories`)
**Impact:** Feeds with multiple independent top-level categories are mis-categorised in podcast directories after republishing.
**Fix approach:** Iterate all `ParsedFeed.categories` entries and emit each as a separate top-level `<itunes:category>` element.

### `test_pipeline.py` positional patch chain is fragile

**Issue:** Thirteen test functions patch 19 symbols with a single positional `with patch(_PATCHES[0]) as m_dl, ...` chain on one line suppressed with `# noqa: E501`. Reordering `_PATCHES` silently maps mock variables to the wrong symbols.
**Files:** `tests/test_pipeline.py:2193, 2215, 2240, 2263, 2285, 2311, 2333, 2354, 2377, 2507, 2525, 2546, 2974`
**Impact:** A future change to `_PATCHES` ordering would produce tests that pass but assert against the wrong mock. The bug would surface only when a previously mocked component starts being called incorrectly.
**Fix approach:** Use `unittest.mock.patch` as a decorator stack or a `MagicMock` spec-based fixture dictionary keyed by name rather than position.

### `extract_llm_reasoning` accesses response internals without a guard

**Issue:** `utils/llm.py:62` accesses `response.choices[0].message` with `# type: ignore[union-attr]` and no bounds check. If litellm returns a response with an empty `choices` list (possible on certain error-response shapes), this raises `IndexError`.
**Files:** `utils/llm.py:62`
**Impact:** A malformed API response causes an unhandled `IndexError` inside `_log_llm_reasoning`, which propagates out of the calling `detect`/`extract` method.
**Fix approach:** Guard with `if response.choices` before index access, or wrap in `try/except (IndexError, AttributeError)` and return `None`.

### Dependency lower bounds only — no upper bounds pinned in `pyproject.toml`

**Issue:** All runtime dependencies use `>=` lower bounds only (e.g., `litellm>=1.83.0`, `aiohttp>=3`). A breaking major-version release of any dependency will not be caught until CI runs against the new version.
**Files:** `pyproject.toml:5-14`
**Impact:** `uv.lock` pins exact versions for reproducible installs, so the risk is low in practice. However, running `uv lock --upgrade` could pull in a breaking release undetected.
**Fix approach:** Add `<` upper bounds for dependencies with a history of breaking changes between majors (especially `litellm`, which updates frequently).

---

*Concerns audit: 2026-05-14*
