---
phase: 1
slug: api-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-14
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (asyncio_mode = "auto") |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest tests/` |
| **Full suite command** | `uv run pytest --cov=.` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/`
- **After every plan wave:** Run `uv run pytest --cov=.`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | INFRA-01 | — | N/A | unit | `uv run pytest tests/api/` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | INFRA-01 | — | N/A | unit | `uv run pytest tests/api/test_event_bus.py` | ❌ W0 | ⬜ pending |
| 01-01-03 | 01 | 1 | INFRA-01 | — | N/A | unit | `uv run pytest tests/test_main.py` | ❌ W0 | ⬜ pending |
| 01-01-04 | 01 | 1 | INFRA-02 | — | Health endpoint returns status, uptime, version | integration | `uv run pytest tests/api/test_health.py` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/api/__init__.py` — package init for API tests
- [ ] `tests/api/test_event_bus.py` — stubs for INFRA-01 EventBus
- [ ] `tests/api/test_health.py` — stubs for INFRA-02 health endpoint
- [ ] `tests/test_main.py` — stubs for dual-mode entry (serve vs pipeline)

*Existing pytest infrastructure (pyproject.toml, conftest.py) covers all tooling requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Server stays alive (process doesn't exit) | INFRA-01 | Subprocess lifecycle not easily unit-testable | Run `python main.py --serve` and observe process remains running |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
