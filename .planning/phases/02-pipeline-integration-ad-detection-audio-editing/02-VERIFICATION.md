---
phase: 02-pipeline-integration-ad-detection-audio-editing
verified: 2026-03-29T00:00:00Z
status: passed
score: 22/22 must-haves verified
---

# Phase 2: Pipeline Integration — Ad Detection & Audio Editing Verification Report

**Phase Goal:** Fully integrate ad detection, ad parsing, ad storage, and audio editing into the Pipeline component. Replace EpisodeCopier with the new ad-detection-aware processing flow. Implement the complete four-branch decision tree in `_process_episode` with the ad detection tail. Achieve 100% test coverage and ruff-clean code.
**Verified:** 2026-03-29
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| #  | Truth | Status | Evidence |
|----|-------|--------|----------|
| 1  | Pipeline instantiates AdDetector with provider/model/api_key from config.app.models.ad_detection | VERIFIED | `pipeline.py:84-88` — `ad_cfg = config.app.models.ad_detection; self._ad_detector = AdDetector(provider=..., model=..., api_key=...)` |
| 2  | Pipeline instantiates AdParser() with no arguments | VERIFIED | `pipeline.py:89` — `self._ad_parser = AdParser()` |
| 3  | Pipeline instantiates AudioEditor with output_dir/file_type/bitrate from config | VERIFIED | `pipeline.py:90-94` — `self._audio_editor = AudioEditor(output_dir=..., file_type=..., bitrate=...)` |
| 4  | EpisodeCopier removed from Pipeline entirely | VERIFIED | `grep -n "EpisodeCopier" pipeline.py` returns no results; `hasattr(pipeline_module, "EpisodeCopier")` assertion in test at line 1267 |
| 5  | Pipeline.run() instantiates AdStore(db.conn) and loads ad_detected_guids before episode loop | VERIFIED | `pipeline.py:169-172` — `ad_store = AdStore(db.conn)` then `ad_detected_guids = await ad_store.get_detected_guids()` |
| 6  | _process_episode signature includes ad_store: AdStore and ad_detected_guids: set[str] | VERIFIED | `pipeline.py:210-211` — both keyword-only parameters present |
| 7  | Branch A: output_exists alone triggers short-circuit — reconstruct URL and return | VERIFIED | `pipeline.py:244-252` — `if output_exists:` then URL update and `return` |
| 8  | Branch B: transcription exists, no output — download, probe, skip preprocess, run ad tail | VERIFIED | `pipeline.py:253-261`; `m_prep.return_value.preprocess.assert_not_called()` at test line 1492 |
| 9  | Ad detection tail: if GUID not in ad_detected_guids — detect, parse, save_segments, mark_detected, save_cost | VERIFIED | `pipeline.py:312-320` — full tail implemented |
| 10 | Ad detection tail: if GUID in ad_detected_guids — load from AdStore, skip detection | VERIFIED | `pipeline.py:321-322` — `else: segments = await ad_store.get_segments_for_guid(episode.guid)` |
| 11 | AudioEditor.edit() called with correct arguments including total_duration_s=meta.duration | VERIFIED | `pipeline.py:324-334` — all positional and keyword args passed including `total_duration_s=meta.duration` |
| 12 | URL updated only when AudioEditor returns a Path (not None) | VERIFIED | `pipeline.py:336-342` — `if output_path is not None:` guard around both update calls |
| 13 | When AudioEditor returns None, store.update_episode_url and feed_publisher.update_episode_url NOT called | VERIFIED | Test `test_branch_b_transcription_exists_no_output_no_ads_keeps_original_url` at line 1504 and `test_branch_c_audio_exists_no_transcription_runs_ad_detection` at line 1640 both assert `assert_not_called()` |
| 14 | Ad detection cost saved to CostTrackingStore | VERIFIED | `pipeline.py:319` — `await cost_store.save_cost(ad_cost)`; `m_cs.return_value.save_cost.assert_awaited()` at test line 1499 |
| 15 | REQUIREMENTS.md EDIT-02 reflects D-01 return-None behavior | VERIFIED | `REQUIREMENTS.md:25` — contains "return None" and "original episode URL unchanged" |
| 16 | ROADMAP.md Phase 2 success criterion 2 reflects return-None behavior | VERIFIED | `ROADMAP.md:41` — "AudioEditor keeps its current return-None behavior... pipeline preserves the original episode URL unchanged" |
| 17 | PROJECT.md contains no stale always-produces-output statements | VERIFIED | `grep` returns no matches for "always produces an output file", "always produces output", "re-encode without cuts" |
| 18 | All tests pass — 577 tests | VERIFIED | `uv run pytest --cov=. -q` reports `577 passed in 4.95s` |
| 19 | 100% coverage maintained | VERIFIED | Coverage report shows `TOTAL 5968 0 100%`; `components/pipeline.py` covered at 100% |
| 20 | Ruff reports no errors | VERIFIED | `uv run ruff check .` exits 0 with output `All checks passed!` |
| 21 | All six decision-tree branch tests exist and cover ad detection paths | VERIFIED | All 6 tests found: `test_branch_a_output_exists_short_circuits`, `test_branch_b_transcription_exists_no_output_with_ads`, `test_branch_b_transcription_exists_no_output_no_ads_keeps_original_url`, `test_branch_d_full_pipeline_with_ad_detection`, `test_branch_d_ad_already_detected_loads_from_store`, `test_branch_c_audio_exists_no_transcription_runs_ad_detection` |
| 22 | test_pipeline.py contains 39 test functions (significantly higher than pre-phase) | VERIFIED | `grep -c "def test_"` returns 39 |

