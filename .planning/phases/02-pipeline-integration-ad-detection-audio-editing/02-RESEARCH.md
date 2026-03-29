# Phase 2: Pipeline Integration — Ad Detection & Audio Editing - Research

**Researched:** 2026-03-29
**Domain:** Python async pipeline orchestration — wiring AdDetector, AdParser, AdStore, AudioEditor into Pipeline; removing EpisodeCopier; refactoring decision tree
**Confidence:** HIGH

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** AudioEditor keeps its current `return None` behavior when no qualifying ad segments exist. The pipeline preserves the original episode URL unchanged — no local output file is produced for clean episodes.
- **D-02:** The all-audio-is-ads guard also keeps its current behavior (log warning, return None). Consistent with D-01.
- **D-03:** This OVERRIDES the committed requirement EDIT-02 ("always produces an output file") and the PROJECT.md Key Decision ("AudioEditor always produces output"). REQUIREMENTS.md EDIT-02, Plan 02-01, and SUCCESS CRITERION 2 in the roadmap must all be updated to reflect D-01.
- **D-04:** When transcription exists but no output file: `download → probe → check ad_detected → load segments from AdStore OR run AdDetector → AudioEditor → update feed URL if output produced`.
- **D-05:** AudioPreprocessor (mono 16-bit PCM) is NOT run in this branch. Preprocessing is only needed before transcription. AudioEditor works on the raw audio directly.
- **D-06:** If AudioEditor returns None in this branch (no qualifying ads), keep the original episode URL — no URL update.
- **D-07:** Branch A (short-circuit): `output_exists → reconstruct URL only`. No download, no processing.
- **D-08:** All other branches run missing stages in order, gated by state: `transcription_exists` → skip download+probe+preprocess+transcribe+extract_topics; `ad_detected` (from AdStore.get_detected_guids) → skip AdDetector, load segments from AdStore.get_segments_for_guid(); AudioEditor called with segments → if returns Path, update URL; if returns None, keep original URL.
- **D-09:** `ad_detected_guids` set is loaded once before the episode loop (same pattern as `transcribed_guids`).
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

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AD-01 | Pipeline instantiates AdDetector with config from `models.ad_detection` (provider, model, api_key) | D-12; PROVIDER_KEY_MAP pattern already in use for transcription and topic extraction |
| AD-02 | Pipeline instantiates AdParser (stateless, no config needed) | D-13; AdParser.__init__ takes no arguments |
| AD-03 | Pipeline instantiates AdStore and loads `ad_detected_guids` set before processing episodes | D-09, D-14; mirrors `transcribed_guids = await transcription_store.get_transcribed_guids()` |
| AD-04 | For each episode: if GUID not in `ad_detected_guids`, call AdDetector.detect() with transcript segments and topic extraction | D-08; requires `transcription_store.get_segments_for_guid(guid)` and `topic_store.get_topic_for_guid(guid)` |
| AD-05 | Pass AdDetector output through AdParser.parse() to merge consecutive segments | AdParser.parse(guid, detections, transcription_segments) → list[AdSegment] |
| AD-06 | Save merged AdSegment objects to AdStore.save_segments(); call AdStore.mark_detected() | AdStore.save_segments(guid, segments) + AdStore.mark_detected(guid) |
| AD-07 | If GUID already in `ad_detected_guids`, load existing AdSegment objects from AdStore.get_segments_for_guid() | AdStore.get_segments_for_guid(guid) → list[AdSegment] |
| EDIT-01 | Pipeline instantiates AudioEditor with output_dir, file_type, and bitrate from config | D-15; config.app.output.file_type and config.app.output.bitrate; config.app.paths.output_dir |
| EDIT-02 | OVERRIDDEN by D-01: AudioEditor keeps return-None behavior; no code change to audio_editor.py | No implementation needed |
| EDIT-03 | Pipeline calls AudioEditor.edit() with required parameters | edit(guid, raw_path, segments, feed_slug, pub_date, episode.title, min_duration_ms, min_confidence, total_duration_s) |
| EDIT-04 | Output file written to `output/{feed_slug}/{DD.MM.YYYY}-{episode-title}.{ext}` | Already handled internally by AudioEditor |
| EDIT-05 | If output file already exists, skip audio editing (AudioEditor implements this guard internally) | AudioEditor.edit() checks dest.exists() and returns dest if so |
| EDIT-06 | EpisodeCopier removed from Pipeline | D-10; remove import, remove __init__ assignment, remove copy() calls |
| PIPE-01 | Decision tree updated — checks transcription_exists, ad_detected, output_exists | New branch shape per D-07/D-08 |
| PIPE-02 | Branch A: output_exists → reconstruct URL only | D-07 |
| PIPE-03 | Ad detection cost saved to CostTrackingStore | D-11; `await cost_store.save_cost(ad_detection_cost)` |
| PIPE-04 | Episode URL updated after audio edit (only when AudioEditor returns Path) | D-06/D-08; conditional `if output_path is not None:` before URL update |
| PIPE-05 | RSS feed published with updated episode URLs | Already implemented; URL update propagates through existing FeedPublisher.update_episode_url() |
| TEST-01 | Pipeline tests cover full ad detection + audio editing path | New branch tests needed in test_pipeline.py |
| TEST-02 | Pipeline tests cover idempotency branches (ad already detected, output already exists) | Two idempotency tests needed |
| TEST-03 | All existing tests remain green; 100% coverage; ruff clean | Verified: 564 tests passing, 100% coverage baseline |
</phase_requirements>

