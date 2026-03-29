# Phase 2: Pipeline Integration — Ad Detection & Audio Editing - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the Q&A.

**Date:** 2026-03-29
**Phase:** 02-pipeline-integration-ad-detection-audio-editing
**Mode:** discuss
**Areas discussed:** No-qualifying-ads encoding, All-ads-detected edge case, Decision tree refactor scope

---

## Gray Areas Presented

| Area | Description |
|------|-------------|
| No-qualifying-ads encoding | Re-encode vs stream-copy when no qualifying ad segments exist |
| All-ads-detected edge case | Behavior when all audio would be removed |
| Decision tree refactor scope | Branch B shape with ad detection added |

---

## Discussion Log

### No-qualifying-ads encoding

**Q:** When no qualifying ad segments exist, how should AudioEditor produce the output file?
Options: Re-encode (Recommended), Stream-copy, You decide

**A (freeform):** "do not produce any output file and keep the original url in the final feed"

→ Flagged conflict with REQUIREMENTS.md EDIT-02 and PROJECT.md Key Decision.

**Confirm Q:** Confirmed: Keep original URL (your new preference)

**Decision:** AudioEditor keeps `return None` behavior. Pipeline preserves original episode URL for clean episodes. EDIT-02 and roadmap success criterion 2 are overridden.

---

### All-ads-detected edge case

**Q:** When all audio is classified as ads, what should happen?

**A:** Keep original URL (follows from above)

**Decision:** All-audio-is-ads guard stays. Returns None, logs warning. Original URL preserved.

---

### Decision tree refactor scope

**Q:** When transcription exists but no output file (old Branch B), what should the pipeline do?

**A (freeform):** "preprocessing is only needed when transcribing. transcription exists but no audio: download audio -> probe -> check ad_detected -> load or run ad_detector -> audio_editor -> update feed"

**Decision:** Branch B flow = `download → probe → check ad_detected → load segments or run AdDetector → AudioEditor → update feed if output produced`. AudioPreprocessor NOT called (only needed for transcription).

---

## Corrections to Committed Requirements

| Requirement | Old | New |
|-------------|-----|-----|
| EDIT-02 | "always produces an output file... re-encode without cuts" | Keep current behavior: return None when no qualifying ads; pipeline keeps original URL |
| PROJECT.md Key Decision | "AudioEditor always produces output" | AudioEditor returns None for clean episodes; original URL preserved |
| Roadmap success criterion 2 | "AudioEditor always produces an output file..." | Remove or revise to reflect None behavior |
| Plan 02-01 | "Update AudioEditor to always produce output" | Drop or rework — no AudioEditor changes needed |