**Score:** 22/22 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `components/pipeline.py` | Updated Pipeline class with ad detection wiring | VERIFIED | 457 lines; imports AdDetector, AdParser, AudioEditor, AdStore; full decision tree implemented; no EpisodeCopier |
| `tests/test_pipeline.py` | Constructor and run() wiring tests + branch tests | VERIFIED | 703 lines; 39 test functions; all ad detection paths covered |
| `.planning/REQUIREMENTS.md` | EDIT-02 updated to reflect D-01 | VERIFIED | Line 25 contains correct return-None text |
| `.planning/ROADMAP.md` | Phase 2 success criterion 2 updated | VERIFIED | Line 41 reflects return-None behavior |
| `.planning/PROJECT.md` | No stale always-produces-output statements | VERIFIED | grep returns no matches |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `Pipeline.__init__` | `AdDetector` | `config.app.models.ad_detection.provider/model + PROVIDER_KEY_MAP` | WIRED | `pipeline.py:84-88` |
| `Pipeline.__init__` | `AdParser` | no-arg constructor | WIRED | `pipeline.py:89` |
| `Pipeline.__init__` | `AudioEditor` | `output_dir/file_type/bitrate` | WIRED | `pipeline.py:90-94` |
| `Pipeline.run()` | `AdStore` | `AdStore(db.conn)` inside `async with Database` | WIRED | `pipeline.py:169` |
| `Pipeline.run()` | `_process_episode` | `ad_store=ad_store, ad_detected_guids=ad_detected_guids` kwargs | WIRED | `pipeline.py:188-189` |
| `pipeline._process_episode` | `_audio_editor.edit()` | called after ad detection in all non-A branches | WIRED | `pipeline.py:324-334` |
| `_audio_editor.edit()` return | `store.update_episode_url` | `if output_path is not None:` conditional | WIRED | `pipeline.py:336-342` |
| `_ad_detector.detect()` | `cost_store.save_cost` | `ad_cost` returned from detect, then saved | WIRED | `pipeline.py:313,319` |
| `REQUIREMENTS.md EDIT-02` | `CONTEXT.md D-01` | Requirement text updated to match locked decision | WIRED | Contains "return None" per D-01 |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| AD-01 | 02-02 | Pipeline instantiates AdDetector with ad_detection config | SATISFIED | `pipeline.py:84-88`; `test_pipeline_constructs_ad_detector` at line 1183 |
| AD-02 | 02-02 | Pipeline instantiates AdParser stateless | SATISFIED | `pipeline.py:89`; `test_pipeline_constructs_ad_parser` at line 1206 |
| AD-03 | 02-02 | Pipeline instantiates AdStore and loads ad_detected_guids before episode loop | SATISFIED | `pipeline.py:169-172`; `test_run_loads_ad_detected_guids_before_episode_loop` at line 1272 |
| AD-04 | 02-03 | After transcription, if not in ad_detected_guids: call AdDetector.detect() | SATISFIED | `pipeline.py:312-315`; multiple branch tests assert detect called |
| AD-05 | 02-03 | Pass detect output through AdParser.parse() | SATISFIED | `pipeline.py:316`; `m_ad_parser.return_value.parse.assert_called_once()` |
| AD-06 | 02-03 | Save segments to AdStore.save_segments(); call AdStore.mark_detected() | SATISFIED | `pipeline.py:317-318`; `m_ad_store.return_value.save_segments.assert_awaited_once()` |
| AD-07 | 02-03 | If already detected: load from AdStore.get_segments_for_guid(), skip detection | SATISFIED | `pipeline.py:321-322`; `test_branch_d_ad_already_detected_loads_from_store` at line 1593 |
| EDIT-01 | 02-02 | Pipeline instantiates AudioEditor with output_dir/file_type/bitrate | SATISFIED | `pipeline.py:90-94`; `test_pipeline_constructs_audio_editor` at line 1225 |
| EDIT-02 | 02-01 | AudioEditor keeps return-None behavior; pipeline preserves original URL | SATISFIED | `REQUIREMENTS.md:25`; tests assert no URL update when edit returns None |
| EDIT-03 | 02-03 | Pipeline calls AudioEditor.edit() with all required args | SATISFIED | `pipeline.py:324-334` — all positional + keyword args including `total_duration_s=meta.duration` |
| EDIT-04 | 02-03 | Output file written to `output/{feed_slug}/{DD.MM.YYYY}-{episode-title}.{ext}` | SATISFIED | AudioEditor handles path generation (its own responsibility, unchanged by this phase) |
| EDIT-05 | 02-03 | If output file already exists, skip audio editing (Branch A short-circuit) | SATISFIED | `pipeline.py:244-252` — `if output_exists: ... return`; `test_branch_a_output_exists_short_circuits` at line 1397 |
| EDIT-06 | 02-02 | EpisodeCopier removed from Pipeline | SATISFIED | No EpisodeCopier in pipeline.py; `test_pipeline_does_not_instantiate_episode_copier` at line 1248 |
| PIPE-01 | 02-03 | Decision tree checks transcription_exists, ad_detected, output_exists | SATISFIED | `pipeline.py:240-244` — all three boolean checks |
| PIPE-02 | 02-03 | Branch A: output_exists → reconstruct URL only | SATISFIED | `pipeline.py:244-252`; `test_branch_a_output_exists_short_circuits` verifies short-circuit |
| PIPE-03 | 02-03 | Ad detection cost saved to CostTrackingStore | SATISFIED | `pipeline.py:319`; `m_cs.return_value.save_cost.assert_awaited()` in branch B test |
| PIPE-04 | 02-03 | Episode URL updated to point to output file after audio edit | SATISFIED | `pipeline.py:336-342` — conditional URL update; branch tests assert update called when edit returns Path |
| PIPE-05 | 02-01/02-03 | RSS feed published with updated episode URLs | SATISFIED | `pipeline.py:250-251, 342` — `self._feed_publisher.update_episode_url(...)` called in both URL-update paths |
| TEST-01 | 02-04 | Pipeline tests cover full ad detection + audio editing path (end-to-end mocked) | SATISFIED | `test_branch_d_full_pipeline_with_ad_detection` at line 1545 covers download→probe→preprocess→transcribe→topic→detect→parse→save→edit→update |
| TEST-02 | 02-04 | Pipeline tests cover idempotency branches | SATISFIED | `test_branch_a_output_exists_short_circuits` + `test_branch_d_ad_already_detected_loads_from_store` |
| TEST-03 | 02-04 | All tests green; 100% coverage; ruff clean | SATISFIED | 577 passed; TOTAL 100%; `All checks passed!` |

