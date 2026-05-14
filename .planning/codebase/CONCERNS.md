# Concerns & Tech Debt

## High

### Unguarded `None` from `get_transcription_text()` passed to TopicExtractor
**File:** `components/pipeline.py:540-546`  
`TranscriptionStore.get_transcription_text()` returns `str | None`. The value is passed directly to `TopicExtractor.extract(transcript: str)` with no null check. A DB inconsistency (transcript marked as done but text missing) would cause an `AttributeError` crash for that episode, silently swallowed by the outer `except Exception`.

### `assert meta is not None` inside async pipeline body
**File:** `components/pipeline.py:468`  
Bare `assert` used as a runtime guard. This is stripped by `python -O`, making the invariant invisible to optimised runs and hiding a real programmer error that should be a raised exception.

---

## Medium

### Duplicated LLM retry state machine
**Files:** `components/ad_detector.py`, `components/topic_extractor.py`  
Both components implement their own retry loop for LLM calls (context-window fallback, JSON schema fallback). The logic is near-identical but not shared. Any bug fix or enhancement must be applied in two places.

### Ad-hoc schema migration via `contextlib.suppress` — no version tracking
**File:** `database/connection.py` (approximately lines 160–167)  
DDL migrations are applied speculatively with suppressed errors rather than tracked via a schema version table. Silent failures on repeated migration attempts make it impossible to detect when a migration was skipped due to an actual error.

### Non-atomic RSS file writes in `FeedPublisher.update_episode_url`
**File:** `components/feed_publisher.py`  
The published RSS file is read, mutated in memory, and written back in place. A crash during the write leaves a corrupt or zero-byte RSS file with no rollback path (write-to-temp + rename would be atomic).

### Global GUID sets load all-feeds data with no per-feed filter
**File:** `components/pipeline.py` (`_Stores` initialisation)  
`get_transcribed_guids()`, `get_extracted_guids()`, and `get_detected_guids()` return all GUIDs across all podcasts. On large databases with many feeds, this causes unbounded memory growth and unnecessary DB reads per pipeline run.

### LLM API 429 rate-limit errors not retried — episode permanently skipped
**Files:** `components/ad_detector.py`, `components/topic_extractor.py`  
A transient 429 from the LLM provider raises an exception that propagates to `Pipeline._process_episode_until_final`, which catches and skips the episode. The episode's stage is not persisted, so the next run retries the API call — but within a single run there is no backoff or retry for 429s.

### `file_type` and `bitrate` config fields passed to ffmpeg without validation
**File:** `components/audio_editor.py` (via `config.app.output.file_type`, `config.app.output.bitrate`)  
Both are typed as `str` with no allowlist validation in the Pydantic model. An invalid value silently propagates into the ffmpeg argument list, producing a runtime error rather than a config-load-time error.

---

## Low

### `podcast_guid` derived from slugified feed title — title changes break directory matching
**File:** `components/pipeline.py:292`  
```python
podcast_guid=str(uuid.uuid5(uuid.NAMESPACE_DNS, slugify(feed.title))),
```
The Podcast 2.0 `<podcast:guid>` is computed from the title slug. Renaming a feed in config silently generates a different GUID, breaking Pocket Casts and other directories that track feeds by this identifier.

### iTunes category hierarchy flattened on round-trip
**File:** `components/feed_publisher.py`  
When a feed has multiple top-level `<itunes:category>` elements, the publisher collapses them into a single parent/child structure. Feeds with two separate top-level categories (e.g., `Technology` and `Arts`) lose the second category on republish.

### `_process_episode_until_final` exceeds complexity thresholds
**File:** `components/pipeline.py:370` (`# noqa: C901, PLR0912, PLR0915`)  
The episode state machine is ~290 lines with 5 guards, nested try/finally, and multiple DB interactions. The `noqa` suppresses the complexity warnings. Refactoring into a state enum + handler dispatch would improve testability but is currently not a blocker given full test coverage.

### `test_pipeline.py` patch line length
**File:** `tests/test_pipeline.py` (multiple lines ~2193+)  
Several test functions patch 19 symbols in a single `with patch(...)` chain, producing lines >500 characters suppressed with `# noqa: E501`. These are correct but fragile — reordering the `_PATCHES` list breaks the positional variable assignments silently.