---

## Summary

Phase 2 wires four existing, fully tested components (AdDetector, AdParser, AdStore, AudioEditor) into Pipeline. The codebase already has every component needed — no new components are being built. The work is purely orchestration: updating `__init__`, `run()`, and `_process_episode()` in `pipeline.py`, then adding comprehensive tests.

The critical design insight from CONTEXT.md D-03 is that EDIT-02 (the requirement to "always produce output") is overridden: AudioEditor's current `return None` behavior is correct and must not change. Plan 02-01 ("Update AudioEditor to always produce output") must be replaced by an update to the requirements documents instead.

The decision tree reshapes around a new three-dimension check: `output_exists`, `transcription_exists`, `ad_detected`. Branch A (output exists) short-circuits immediately. All other branches share a common tail: after transcription exists, they call ad detection (or load from AdStore), call AudioEditor with segments, then conditionally update the URL only if AudioEditor returned a Path.

**Primary recommendation:** Implement in four sequential plans — (1) update REQUIREMENTS.md/ROADMAP.md to reflect D-01; (2) wire AdDetector, AdParser, AdStore into Pipeline.__init__ and run(); (3) rewrite the _process_episode decision tree; (4) write comprehensive integration tests.

---

## Standard Stack

### Core (no new dependencies needed)
All libraries are already installed.

| Library | Version (verified) | Purpose | Source |
|---------|-------------------|---------|--------|
| aiosqlite | >=0.22.1 | Async SQLite via AdStore | pyproject.toml |
| pytest-asyncio | >=0.24 | Async test execution | pyproject.toml |
| pytest | >=9.0.2 | Test framework | pyproject.toml |
| pytest-cov | >=7.0.0 | Coverage enforcement | pyproject.toml |
| ruff | >=0.15.7 | Linting | pyproject.toml |

**No new packages needed.** All components that Phase 2 wires together are already present.

### Commands
```bash
uv run pytest                  # run tests
uv run pytest --cov=.          # run with coverage (must be 100%)
uv run ruff check .            # lint
```

---

## Architecture Patterns

### Decision Tree Redesign

The current four-branch tree (`transcription_exists × audio_exists`) must be replaced with a new tree where `output_exists` is the primary gate, and the remaining branches all share a common post-transcription tail.

**New branch shape:**

```
Branch A: output_exists → reconstruct URL, return
Branch B: transcription_exists, no output:
    download → probe → save meta → ad_detected? → load OR detect → edit → maybe update URL
Branch C: audio_exists (output dir), no transcription:
    (current Branch C — unchanged except: add ad detection + edit tail after topic extraction)
Branch D: nothing exists:
    (current Branch D — unchanged except: add ad detection + edit tail after topic extraction)
```

**Key insight:** Branches C and D only differ in whether they download first. Both now share an identical post-transcription tail: ad detection → audio editing → conditional URL update.

### Post-Transcription Ad Detection Tail (Branches B, C, D)

After transcription and topic extraction are complete (or loaded from store):

