---
phase: 5
slug: database-viewer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-19
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (uv run pytest) |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest tests/test_api_db.py -x` |
| **Full suite command** | `uv run pytest --cov=. && uv run ruff` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_api_db.py -x`
- **After every plan wave:** Run `uv run pytest --cov=. && uv run ruff`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | DB-01,DB-02,DB-03,DB-04 | — | Read-only endpoints; no write path | unit | `uv run pytest tests/test_api_db.py -x` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 1 | DB-01 | — | Episodes list paginated, filtered | unit | `uv run pytest tests/test_api_db.py -k episodes -x` | ❌ W0 | ⬜ pending |
| 05-02-02 | 02 | 1 | DB-02 | — | Transcription 404 on missing | unit | `uv run pytest tests/test_api_db.py -k transcription -x` | ❌ W0 | ⬜ pending |
| 05-02-03 | 02 | 1 | DB-03 | — | Ad detection 404 on missing | unit | `uv run pytest tests/test_api_db.py -k ads -x` | ❌ W0 | ⬜ pending |
| 05-02-04 | 02 | 1 | DB-04 | — | Cost aggregates filtered correctly | unit | `uv run pytest tests/test_api_db.py -k costs -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_api_db.py` — test stubs for DB-01, DB-02, DB-03, DB-04
- [ ] Test fixtures for in-memory SQLite with seeded episodes, transcriptions, ad_detection_runs, cost_tracking

*Existing conftest.py and test infrastructure from Phases 3–4 cover shared fixtures.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| `pipeline_state=complete` when output file exists | DB-01 | Filesystem check cannot be mocked automatically | Create real output file in temp dir; call /api/v1/db/episodes; verify complete state |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