**All 22 requirements SATISFIED.**

No orphaned requirements found — every requirement ID declared across plans 02-01 through 02-04 is accounted for.

---

## Anti-Patterns Found

No anti-patterns found in `components/pipeline.py` or `tests/test_pipeline.py`.

- No TODO/FIXME/PLACEHOLDER comments
- No empty handlers or stub returns
- No hardcoded empty data passed to rendering
- No EpisodeCopier references in production code

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All tests pass | `uv run pytest --cov=. -q` | 577 passed, 100% coverage | PASS |
| Ruff clean | `uv run ruff check .` | All checks passed! | PASS |
| EpisodeCopier absent from pipeline | `grep -n "EpisodeCopier" components/pipeline.py` | (no output) | PASS |
| Ad wiring lines >= 8 | `grep -c "ad_detected_guids\|_ad_detector\|_ad_parser\|_audio_editor" components/pipeline.py` | 11 lines | PASS |
| Conditional URL update present | `grep -n "if output_path is not None"` | `pipeline.py:336` | PASS |
| AdStore(db.conn) present | `grep -n "AdStore(db.conn)"` | `pipeline.py:169` | PASS |
| Branch B has no preprocess call | `grep -A5 "elif transcription_exists"` | no "preprocess" in Branch B block | PASS |
| Ad cost saved | `grep -n "save_cost.*ad_cost"` | `pipeline.py:319` | PASS |

---

## Human Verification Required

None. All goal-relevant behaviors are verified programmatically through the test suite.

---

## Summary

Phase 2 achieves its goal completely. The Pipeline has been fully refactored:

- `EpisodeCopier` is eliminated with no trace in production code.
- `AdDetector`, `AdParser`, `AdStore`, and `AudioEditor` are wired into `__init__` and `run()`.
- The four-branch decision tree in `_process_episode` is implemented correctly: Branch A short-circuits on existing output, Branches B/C/D all run the ad detection tail.
- The ad detection tail correctly handles both fresh detection (detect→parse→save→mark) and idempotent reload (load from AdStore).
- URL updates are conditional on `output_path is not None`, preserving the original URL for clean episodes per D-01.
- 577 tests pass, 100% coverage, ruff clean.
- All 22 phase requirements (AD-01 through TEST-03) are satisfied and checked off in REQUIREMENTS.md.

---

_Verified: 2026-03-29_
_Verifier: Claude (gsd-verifier)_