```python
# Load transcript segments for ad detection
t_segments = await transcription_store.get_segments_for_guid(episode.guid)
topic = await topic_store.get_topic_for_guid(episode.guid)

if episode.guid not in ad_detected_guids:
    _, detections, ad_cost = await self._ad_detector.detect(
        episode.guid, t_segments, topic
    )
    segments = self._ad_parser.parse(episode.guid, detections, t_segments)
    await ad_store.save_segments(episode.guid, segments)
    await ad_store.mark_detected(episode.guid)
    await cost_store.save_cost(ad_cost)
    ad_detected_guids.add(episode.guid)
else:
    segments = await ad_store.get_segments_for_guid(episode.guid)

output_path = await self._audio_editor.edit(
    episode.guid,
    raw_path,
    segments,
    feed_slug,
    episode.pub_date,
    episode.title,
    min_duration_ms=self._config.app.ad_detection.min_duration,
    min_confidence=self._config.app.ad_detection.min_confidence,
    total_duration_s=meta.duration,
)

if output_path is not None:
    new_url = FeedPublisher.episode_url(
        self._config.app.base_url, feed_slug, episode.pub_date, episode.title,
        self._config.app.output.file_type
    )
    await store.update_episode_url(episode.guid, new_url)
    await self._feed_publisher.update_episode_url(feed.title, episode.guid, new_url)
# else: keep original episode URL unchanged — no update call
```

**CRITICAL:** When AudioEditor returns None, `update_episode_url` is NOT called. The episode keeps its original URL. This means the existing `await store.update_episode_url(...)` at the bottom of `_process_episode` (which currently runs unconditionally) must be removed and replaced with conditional logic.

### Branch A: Output Exists — Reconstruct URL Only

```python
if audio_exists:
    # Branch A: output already produced — reconstruct URL
    ext = existing_audio.suffix.lstrip(".")
    new_url = FeedPublisher.episode_url(
        self._config.app.base_url, feed_slug, episode.pub_date, episode.title, ext
    )
    await store.update_episode_url(episode.guid, new_url)
    await self._feed_publisher.update_episode_url(feed.title, episode.guid, new_url)
    return
```

This is simpler than the current Branch A because `output_exists` becomes the only condition — `transcription_exists` is irrelevant when the output file already exists.

### __init__ Changes

**Add:**
```python
from components.ad_detector import AdDetector
from components.ad_parser import AdParser
from components.audio_editor import AudioEditor
from database.ad_store import AdStore

# in __init__:
ad_cfg = config.app.models.ad_detection
self._ad_detector = AdDetector(
    provider=ad_cfg.provider,
    model=ad_cfg.model,
    api_key=getattr(config.credentials, PROVIDER_KEY_MAP[ad_cfg.provider]),
)
self._ad_parser = AdParser()
self._audio_editor = AudioEditor(
    output_dir=config.app.paths.output_dir,
    file_type=config.app.output.file_type,
    bitrate=config.app.output.bitrate,
)
```

**Remove:**
```python
from components.episode_copier import EpisodeCopier  # DELETE
self._episode_copier = EpisodeCopier(...)            # DELETE
```

**Note:** `AdStore` is NOT instantiated in `__init__` — it takes an open `db.conn` and is instantiated inside the `async with Database(...)` block in `run()`, alongside `TranscriptionStore`, `TopicStore`, etc.

### run() Changes

Inside the `async with Database(self._db_path) as db:` block, before the episode loop, add:

```python
ad_store = AdStore(db.conn)
ad_detected_guids = await ad_store.get_detected_guids()
```

Pass `ad_store` and `ad_detected_guids` to `_process_episode()` in the same way `transcription_store` and `transcribed_guids` are passed.

### _process_episode Signature Changes

Add to the keyword-only parameters:
- `ad_store: AdStore`
- `ad_detected_guids: set[str]`

Remove from the keyword-only parameters:
- No parameter removals needed (existing params stay; transcription_store is still needed for get_segments_for_guid)

### Branch B Detail: Transcription Exists, No Output

This branch replaces the current "download → probe → preprocess → copy" branch. AudioPreprocessor is NOT called (D-05):

```
download → probe → save meta → [ad detection tail with raw_path]
```

The `raw_path` from the download is passed directly to `AudioEditor.edit()` without preprocessing.

### Anti-Patterns to Avoid

