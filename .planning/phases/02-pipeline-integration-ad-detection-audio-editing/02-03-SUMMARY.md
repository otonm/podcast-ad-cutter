---
phase: 02-pipeline-integration-ad-detection-audio-editing
plan: "03"
subsystem: pipeline
tags: [tdd, decision-tree, ad-detection, audio-editor, pipeline]
dependency_graph:
  requires: [02-02]
  provides: [_process_episode-ad-detection-tail, branch-a-short-circuit, conditional-url-update]
  affects: [components/pipeline.py, tests/test_pipeline.py]
tech_stack:
  added: []
  patterns: [tdd-red-green-refactor, ad-detection-tail, conditional-url-update, cache-dir-branch-c]
key_files:
  created: []
  modified:
    - components/pipeline.py
    - tests/test_pipeline.py
decisions:
  - "Branch A triggers on output_exists alone (not transcription AND output) — simpler invariant: if output file exists, nothing to do"
  - "Branch C checks cache_dir/{guid}.* for cached raw audio, distinct from output_feed_dir — allows re-transcribing downloaded audio without re-downloading"
  - "URL update only in two controlled places: Branch A unconditional, ad tail conditional on output_path is not None"
  - "AudioPreprocessor NOT called in Branch B (D-05): transcription already exists, no need to re-preprocess for ad detection"
metrics:
  duration: "~19 minutes"
  completed_date: "2026-03-29"
  tasks_completed: 1
  files_modified: 2
---

# Phase 02 Plan 03: _process_episode Decision Tree Rewrite with Ad Detection Tail Summary

Rewrote `_process_episode` using TDD: new 4-branch decision tree with a shared ad detection tail wired into Branches B, C, and D. Branch A short-circuits when output file exists.

## What Was Built

The `_process_episode` method in `components/pipeline.py` was completely rewritten to integrate ad detection (AdDetector, AdParser, AdStore) and conditional URL updating. The new decision tree correctly separates output existence from transcription existence.

### New Branch Logic

| Branch | Trigger | Actions |
|--------|---------|---------|
| A | `output_exists` (file in output_feed_dir) | Reconstruct URL, update, **return** |
| B | `transcription_exists`, no output | Download, probe, load t_segments+topic from stores, **ad tail** |
| C | `cached_audio_exists` (file in cache_dir), no transcription | Probe cached, preprocess, transcribe, topic, **ad tail** |
| D | Nothing exists | Download, probe, preprocess, transcribe, topic, **ad tail** |

### Ad Detection Tail (Branches B, C, D)

```python
if episode.guid not in ad_detected_guids:
    # detect: AdDetector.detect() -> AdParser.parse() -> AdStore.save_segments() + mark_detected() + cost_store.save_cost(ad_cost)
else:
    # load: AdStore.get_segments_for_guid()

output_path = await self._audio_editor.edit(...)
if output_path is not None:
    # update URL — conditional
```

## Tests Added

6 new branch tests (RED written before implementation):

| Test | Coverage |
|------|----------|
| `test_branch_a_output_exists_short_circuits` | PIPE-02: no download/detect/edit when output exists |
| `test_branch_b_transcription_exists_no_output_with_ads` | AD-04/05/06, EDIT-03, PIPE-03/04 |
| `test_branch_b_transcription_exists_no_output_no_ads_keeps_original_url` | PIPE-04 negative: no URL update when edit returns None |
| `test_branch_d_full_pipeline_with_ad_detection` | AD-04/05/06, EDIT-03, TEST-01 |
| `test_branch_d_ad_already_detected_loads_from_store` | AD-07, TEST-02: skip detection when already detected |
| `test_branch_c_audio_exists_no_transcription_runs_ad_detection` | Full Branch C path with ad tail |

4 existing tests updated to match new behavior:
- `test_branch_b`: removed `preprocess.assert_awaited_once()` → `assert_not_called()` (D-05)
- `test_branch_c`: updated to use cache_dir/{guid}.mp3 instead of output_dir file
- `test_branch_d`: updated cost count 2→3 (transcription + topic + ad_detection)
- `_wire_branch_mocks`: added `get_segments_for_guid` and `get_topic_for_guid` as `AsyncMock`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Branch C needs cache_dir check, not output_dir**
- **Found during:** GREEN phase implementation
- **Issue:** The plan's new Branch A (`if audio_exists: ... return`) catches ALL cases where `output_feed_dir` has a file. This made old Branch C (`elif audio_exists:`) unreachable dead code. The spec note says "cache has audio" for Branch C — this means checking `cache_dir/{guid}.*`, not `output_feed_dir`.
- **Fix:** Added `cached_audio = next((p for p in cache_dir.glob(f"{episode.guid}.*")), None)` check using `self._config.app.paths.cache_dir`. Branch C now triggers on `cached_audio_exists` instead of `audio_exists`.
- **Files modified:** components/pipeline.py, tests/test_pipeline.py
- **Commit:** ab84abd

**2. [Rule 1 - Bug] Unused noqa directive on cache_dir glob**
- **Found during:** ruff check
- **Issue:** `# noqa: ASYNC240` on the `cache_dir.glob()` call was unused (MagicMock glob in tests doesn't trigger the async warning).
- **Fix:** Removed unused noqa directive.
- **Files modified:** components/pipeline.py
- **Commit:** ab84abd

**3. [Rule 1 - Bug] RET505: unnecessary elif after return**
- **Found during:** ruff check
- **Issue:** `elif transcription_exists:` after `return` in Branch A was flagged by RET505.
- **Fix:** Changed to `if transcription_exists:`.
- **Files modified:** components/pipeline.py
- **Commit:** ab84abd

## Known Stubs

None — all data flows are wired. The ad detection tail is fully connected to AdDetector, AdParser, AdStore, and AudioEditor.

## Verification

```
uv run pytest tests/test_pipeline.py -x --tb=short   -> 39 passed
uv run ruff check components/pipeline.py              -> All checks passed
uv run pytest --cov=. -q                              -> 577 passed, TOTAL 100%
```

Key structural checks:
- Branch A short-circuits with `return` on line 252
- `if output_path is not None:` present — conditional URL update only
- No unconditional `update_episode_url` at end of `_process_episode`
- `preprocess` NOT called in Branch B
- `cost_store.save_cost(ad_cost)` present in ad detection tail

## Self-Check: PASSED

- components/pipeline.py: FOUND
- tests/test_pipeline.py: FOUND
- Commit ab84abd: FOUND
