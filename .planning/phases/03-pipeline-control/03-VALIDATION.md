---
phase: 3
slug: pipeline-control
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-16
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 0.24 |
| **Config file** | `pyproject.toml` (`asyncio_mode = "auto"`) |
| **Quick run command** | `uv run pytest tests/test_api_control.py -x` |
| **Full suite command** | `uv run pytest --cov=.` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_api_control.py tests/test_episode_store.py -x`
- **After every plan wave:** Run `uv run pytest --cov=.`
- **Before `/gsd:verify-work`:** Full suite must be green at 100% coverage
- **Max feedback latency:** ~30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 0 | — | — | N/A | stub | `uv run pytest tests/test_api_control.py -x` | ❌ W0 | ⬜ pending |
| 03-xx-01 | xx | 1 | STAT-01 | — | N/A | unit | `uv run pytest tests/test_api_control.py::TestStatus -x` | ❌ W0 | ⬜ pending |
| 03-xx-02 | xx | 1 | CTRL-01 | — | 409 on duplicate | unit | `uv run pytest tests/test_api_control.py::TestStartRun -x` | ❌ W0 | ⬜ pending |
| 03-xx-03 | xx | 1 | CTRL-02 | — | graceful stop via event | unit | `uv run pytest tests/test_api_control.py::TestStopRun -x` | ❌ W0 | ⬜ pending |
| 03-xx-04 | xx | 1 | CTRL-03 | — | slug not found → 404 | unit | `uv run pytest tests/test_api_control.py::TestFeedRun -x` | ❌ W0 | ⬜ pending |
| 03-xx-05 | xx | 2 | CTRL-04 | T-03-01 | STAGE_CASCADE whitelist prevents SQL injection | unit | `uv run pytest tests/test_api_control.py::TestReprocess -x` | ❌ W0 | ⬜ pending |
| 03-xx-06 | xx | 2 | CTRL-05 | — | 409 when run active | unit | `uv run pytest tests/test_api_control.py::TestSkipEpisode -x` | ❌ W0 | ⬜ pending |
| 03-xx-07 | xx | 2 | CTRL-01+D-04 | — | RunState resets to idle after task ends | unit | `uv run pytest tests/test_api_control.py::TestRunStateLifecycle -x` | ❌ W0 | ⬜ pending |
| 03-xx-08 | xx | 2 | CTRL-05 | — | skipped=1 episodes not processed by pipeline | unit | `uv run pytest tests/test_pipeline_stop.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_api_control.py` — stubs for STAT-01 + CTRL-01 through CTRL-05
- [ ] `tests/test_pipeline_stop.py` — graceful stop, force stop, per-episode state updates, skipped episode guard
- [ ] `api/run_state.py` — new module (RunState dataclass, FeedRunCounts, VALID_STAGES constant)
- [ ] `api/routes/control.py` — new module stub

*Existing test infrastructure (`pytest`, `pytest-asyncio`) covers all other requirements.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