- **Calling AudioPreprocessor in Branch B:** D-05 explicitly forbids this — preprocessing is for transcription only, AudioEditor takes raw audio.
- **Unconditional URL update:** The current `await store.update_episode_url(...)` at the end of `_process_episode` is unconditional. Phase 2 must make it conditional on AudioEditor returning a Path.
- **Moving AdStore instantiation to __init__:** AdStore requires an open db connection. It must be instantiated inside `async with Database(...)`.
- **Updating `ad_detected_guids` set only when running detection:** Also add the guid when loading from AdStore is skipped — no, the set is already pre-loaded from the database. The guid IS in `ad_detected_guids` when we load from store. Only add to the in-memory set after running fresh detection.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Ad segment persistence | Custom SQL | AdStore (already built) | AdStore already has get_detected_guids, save_segments, mark_detected, get_segments_for_guid |
| Ad segment merging | Custom merge logic | AdParser (already built) | AdParser handles consecutive index grouping |
| Audio cutting | Custom ffmpeg wrapper | AudioEditor (already built) | AudioEditor handles filter_complex, progress, dest.exists() guard, codec mapping |
| Cost saving | Custom query | `cost_store.save_cost(AdDetectionCost(...))` | CostTrackingStore already polymorphic over cost types |

---

## Common Pitfalls

### Pitfall 1: Unconditional URL Update Breaks "No Ads" Episodes

**What goes wrong:** If `await store.update_episode_url(episode.guid, new_url)` is called unconditionally at the end of `_process_episode`, episodes with no qualifying ads get a URL pointing to a file that doesn't exist (AudioEditor returned None, so no file was written).

**Why it happens:** The current code ends every branch with an unconditional URL update. The new branches must only update when AudioEditor returned a Path.

**How to avoid:** Restructure the logic so URL update only happens in two places: (1) Branch A (output already exists — always update), and (2) inside the `if output_path is not None:` block after AudioEditor.

**Warning signs:** Test assertions that check `update_episode_url.assert_not_awaited()` when AudioEditor returns None.

### Pitfall 2: AudioPreprocessor Called in Branch B

**What goes wrong:** Branch B currently calls `AudioPreprocessor.preprocess()` before copying. The new Branch B must NOT call it, because AudioEditor works on raw audio (D-05).

**Why it happens:** Copy-paste from the old Branch B structure.

**How to avoid:** Check the new Branch B flow explicitly: `download → probe → save meta → [ad tail]`. No preprocess step.

**Warning signs:** `m_prep.return_value.preprocess.assert_not_called()` failing in Branch B test.

### Pitfall 3: AdStore Not Added to _process_episode Signature

**What goes wrong:** `ad_store` is needed in `_process_episode` but not passed through. Runtime `NameError`.

**Why it happens:** `run()` creates `ad_store` locally but `_process_episode` needs it for both the load-from-store and save-to-store paths.

**How to avoid:** Add `ad_store: AdStore` as a keyword-only parameter to `_process_episode` alongside `transcription_store`, `topic_store`, etc. Also pass `ad_detected_guids: set[str]` the same way `transcribed_guids` is passed.

### Pitfall 4: Failing to Load Transcript Segments for Ad Detection

**What goes wrong:** `AdDetector.detect()` takes `list[TranscriptionSegment]` but `_process_episode` only has the `Transcription` object (text only) in some branches. Transcript segments must be loaded from `TranscriptionStore`.

**Why it happens:** In Branches C and D, `transcribe()` returns `(guid, transcription, segments, cost)` — segments are available immediately. But if transcription already exists (Branch B) segments must be fetched with `transcription_store.get_segments_for_guid(episode.guid)`.

**How to avoid:** In Branch B: `t_segments = await transcription_store.get_segments_for_guid(episode.guid)`. In Branches C/D: segments are the return value from `self._transcriptor.transcribe()` — use them directly.

**Warning signs:** `AdDetector.detect.assert_awaited_once_with(...)` failing with wrong arguments.

### Pitfall 5: Test Helper _wire_branch_mocks Is Not Extended

**What goes wrong:** Existing branch tests use `_wire_branch_mocks` which wires mocks for `EpisodeCopier` (to be removed) but not for `AdStore`, `AdDetector`, `AdParser`, `AudioEditor`. New tests that use the helper without updating it will fail or have wrong assertions.

**Why it happens:** The helper is tightly coupled to the current component set.

**How to avoid:** Update `_wire_branch_mocks` to add mocks for `AdStore`, `AdDetector`, `AdParser`, `AudioEditor` and remove `EpisodeCopier`. All tests in the file use this helper — update it once, all tests benefit.

