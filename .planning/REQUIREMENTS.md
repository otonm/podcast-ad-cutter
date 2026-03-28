# Requirements: Podcast Ad Cutter

**Defined:** 2026-03-28
**Core Value:** Automatically produce ad-free podcast audio files with a valid RSS feed, minimising repeated processing by tracking work already done.

## v1 Requirements

### Bug Fixes

- [ ] **BUG-01**: TopicExtractor retries on malformed JSON — implementation raises immediately instead of appending retry prompt and calling LLM again (5 failing tests in test_topic_extractor.py)

### Ad Detection

- [ ] **AD-01**: Pipeline instantiates AdDetector with config from `models.ad_detection` (provider, model, api_key)
- [ ] **AD-02**: Pipeline instantiates AdParser (stateless, no config needed)
- [ ] **AD-03**: Pipeline instantiates AdStore and loads `ad_detected_guids` set before processing episodes
- [ ] **AD-04**: For each episode, after transcription and topic extraction: if episode GUID not in `ad_detected_guids`, call AdDetector.detect() with transcript segments and topic extraction
- [ ] **AD-05**: Pass AdDetector output through AdParser.parse() to merge consecutive segments into AdSegment objects
- [ ] **AD-06**: Save merged AdSegment objects to AdStore.save_segments(); call AdStore.mark_detected() to record the run
- [ ] **AD-07**: If episode GUID already in `ad_detected_guids`, load existing AdSegment objects from AdStore.get_segments_for_guid() and skip detection

### Audio Editing

- [ ] **EDIT-01**: Pipeline instantiates AudioEditor with output_dir, file_type, and bitrate from config
- [ ] **EDIT-02**: AudioEditor.edit() always produces an output file — when no qualifying ad segments exist, re-encode the input without cuts (do not return None)
- [ ] **EDIT-03**: Pipeline calls AudioEditor.edit() with: raw input path, ad segments, feed_slug, pub_date, episode title, min_duration_ms, min_confidence, total_duration_s from AudioMetadata
- [ ] **EDIT-04**: Output file written to `output/{feed_slug}/{DD.MM.YYYY}-{episode-title}.{ext}`
- [ ] **EDIT-05**: If output file already exists, skip audio editing entirely (idempotency — AudioEditor already implements this guard)
- [ ] **EDIT-06**: EpisodeCopier removed from Pipeline; AudioEditor is the sole output-file producer

### Pipeline Integration

- [ ] **PIPE-01**: Decision tree updated — episode processing checks: `transcription_exists`, `ad_detected` (from ad_detection_runs), `output_exists` (filesystem)
- [ ] **PIPE-02**: Branch logic: if output_exists → reconstruct URL only (Branch A); otherwise run missing stages in order: download → probe → preprocess → transcribe → extract topics → detect ads → edit audio
- [ ] **PIPE-03**: Ad detection cost saved to CostTrackingStore (AdDetectionCost → cost_store.save_cost)
- [ ] **PIPE-04**: Episode URL updated to point to locally produced output file after audio edit
- [ ] **PIPE-05**: RSS feed published with updated episode URLs pointing to edited audio files

### Testing

- [ ] **TEST-01**: Pipeline tests cover the full ad detection + audio editing path (end-to-end mocked)
- [ ] **TEST-02**: Pipeline tests cover the idempotency branches (ad already detected, output already exists)
- [ ] **TEST-03**: All existing tests remain green; 100% coverage maintained; ruff clean

## v2 Requirements

### Observability

- **OBS-01**: Per-episode cost summary logged at end of pipeline run (transcription + topic + ad detection)
- **OBS-02**: Summary of ads cut per episode (count, total duration removed)

### Configuration

- **CFG-01**: Per-feed override of ad detection model/provider
- **CFG-02**: Option to disable ad detection for specific feeds

## Out of Scope

| Feature | Reason |
|---------|--------|
| GUI / web interface | CLI pipeline only; complexity not justified |
| Multi-user / SaaS | Single operator use case |
| Video podcast support | Audio only; video adds significant complexity |
| Re-processing already-edited episodes | Idempotency guards prevent this by design |
| Real-time streaming pipeline | Batch processing is sufficient |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| BUG-01 | Phase 1 | Pending |
| AD-01 | Phase 2 | Pending |
| AD-02 | Phase 2 | Pending |
| AD-03 | Phase 2 | Pending |
| AD-04 | Phase 2 | Pending |
| AD-05 | Phase 2 | Pending |
| AD-06 | Phase 2 | Pending |
| AD-07 | Phase 2 | Pending |
| EDIT-01 | Phase 2 | Pending |
| EDIT-02 | Phase 2 | Pending |
| EDIT-03 | Phase 2 | Pending |
| EDIT-04 | Phase 2 | Pending |
| EDIT-05 | Phase 2 | Pending |
| EDIT-06 | Phase 2 | Pending |
| PIPE-01 | Phase 2 | Pending |
| PIPE-02 | Phase 2 | Pending |
| PIPE-03 | Phase 2 | Pending |
| PIPE-04 | Phase 2 | Pending |
| PIPE-05 | Phase 2 | Pending |
| TEST-01 | Phase 2 | Pending |
| TEST-02 | Phase 2 | Pending |
| TEST-03 | Phase 2 | Pending |

**Coverage:**
- v1 requirements: 22 total
- Mapped to phases: 22
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-28*
*Last updated: 2026-03-28 after initialization*
