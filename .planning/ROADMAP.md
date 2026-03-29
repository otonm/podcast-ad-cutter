# Roadmap: Podcast Ad Cutter

## Overview

The pipeline infrastructure is complete. Feed download, transcription, topic extraction, and all ad-detection and audio-editing components exist and are tested in isolation. Two phases remain: fix a retry bug in TopicExtractor, then wire AdDetector, AdParser, AdStore, and AudioEditor into the Pipeline and update its decision tree. When Phase 2 completes, the pipeline produces ad-free audio files and a clean RSS feed for every episode.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: TopicExtractor Retry Bug Fix** - Fix retry loop so TopicExtractor mirrors AdDetector's working retry pattern (completed 2026-03-29)
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
- [x] 01-01-PLAN.md — Fix TopicExtractor retry loop to mirror AdDetector pattern (failing test first, then implementation)
- [x] 01-02-PLAN.md — Verify full test suite green, coverage 100%, ruff clean

### Phase 2: Pipeline Integration — Ad Detection & Audio Editing
**Goal**: The Pipeline produces a final output audio file and RSS entry for every episode that has qualifying ad segments, with ad segments detected, cut, and costs tracked
**Depends on**: Phase 1
**Requirements**: AD-01, AD-02, AD-03, AD-04, AD-05, AD-06, AD-07, EDIT-01, EDIT-02, EDIT-03, EDIT-04, EDIT-05, EDIT-06, PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05, TEST-01, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. Pipeline instantiates and uses AdDetector, AdParser, AdStore, and AudioEditor; EpisodeCopier is removed from the pipeline flow
  2. AudioEditor keeps its current return-None behavior — when no qualifying ad segments exist, the pipeline preserves the original episode URL unchanged and produces no local output file for that episode
  3. Ad detection is skipped (existing segments loaded from AdStore) when an ad_detection_runs record already exists for the episode
  4. Audio editing is skipped when the output file already exists on disk (Branch A short-circuit)
  5. Ad detection cost is saved to CostTrackingStore after each detection run
  6. Pipeline tests cover the full ad detection + audio editing path and all idempotency branches, with 100% coverage and ruff clean
**Plans**: 4 plans

Plans:
- [x] 02-01-PLAN.md — Update REQUIREMENTS.md and ROADMAP.md to reflect D-01 (AudioEditor keeps return-None behavior)
- [x] 02-02-PLAN.md — Wire AdDetector, AdParser, AdStore, AudioEditor into Pipeline.__init__ and run(); remove EpisodeCopier (TDD)
- [x] 02-03-PLAN.md — Rewrite _process_episode decision tree with ad detection tail and conditional URL update (TDD)
- [ ] 02-04-PLAN.md — Quality gate: full suite 100% coverage, ruff clean

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. TopicExtractor Retry Bug Fix | 2/2 | Complete   | 2026-03-29 |
| 2. Pipeline Integration — Ad Detection & Audio Editing | 3/4 | In Progress|  |