**Warning signs:** `AttributeError: Mock object has no attribute 'edit'` in new branch tests.

### Pitfall 6: ruff TC003/ANN on AdStore TYPE_CHECKING Import

**What goes wrong:** AdStore uses `if TYPE_CHECKING: import aiosqlite` — the same pattern as TranscriptionStore. When pipeline.py imports AdStore and adds an `AdStore` type annotation, ruff may flag TC003 (move to TYPE_CHECKING block) depending on where it's used.

**Why it happens:** The test file's ruff ignore list includes TC003 for test files but pipeline.py is production code.

**How to avoid:** In pipeline.py, import `AdStore` inside `if TYPE_CHECKING:` for the type annotation and as a direct import only if needed at runtime. Given that `AdStore(db.conn)` is called at runtime in `run()`, it needs a direct runtime import — not just TYPE_CHECKING.

### Pitfall 7: Config Path for output.file_type / output.bitrate

**What goes wrong:** AudioEditor needs `file_type` and `bitrate` from `config.app.output.file_type` / `config.app.output.bitrate`. Test mocks that use `MagicMock()` for config will auto-create these attributes, but explicit tests for the constructor call need to set them.

**Why it happens:** Config mock setup in test helpers only sets `models.*` and `paths.*` — `output.*` is new.

**How to avoid:** Add `config.app.output.file_type = "mp3"` and `config.app.output.bitrate = "128k"` to `_branch_config()` and any test that constructs `Pipeline(config)` with assertions on AudioEditor.

---

## Code Examples

### AdDetector.detect() Call Signature (source: components/ad_detector.py)

```python
# AdDetectionResult = tuple[str, list[AdSegmentDetection], AdDetectionCost]
guid, detections, ad_cost = await self._ad_detector.detect(
    episode.guid,
    t_segments,           # list[TranscriptionSegment]
    topic,                # TopicExtraction | None
)
```

### AdParser.parse() Call Signature (source: components/ad_parser.py)

```python
segments = self._ad_parser.parse(
    episode.guid,
    detections,           # list[AdSegmentDetection]
    t_segments,           # list[TranscriptionSegment]
)
```

### AdStore Usage Pattern (source: database/ad_store.py)

```python
# Before episode loop — in run():
ad_store = AdStore(db.conn)
ad_detected_guids = await ad_store.get_detected_guids()  # set[str]

# During episode processing:
await ad_store.save_segments(episode.guid, segments)  # note: guid param is first
await ad_store.mark_detected(episode.guid)
segments = await ad_store.get_segments_for_guid(episode.guid)
```

**Important:** `AdStore.save_segments` signature is `save_segments(guid: str, segments: list[AdSegment])` — guid is a separate first argument, not embedded in each AdSegment object.

### AudioEditor.edit() Call Signature (source: components/audio_editor.py)

```python
output_path = await self._audio_editor.edit(
    episode.guid,
    raw_path,
    segments,                                              # list[AdSegment]
    feed_slug,
    episode.pub_date,
    episode.title,
    min_duration_ms=self._config.app.ad_detection.min_duration,
    min_confidence=self._config.app.ad_detection.min_confidence,
    total_duration_s=meta.duration,                       # float from AudioMetadata
)
# output_path is Path | None
```

### cost_store.save_cost for AdDetectionCost (source: models/ad_detection.py)

```python
# AdDetectionCost fields: provider, model, cost
await cost_store.save_cost(ad_cost)  # same call as for TranscriptionCost, TopicExtractionCost
```

### Existing Test Mock Pattern (source: tests/test_pipeline.py)

The current `_wire_branch_mocks` function parameters:
```python
def _wire_branch_mocks(
    m_dl, m_fp, m_pub, m_db, m_store, m_ts, m_ams, m_cs,
    m_ep_dl, m_prober, m_prep, m_trans, m_copier, m_topic_ext, m_topic_store,
    *, episodes, parsed, transcribed_guids, extracted_guids=None,
) -> None:
```

After Phase 2, `m_copier` is removed and `m_ad_detector`, `m_ad_parser`, `m_ad_store`, `m_audio_editor` are added. The function signature and all callers must be updated in sync.

### TopicStore.get_topic_for_guid (source: database/topic_store.py)

```python
topic = await topic_store.get_topic_for_guid(episode.guid)  # TopicExtraction | None
```

