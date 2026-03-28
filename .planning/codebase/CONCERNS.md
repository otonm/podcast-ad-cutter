# Concerns

**Analysis Date:** 2026-03-28

## Technical Debt

**Broad exception handling in pipeline:**
- Location: `components/pipeline.py` line 173
- Issue: `except Exception` silently swallows errors per episode — pipeline continues without surfacing root cause
- Risk: Silent data loss; failed episodes are logged but not retried or flagged distinctly

**Missing schema versioning:**
- Location: `database/connection.py`
- Issue: No migration system; schema changes require manual DB deletion or ad-hoc ALTER TABLE
- Risk: Breaking changes to schema during development require manual intervention; no upgrade path for production

**Cost tracking orphaned from episodes:**
- Location: `database/cost_tracking_store.py`
- Issue: Cost table has no foreign key relationship to episodes table
- Risk: Cost data cannot be attributed back to specific episodes; billing reconciliation is difficult

**No retry logic for topic extraction:**
- Location: `components/topic_extractor.py`
- Issue: TopicExtractor has no retry unlike EpisodeDownloader which has a mechanism
- Risk: Transient API failures cause permanent topic extraction skip with no recovery

**Transcript truncation heuristic:**
- Location: `components/topic_extractor.py`
- Issue: Full transcripts passed to LLM topic extractor; long episodes may exceed context windows
- Risk: Silent truncation or API errors for long-form episodes

## Known Bugs

**Feed parser suppresses XML security warnings:**
- Location: `components/feed_parser.py`
- Issue: defusedxml warnings suppressed; may mask legitimate security issues
- Risk: Low, but obscures security-relevant XML behaviour

**Partial downloads not cleaned up on cancellation:**
- Location: `components/episode_downloader.py`
- Issue: If async task is cancelled mid-download, partial files may be left in cache/
- Risk: Stale partial files may be treated as complete on next run

## Security

**API keys in memory as plaintext:**
- Location: `components/episode_transcriptor.py`, `components/topic_extractor.py`
- Issue: API keys stored as instance attributes (`self._api_key`)
- Risk: Memory dumps or debug logging could expose credentials; acceptable for current scope but worth noting

**SQL construction with f-strings:**
- Location: Various `database/` stores
- Issue: Some dynamic SQL uses f-strings for table/column names (not user-controlled)
- Risk: Low — not user input, but pattern is fragile if naming logic ever becomes dynamic

## Performance

**Single-threaded episode processing:**
- Location: `components/pipeline.py` Pipeline.run()
- Issue: Episodes processed sequentially in a loop; no concurrency within a feed
- Risk: Processing time scales linearly with episode count; no parallelism for download/transcription

**Full feed re-download on every run:**
- Location: `components/feed_downloader.py`
- Issue: No conditional HTTP (ETag/Last-Modified headers)
- Risk: Unnecessary bandwidth and latency on repeat runs

**Redundant state checks per episode:**
- Location: `components/pipeline.py` _process_episode()
- Issue: Filesystem existence checks re-run for every episode rather than being batched
- Risk: Degrades with large episode counts; filesystem stat calls are not free

## Fragile Areas

**Pipeline 4-branch decision tree:**
- Location: `components/pipeline.py` _process_episode()
- Issue: Branch logic is tightly coupled; adding a new processing step requires updating all 4 branches
- Risk: High likelihood of regression when adding features (e.g., ad detection)

**FeedPublisher manual XML construction:**
- Location: `components/feed_publisher.py`
- Issue: RSS XML built with string concatenation rather than an XML library
- Risk: Encoding issues or malformed XML for edge-case metadata (special characters, CDATA)

**TopicStore silent duplicate handling:**
- Location: `database/topic_store.py`
- Issue: INSERT OR IGNORE semantics silently skip duplicate topic insertions
- Risk: Re-runs may silently fail to update stale topics

## Scaling Limits

**SQLite file-level locking:**
- Location: `database/connection.py`
- Issue: SQLite blocks concurrent writes; not suitable for multi-process or multi-instance deployments
- Risk: Acceptable for single-user CLI tool; would need PostgreSQL for any concurrent use

**In-memory episode list grows linearly:**
- Location: `components/pipeline.py`
- Issue: All episodes for all feeds loaded into memory; no streaming or pagination
- Risk: Memory pressure with feeds containing hundreds of episodes

**Filesystem glob for output audio:**
- Location: `components/pipeline.py` _check_output_audio()
- Issue: Directory listing to find existing output files degrades with large output directories
- Risk: Noticeable slowdown with thousands of processed episodes

## Dependencies at Risk

**litellm no upper version bound:**
- Location: `pyproject.toml`
- Issue: `litellm>=1.0` with no upper bound; library has a history of breaking changes
- Risk: `uv lock --upgrade` could pull in a breaking litellm version silently

**aiohttp streaming API:**
- Location: `components/episode_downloader.py`
- Issue: Uses `resp.content.iter_chunked()` which is implementation-specific
- Risk: Low for current versions but could change in a major aiohttp version bump

## Missing Features (gaps that create operational risk)

- **No feed URL validation at startup** — bad config URL only discovered at runtime
- **No checkpoint/resumption** — interrupted runs restart from scratch; no partial progress saved
- **No cost budget enforcement** — no way to cap API spend; runaway costs possible
- **No episode filtering/exclusion rules** — cannot skip specific episodes by pattern or date

## Test Coverage Gaps (High Priority)

- Pipeline branches A-D not tested individually — `tests/test_pipeline.py` tests full flows but not each branch in isolation
- TopicExtractor JSON recovery path not tested — `tests/test_topic_extractor.py` missing malformed JSON response case
- FeedPublisher XML output not validated with an XML parser — only string assertions
- Cost computation edge cases (zero audio duration, missing cost fields) untested

---

*Concerns analysis: 2026-03-28*
