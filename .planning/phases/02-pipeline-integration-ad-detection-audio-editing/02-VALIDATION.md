---
phase: 2
slug: pipeline-integration-ad-detection-audio-editing
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-03-29
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/test_pipeline.py tests/test_ad_detection_models.py tests/test_ad_store.py -x` |
| **Full suite command** | `uv run pytest --cov=. && uv run ruff` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_pipeline.py tests/test_ad_detection_models.py tests/test_ad_store.py -x`
- **After every plan wave:** Run `uv run pytest --cov=. && uv run ruff`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 2-01-01 | 01 | 1 | EDIT-01, EDIT-02 | unit | `uv run pytest tests/test_pipeline.py -k audio_editor -x` | ✅ | ⬜ pending |
| 2-02-01 | 02 | 1 | AD-01, AD-02, PIPE-01 | unit | `uv run pytest tests/test_pipeline.py -k init -x` | ✅ | ⬜ pending |
| 2-03-01 | 03 | 2 | PIPE-02, PIPE-03, PIPE-04, AD-03, AD-04, EDIT-03 | integration | `uv run pytest tests/test_pipeline.py -x` | ✅ | ⬜ pending |
| 2-04-01 | 04 | 3 | TEST-01, TEST-02, TEST-03 | coverage | `uv run pytest --cov=. && uv run ruff` | ✅ | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Existing infrastructure covers all phase requirements.

*All test files exist: tests/test_pipeline.py, tests/test_ad_detection_models.py, tests/test_ad_store.py, tests/test_ad_parser.py — no Wave 0 stubs needed.*

---

## Manual-Only Verifications

All phase behaviors have automated verification.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