This is how the pipeline passes topic context to AdDetector in Branches B (transcription already exists) and in the post-transcription tail of C/D.

---

## Runtime State Inventory

Step 2.5: SKIPPED — this is a greenfield wiring phase with no rename/refactor/migration. No stored data, live service config, OS registrations, secrets, build artifacts, or runtime state need updating.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12+ | Project runtime | Available | 3.13.5 | — |
| uv | Package manager | Available | 0.10.12 | — |
| ffmpeg | AudioEditor (already tested) | Available | 7.1.3 | — |
| aiosqlite | AdStore | Available (installed) | >=0.22.1 | — |
| litellm | AdDetector | Available (installed) | >=1.82.6 | — |
| pytest-asyncio | Tests | Available (installed) | >=0.24 | — |

**No missing dependencies.** All required tools and libraries are already present.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9+ with pytest-asyncio |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| Quick run command | `uv run pytest tests/test_pipeline.py -x` |
| Full suite command | `uv run pytest --cov=. -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AD-01 | AdDetector instantiated from config | unit | `uv run pytest tests/test_pipeline.py::test_pipeline_constructs_ad_detector -x` | Wave 0 |
| AD-02 | AdParser instantiated (stateless) | unit | `uv run pytest tests/test_pipeline.py::test_pipeline_constructs_ad_parser -x` | Wave 0 |
| AD-03 | ad_detected_guids loaded before loop | unit | `uv run pytest tests/test_pipeline.py -k "ad_detected" -x` | Wave 0 |
| AD-04 | AdDetector.detect() called with segments+topic | unit | `uv run pytest tests/test_pipeline.py::test_branch_d_runs_ad_detection -x` | Wave 0 |
| AD-05 | AdParser.parse() called with detections | unit | included in AD-04 test | Wave 0 |
| AD-06 | AdStore.save_segments + mark_detected called | unit | included in AD-04 test | Wave 0 |
| AD-07 | Segments loaded from AdStore when ad_detected | unit | `uv run pytest tests/test_pipeline.py::test_branch_d_ad_already_detected_skips_detection -x` | Wave 0 |
| EDIT-01 | AudioEditor instantiated from config | unit | `uv run pytest tests/test_pipeline.py::test_pipeline_constructs_audio_editor -x` | Wave 0 |
| EDIT-02 | OVERRIDDEN — update REQUIREMENTS.md | n/a | manual | n/a |
| EDIT-03 | AudioEditor.edit() called with correct args | unit | included in AD-04 test | Wave 0 |
| EDIT-05 | Output exists → AudioEditor skips internally | unit | `uv run pytest tests/test_pipeline.py::test_branch_a_output_exists_skips_all_processing -x` | Wave 0 |
| EDIT-06 | EpisodeCopier not imported/instantiated | unit | verify no EpisodeCopier patch in tests | Wave 0 |
| PIPE-01 | Decision tree checks output_exists, ad_detected | unit | branch tests | Wave 0 |
| PIPE-02 | Branch A: output_exists → URL only | unit | `uv run pytest tests/test_pipeline.py::test_branch_a_output_exists_skips_all_processing -x` | Wave 0 |
| PIPE-03 | AdDetectionCost saved to CostTrackingStore | unit | assert save_cost.await_count includes ad cost | Wave 0 |
| PIPE-04 | URL updated only when AudioEditor returns Path | unit | test with AudioEditor returning None + returning Path | Wave 0 |
| PIPE-05 | RSS published with updated URLs | unit | existing feed publisher tests (no change) | ✅ |
| TEST-01 | Full ad detection + audio editing path | unit | `uv run pytest tests/test_pipeline.py -k "ad_detection" -x` | Wave 0 |
| TEST-02 | Idempotency branches | unit | `uv run pytest tests/test_pipeline.py -k "idempotent or already_detected or output_exists" -x` | Wave 0 |
| TEST-03 | 100% coverage, ruff clean | coverage | `uv run pytest --cov=. -q && uv run ruff check .` | ✅ (baseline) |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_pipeline.py -x --tb=short`
- **Per wave merge:** `uv run pytest --cov=. -q && uv run ruff check .`
- **Phase gate:** Full suite green (`564+ tests, 100% coverage`) before `/gsd:verify-work`

### Wave 0 Gaps

