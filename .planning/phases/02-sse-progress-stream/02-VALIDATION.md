---
phase: 2
slug: sse-progress-stream
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-16
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (auto mode) + pytest-cov |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest` |
| **Full suite command** | `uv run pytest --cov=.` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest`
- **After every plan wave:** Run `uv run pytest --cov=.`
- **Before `/gsd:verify-work`:** Full suite must be green at 100% coverage
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | EVT-01 | — | _Stores counter fields correctly initialized | unit | `uv run pytest tests/test_pipeline.py` | ✅ | ⬜ pending |
| 02-01-02 | 01 | 1 | EVT-01 | — | Pipeline emits RUN_STARTED/COMPLETED events | unit | `uv run pytest tests/test_pipeline.py` | ✅ | ⬜ pending |
| 02-01-03 | 01 | 1 | EVT-01 | — | EPISODE_STAGE_CHANGED fires started+completed per stage | unit | `uv run pytest tests/test_pipeline.py` | ✅ | ⬜ pending |
| 02-01-04 | 01 | 1 | EVT-01 | — | DOWNLOAD_PROGRESS / ENCODE_PROGRESS events emitted with percent | unit | `uv run pytest tests/test_pipeline.py` | ✅ | ⬜ pending |
| 02-01-05 | 01 | 1 | EVT-01 | — | EPISODE_COMPLETED/FAILED payloads include counter fields | unit | `uv run pytest tests/test_pipeline.py` | ✅ | ⬜ pending |
| 02-02-01 | 02 | 1 | EVT-01 | — | SSE route registered and returns text/event-stream | unit | `uv run pytest tests/test_events_route.py` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 1 | EVT-01 | — | SSE handler subscribes on connect, unsubscribes in finally | unit | `uv run pytest tests/test_events_route.py` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 1 | EVT-01 | — | Multiple concurrent SSE clients each receive full stream | unit | `uv run pytest tests/test_events_route.py` | ❌ W0 | ⬜ pending |
| 02-02-04 | 02 | 1 | EVT-01 | — | Client disconnect does not affect pipeline or other clients | unit | `uv run pytest tests/test_events_route.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_events_route.py` — stubs for EVT-01 SSE route tests

*Existing infrastructure covers pipeline test expansion.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SSE stream delivers events live during real pipeline run | EVT-01 | Requires actual pipeline run + real-time observation | Run `uv run python main.py`, connect curl client to `/api/v1/events`, start a pipeline run, observe event stream |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
