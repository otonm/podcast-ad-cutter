# Podcast Ad Cutter

## What This Is

A self-hosted Python pipeline that downloads podcast RSS feeds, transcribes episodes via LLM, detects and cuts advertisement segments using context-aware LLM analysis, and publishes a clean RSS feed pointing to the edited audio files. Designed for a single operator running against their own podcast subscriptions.

## Core Value

Automatically produce ad-free podcast audio files with a valid RSS feed, minimising repeated processing by tracking work already done.

## Requirements

### Validated

- ✓ RSS/Atom feed download and parsing (aiohttp + custom FeedParser) — existing
- ✓ Episode metadata stored in SQLite (EpisodeStore) — existing
- ✓ Episode audio download with progress callback (EpisodeDownloader) — existing
- ✓ Audio probing via ffprobe (AudioProber, AudioMetadataStore) — existing
- ✓ Audio preprocessing to mono 16-bit PCM via ffmpeg (AudioPreprocessor) — existing
- ✓ Transcription via litellm with multiple provider support and cost tracking (EpisodeTranscriptor) — existing
- ✓ Topic/context extraction via LLM with cost tracking (TopicExtractor) — existing
- ✓ Database schema includes ad_segments and ad_detection_runs tables — existing
- ✓ Config has AdDetectionConfig (min_duration, min_confidence) and LLMConfig for ad detection — existing
- ✓ AdDetector: LLM-based ad segment detection using transcript + topic context — existing (untracked)
- ✓ AdParser: merges consecutive detected segments into time-bounded AdSegment objects — existing (untracked)
- ✓ AudioEditor: cuts qualifying ad segments via ffmpeg atrim+concat, encodes to output format — existing (untracked)
- ✓ AdStore: persists ad_segments and ad_detection_runs per episode — existing (untracked)

### Active

- [ ] Fix TopicExtractor retry loop — implementation does not retry on malformed JSON (5 failing tests)
- [ ] Wire AdDetector, AdParser, AdStore, AudioEditor into Pipeline.__init__
- [ ] Expand Pipeline._process_episode decision tree to include ad detection → audio editing stages
- [ ] AudioEditor always produces an output file (even when no qualifying ads — re-encode without cuts)
- [ ] Pipeline idempotency: skip ad detection if ad_detection_runs record exists; skip audio edit if output file exists
- [ ] Replace EpisodeCopier with AudioEditor as the sole output-file producer
- [ ] Output pattern: `output/{feed_slug}/{DD.MM.YYYY}-{episode-title}.{ext}` (already implemented in AudioEditor)
- [ ] RSS feed updated with local output file URL after audio edit
- [ ] Tests covering Pipeline branches with ad detection and audio editing
- [ ] All tests passing, 100% coverage, ruff clean

### Out of Scope

- GUI or web interface — CLI pipeline only
- Multi-user or SaaS deployment — single operator use
- Batch re-processing of already-edited episodes — idempotency guards prevent this
- Non-audio podcast content (video, PDF) — audio only

## Context

The project has a well-established async Python architecture. All major components exist. The remaining work is almost entirely integration:

- **TopicExtractor retry bug**: `topic_extractor.py` raises immediately on the first parse failure instead of retrying. Tests expect retry behaviour (retry prompt appended to messages, up to `max_retries`). AdDetector has the correct retry pattern; TopicExtractor should mirror it.
- **Pipeline wiring gap**: AdDetector, AdParser, AdStore, AudioEditor are implemented and tested in isolation but not yet instantiated or called in Pipeline.
- **EpisodeCopier replacement**: EpisodeCopier converted audio to output format as a "copy" step. AudioEditor does the same (plus ad cutting), so EpisodeCopier should be removed from the flow once AudioEditor is wired.
- **No-qualifying-ads behaviour**: When `AudioEditor.edit()` finds no qualifying segments, it currently returns `None`. Per the project's design, a final output file must always be produced (re-encode without cuts). AudioEditor should be updated to always produce output.
- **Decision tree extension**: The existing 4-branch decision tree (A/B/C/D) needs ad-state tracking added as a second dimension: `ad_detected` (from AdStore.get_detected_guids) drives whether ad detection runs; `output_exists` (filesystem check) drives whether audio editing runs.

## Constraints

- **Tech stack**: Python 3.12, async throughout, litellm for all LLM calls, aiosqlite for all DB, ffmpeg binary in PATH
- **Test discipline**: Write failing test first, then implement. 100% coverage required. All ruff errors must be resolved.
- **Logging**: f-strings only, never % operator
- **Config ownership**: Only Pipeline reads Config; all components receive plain extracted values

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| AudioEditor always produces output (even with no qualifying ads) | User expects a final file and RSS entry for every episode; None return would leave original feed URL unchanged | — Pending |
| EpisodeCopier removed, AudioEditor is sole output producer | AudioEditor already handles format encoding; keeping both creates duplicate logic | — Pending |
| Ad detection skipped if ad_detection_runs record exists | Expensive LLM call; transcript doesn't change between runs | — Pending |
| Audio edit skipped if output file already exists | AudioEditor already implements this guard internally | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-28 after initialization*
