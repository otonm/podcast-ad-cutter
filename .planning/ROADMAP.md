# Roadmap: Podcast Ad Cutter

## Overview

The pipeline infrastructure is complete. Feed download, transcription, topic extraction, and all ad-detection and audio-editing components exist and are tested in isolation. Two phases remain: fix a retry bug in TopicExtractor, then wire AdDetector, AdParser, AdStore, and AudioEditor into the Pipeline and update its decision tree. When Phase 2 completes, the pipeline produces ad-free audio files and a clean RSS feed for every episode.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: TopicExtractor Retry Bug Fix** - Fix retry loop so TopicExtractor mirrors AdDetector's working retry pattern
- [ ] **Phase 2: Pipeline Integration — Ad Detection & Audio Editing** - Wire AdDetector, AdParser, AdStore, and AudioEditor into Pipeline; update decision tree; remove EpisodeCopier

## Phase Details

### Phase 1: TopicExtractor Retry Bug Fix
**Goal**: TopicExtractor retries on malformed JSON responses, matching the retry behaviour already working in AdDetector
**Depends on**: Nothing (first phase)
**Requirements**: BUG-01, TEST-03
**Success Criteria** (what must be TRUE):
  1. TopicExtractor appends the retry prompt to messages and calls the LLM again when JSON parsing fails, up to max_retries
  2. TopicExtractor raises an exception only after all retries are exhausted, not on the first parse failure
  3. All 5 previously failing tests in test_topic_extractor.py pass
  4. 100% test coverage maintained; ruff reports no errors
**Plans**: 2 plans

Plans:
- [ ] 01-01: Fix TopicExtractor retry loop to mirror AdDetector pattern (failing test first, then implementation)
- [ ] 01-02: Verify full test suite green, coverage 100%, ruff clean

### Phase 2: Pipeline Integration — Ad Detection & Audio Editing
**Goal**: The Pipeline produces a final output audio file and RSS entry for every episode, with ad segments detected, cut, and costs tracked
**Depends on**: Phase 1
**Requirements**: AD-01, AD-02, AD-03, AD-04, AD-05, AD-06, AD-07, EDIT-01, EDIT-02, EDIT-03, EDIT-04, EDIT-05, EDIT-06, PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05, TEST-01, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. Pipeline instantiates and uses AdDetector, AdParser, AdStore, and AudioEditor; EpisodeCopier is removed from the pipeline flow
  2. AudioEditor always produces an output file — when no qualifying ad segments exist, the episode is re-encoded without cuts rather than returning None
  3. Ad detection is skipped (existing segments loaded from AdStore) when an ad_detection_runs record already exists for the episode
  4. Audio editing is skipped when the output file already exists on disk
  5. Ad detection cost is saved to CostTrackingStore after each detection run
  6. Pipeline tests cover the full ad detection + audio editing path and all idempotency branches, with 100% coverage and ruff clean
**Plans**: 4 plans

Plans:
- [ ] 02-01: Update AudioEditor to always produce output (re-encode without cuts when no qualifying segments)
- [ ] 02-02: Wire AdDetector, AdParser, AdStore into Pipeline.__init__ and _process_episode; load ad_detected_guids before episode loop
- [ ] 02-03: Update Pipeline decision tree — add ad_detected and output_exists dimensions; integrate AudioEditor, remove EpisodeCopier; save ad detection cost to CostTrackingStore; update episode URL after edit
- [ ] 02-04: Write Pipeline integration tests for ad detection + audio editing path and idempotency branches; verify 100% coverage and ruff clean

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. TopicExtractor Retry Bug Fix | 0/2 | Not started | - |
| 2. Pipeline Integration — Ad Detection & Audio Editing | 0/4 | Not started | - |
