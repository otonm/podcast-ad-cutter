---
phase: 02-pipeline-integration-ad-detection-audio-editing
plan: 02
subsystem: pipeline
tags: [python, asyncio, pipeline, ad-detection, audio-editor, tdd]

# Dependency graph
requires:
  - phase: 02-01
    provides: AdDetector, AdParser, AudioEditor, AdStore models and components wired individually
provides:
  - Pipeline.__init__ instantiates AdDetector, AdParser, AudioEditor from config
  - Pipeline.run() loads ad_detected_guids via AdStore before episode loop
  - Pipeline._process_episode accepts ad_store and ad_detected_guids keyword-only params
  - EpisodeCopier removed from Pipeline entirely
affects:
  - 02-03 (decision tree extension depends on this wiring)
  - 02-04 (branch assertion tests depend on this wiring)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "noqa: ARG002 pragma for intentional stub params forwarded to next plan"
    - "noqa: PLR0915 pragma for large orchestrator methods that exceed statement limit"

key-files:
  created: []
  modified:
    - components/pipeline.py
    - tests/test_pipeline.py

key-decisions:
  - "Replace _episode_copier.copy() with _audio_editor.edit() calls in branches B and D; fallback to episode.url when edit() returns None (per D-01)"
  - "Mark ad_store and ad_detected_guids params with noqa: ARG002 as intentional stubs for plan 02-03 decision tree wiring"
  - "Add noqa: PLR0915 to _process_episode; function will be split or refactored in plan 02-03"

patterns-established:
  - "New pipeline components wired in __init__ following PROVIDER_KEY_MAP pattern for LLM-backed services"
  - "Stores loaded per-feed inside Database context manager before episode loop"

requirements-completed:
  - AD-01
  - AD-02
  - AD-03
  - EDIT-01
  - EDIT-06

# Metrics
duration: 9min
completed: 2026-03-29
---

# Phase 02 Plan 02: Pipeline Constructor Wiring Summary

**AdDetector, AdParser, AudioEditor, and AdStore wired into Pipeline using TDD; EpisodeCopier removed and replaced with AudioEditor as sole output producer**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-29T15:13:32Z
- **Completed:** 2026-03-29T15:22:40Z
- **Tasks:** 3 (RED, GREEN, REFACTOR)
- **Files modified:** 2

## Accomplishments

- Removed EpisodeCopier from Pipeline — AudioEditor is now the sole audio output component
- Pipeline.__init__ instantiates AdDetector (with provider/model/api_key), AdParser(), and AudioEditor (with output_dir/file_type/bitrate) from config
- Pipeline.run() creates AdStore(db.conn) and awaits get_detected_guids() before the episode loop
- Pipeline._process_episode signature extended with ad_store and ad_detected_guids keyword-only params (stubs for plan 02-03 decision tree work)
- All 33 pipeline tests pass, 100% coverage, ruff clean

## Task Commits

1. **RED: Failing tests for constructor wiring** - `6020896` (test)
2. **GREEN: Implement wiring + REFACTOR: update all existing branch tests** - `c1cc9a4` (feat)

## Files Created/Modified

- `/home/oton/projects/podcast-ad-cutter/components/pipeline.py` - Removed EpisodeCopier; added AdDetector/AdParser/AudioEditor/AdStore imports and instantiation; updated run() and _process_episode signature
- `/home/oton/projects/podcast-ad-cutter/tests/test_pipeline.py` - Added 5 new constructor/run wiring tests; updated all 8 branch/error tests to use new mock set; added 2 coverage tests for audio editor non-None path

## Decisions Made

- Replace `_episode_copier.copy()` with `_audio_editor.edit([])` in branches B and D, with fallback to `episode.url` when edit returns `None`. This is the minimum viable change for this plan — plan 02-03 will wire full ad detection into the branches.
- Mark `ad_store` and `ad_detected_guids` params as `noqa: ARG002` since they are intentionally unused until plan 02-03 wires the decision tree.
- Add `noqa: PLR0915` to `_process_episode` — the function will be simplified in plan 02-03 once ad detection logic replaces the placeholder calls.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added two coverage tests for AudioEditor returning non-None path**
- **Found during:** GREEN phase, after full coverage check
- **Issue:** New `if output_path is not None` branches in branches B and D were uncovered (mocks return None by default)
- **Fix:** Added `test_branch_b_audio_editor_returns_path_uses_computed_url` and `test_branch_d_audio_editor_returns_path_uses_computed_url` with mocks returning an actual Path
- **Files modified:** tests/test_pipeline.py
- **Verification:** Coverage 100%
- **Committed in:** c1cc9a4 (GREEN phase commit)

---

**Total deviations:** 1 auto-fixed (Rule 2 - missing coverage for new branches)
**Impact on plan:** Essential for 100% coverage requirement. No scope creep.

## Issues Encountered

- The non-branch pipeline tests (`test_run_passes_only_enabled_feeds`, progress callback tests, transcriptor test) all create inline `MagicMock()` configs that lacked the new `ad_detection` and `output` config attributes. Fixed by adding the missing attrs and patching the new component classes in each test's `with` block.

## Known Stubs

- `_process_episode` receives `ad_store` and `ad_detected_guids` but does not use them yet. Plan 02-03 will wire the ad detection decision tree using these values.
- `AudioEditor.edit()` is called with empty `ad_segments=[]` in branches B and D. Plan 02-03 will pass real segments from ad detection.

## Next Phase Readiness

- Plan 02-03 (decision tree extension) can now build on the wired `ad_store`, `ad_detected_guids`, `self._ad_detector`, `self._ad_parser`, and `self._audio_editor` inside `_process_episode`
- No blockers

---
*Phase: 02-pipeline-integration-ad-detection-audio-editing*
*Completed: 2026-03-29*