The following test cases must be created as part of plan 02-04 (or alongside the implementation plans):

- [ ] `tests/test_pipeline.py` — `test_pipeline_constructs_ad_detector` — covers AD-01
- [ ] `tests/test_pipeline.py` — `test_pipeline_constructs_ad_parser` — covers AD-02
- [ ] `tests/test_pipeline.py` — `test_branch_b_transcription_exists_no_output_with_ads` — covers D-04, D-05, EDIT-03
- [ ] `tests/test_pipeline.py` — `test_branch_b_transcription_exists_no_output_no_ads_keeps_url` — covers D-06, PIPE-04
- [ ] `tests/test_pipeline.py` — `test_branch_d_runs_ad_detection` — covers AD-04, AD-05, AD-06, PIPE-03
- [ ] `tests/test_pipeline.py` — `test_branch_d_ad_already_detected_skips_detection` — covers AD-07
- [ ] `tests/test_pipeline.py` — `test_branch_a_output_exists_skips_all_processing` — covers PIPE-02 (new Branch A shape)
- [ ] `tests/test_pipeline.py` — `test_audio_editor_returns_none_no_url_update` — covers PIPE-04 negative case

Existing tests that exercise `EpisodeCopier` must be updated to remove that mock and add `AdStore`/`AudioEditor`/`AdDetector`/`AdParser` mocks instead.

---

## Open Questions

1. **Where exactly does Branch B get `t_segments` from?**
   - What we know: In Branch B, transcription already exists in the DB. `_process_episode` has access to `transcription_store`.
   - What's clear: Call `await transcription_store.get_segments_for_guid(episode.guid)` at the start of the post-transcription ad tail in Branch B.
   - Recommendation: This is confirmed by the code — `TranscriptionStore.get_segments_for_guid(guid)` exists and returns `list[TranscriptionSegment]`.

2. **Branch C: "audio exists, no transcription" — is this branch still valid after Phase 2?**
   - What we know: Branch C exists today (audio present, no transcription). After Phase 2, the "audio" is an edited output file, not a raw download. Running ad detection on it would be wrong.
   - What's unclear: Is Branch C expected to remain, or is it only a legacy state that shouldn't occur with the new pipeline?
   - Recommendation: Keep Branch C for now (state recovery path). After transcription+topic extraction in Branch C, run the same ad detection tail using the `existing_audio` path as the `raw_path` argument to AudioEditor. This is consistent with D-08.

3. **`_process_episode` method — `raw_path` scoping**
   - What we know: In Branch B and D, `raw_path` is the return value from `episode_downloader.download()`. In Branch C, the input to AudioEditor is `existing_audio`. In Branch B, the raw path comes from the fresh download.
   - Recommendation: Each branch sets its own local `raw_path` before calling the shared ad detection tail. The tail references `raw_path` as a local variable. No shared assignment needed at the top of the method.

---

## Sources

### Primary (HIGH confidence)
- `components/pipeline.py` — Direct source read; current decision tree, component instantiation patterns, _process_episode signature
- `components/ad_detector.py` — Direct source read; detect() signature, return type AdDetectionResult
- `components/ad_parser.py` — Direct source read; parse() signature
- `database/ad_store.py` — Direct source read; all method signatures and semantics
- `components/audio_editor.py` — Direct source read; edit() signature, return type Path|None, existing dest.exists() guard
- `config/config_loader.py` — Direct source read; AdDetectionConfig, OutputConfig, PROVIDER_KEY_MAP
- `database/topic_store.py` — Direct source read; get_topic_for_guid() signature
- `tests/test_pipeline.py` — Direct source read; existing mock patterns, _wire_branch_mocks structure, branch test structure
- `models/ad_detection.py` — Direct source read; AdSegment, AdDetectionCost, AdSegmentDetection

### Secondary (MEDIUM confidence)
- `pyproject.toml` — Verified dependencies, ruff config, pytest config
- `tests/test_ad_store.py` — Verified AdStore API usage patterns

### Tertiary (LOW confidence)
- None

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; all libraries already in pyproject.toml
- Architecture: HIGH — all component APIs verified directly from source
- Pitfalls: HIGH — identified from direct code analysis, not speculation
- Test patterns: HIGH — existing test_pipeline.py provides exact mock structure to follow

**Research date:** 2026-03-29
**Valid until:** This research references specific source files. Valid indefinitely unless source files change.
