# Phase 2: Pipeline Integration — Ad Detection & Audio Editing - Context

**Gathered:** 2026-03-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Wire AdDetector, AdParser, AdStore, and AudioEditor into Pipeline. Update the decision tree to add `ad_detected` and `output_exists` dimensions. Remove EpisodeCopier. Add comprehensive tests covering all ad detection and audio editing branches with 100% coverage.

</domain>

<decisions>
## Implementation Decisions

### AudioEditor: no-qualifying-ads behavior

- **D-01:** AudioEditor keeps its current `return None` behavior when no qualifying ad segments exist. The pipeline preserves the original episode URL unchanged — no local output file is produced for clean episodes.
- **D-02:** The all-audio-is-ads guard also keeps its current behavior (log warning, return None). Consistent with D-01.
- **D-03:** ⚠️ This OVERRIDES the committed requirement EDIT-02 ("always produces an output file") and the PROJECT.md Key Decision ("AudioEditor always produces output"). REQUIREMENTS.md EDIT-02, Plan 02-01, and SUCCESS CRITERION 2 in the roadmap must all be updated to reflect D-01.

### Branch logic: transcription exists, no output (old Branch B)

- **D-04:** When transcription exists but no output file: `download → probe → check ad_detected → load segments from AdStore OR run AdDetector → AudioEditor → update feed URL if output produced`.
- **D-05:** AudioPreprocessor (mono 16-bit PCM) is NOT run in this branch. Preprocessing is only needed before transcription. AudioEditor works on the raw audio directly.
- **D-06:** If AudioEditor returns None in this branch (no qualifying ads), keep the original episode URL — no URL update.

### Decision tree shape (all branches)

- **D-07:** Branch A (short-circuit): `output_exists → reconstruct URL only`. No download, no processing.
- **D-08:** All other branches run missing stages in order, gated by state:
  - `transcription_exists` → skip download+probe+preprocess+transcribe+extract_topics
  - `ad_detected` (from AdStore.get_detected_guids) → skip AdDetector, load segments from AdStore.get_segments_for_guid()
  - AudioEditor called with segments → if returns Path, update URL; if returns None, keep original URL
- **D-09:** `ad_detected_guids` set is loaded once before the episode loop (same pattern as `transcribed_guids`).

### Pipeline wiring

- **D-10:** EpisodeCopier is removed from Pipeline entirely — no longer imported or instantiated.
- **D-11:** Ad detection cost (AdDetectionCost) is saved to CostTrackingStore after each detection run (same pattern as transcription and topic costs).
- **D-12:** AdDetector is instantiated with ad detection config: `provider`, `model`, `api_key` from `config.app.models.ad_detection` (using PROVIDER_KEY_MAP for api_key).
- **D-13:** AdParser is stateless — instantiated with no arguments.
- **D-14:** AdStore receives the open db connection (same pattern as TranscriptionStore, TopicStore).
- **D-15:** AudioEditor receives `output_dir`, `file_type`, `bitrate` from config.

### Claude's Discretion

- Test file structure (extend test_pipeline.py or new file) — follow existing project test conventions
- Mock strategy for Pipeline tests — follow existing patterns in test_pipeline.py
- Exact logging messages for new branches

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Core files to modify
- `components/pipeline.py` — Pipeline orchestrator; add AdDetector, AdParser, AdStore, AudioEditor; remove EpisodeCopier; update decision tree
- `components/audio_editor.py` — AudioEditor; keep current return-None behavior (no changes needed per D-01)
- `tests/test_pipeline.py` — Existing pipeline tests; extend with ad detection and audio editing branches

### Components being wired in (read-only reference)
- `components/ad_detector.py` — AdDetector.detect(guid, segments, topic_extraction) → AdDetectionResult
- `components/ad_parser.py` — AdParser.parse(guid, detections, transcription_segments) → list[AdSegment]
- `database/ad_store.py` — AdStore: get_detected_guids(), get_segments_for_guid(), save_segments(), mark_detected()
- `models/ad_detection.py` — AdSegment, AdDetectionCost, AdSegmentDetection dataclasses

### Config reference
- `config/config_loader.py` — PROVIDER_KEY_MAP, Config, AdDetectionConfig structure

### Requirements (updated)
- `.planning/REQUIREMENTS.md` — Note: EDIT-02 must be updated to reflect D-01 (no-qualifying-ads → keep original URL)
- `.planning/ROADMAP.md` — Note: Phase 2 success criterion 2 must be updated to reflect D-01

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `AdStore(db.conn)` — same constructor pattern as TranscriptionStore, TopicStore
- `PROVIDER_KEY_MAP` — already used for transcription and topic extraction config; use same pattern for AdDetector
- `transcribed_guids = await transcription_store.get_transcribed_guids()` — model for `ad_detected_guids = await ad_store.get_detected_guids()`
- `await cost_store.save_cost(cost)` — already called for transcription and topic costs; call same method with AdDetectionCost

### Established Patterns
- All stores receive `db.conn` (not `db` itself) — created inside `async with Database(self._db_path) as db:` block
- `set[str]` loaded before episode loop for fast lookup: `transcribed_guids`, `extracted_guids` → add `ad_detected_guids`
- Components instantiated in `__init__` with plain extracted config values — no component imports from config module
- Branch docstring must be updated to reflect new branch logic

### Integration Points
- `_process_episode` method — primary change target; all new ad detection + editing logic goes here
- `__init__` — add AdDetector, AdParser, AdStore (as instance vars), AudioEditor; remove EpisodeCopier
- `run()` → inside the `async with Database(...)` block, instantiate AdStore and load `ad_detected_guids` before episode loop
- `store.update_episode_url()` + `_feed_publisher.update_episode_url()` — only called when AudioEditor returns a Path (not None)

</code_context>

<specifics>
## Specific Ideas

- AudioPreprocessor is ONLY for transcription — do not call it in branches where transcription already exists
- Branch B revised flow: `download → probe → check ad_detected → load or run AdDetector → AudioEditor → update feed if output produced`
- The "no qualifying ads → None" behavior in AudioEditor is CORRECT — do NOT change it
- Plan 02-01 ("Update AudioEditor to always produce output") should be dropped or reworked to reflect that no changes to AudioEditor are needed

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 02-pipeline-integration-ad-detection-audio-editing*
*Context gathered: 2026-03-29*
