---
phase: 4
slug: config-feed-management
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-05-17
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (auto mode) + aiohttp.test_utils |
| **Config file** | `pyproject.toml` — `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest tests/ -x -q` |
| **Full suite command** | `uv run pytest --cov=. --cov-report=term-missing` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/ -x -q`
- **After every plan wave:** Run `uv run pytest --cov=. --cov-report=term-missing`
- **Before `/gsd:verify-work`:** Full suite must be green at 100% coverage
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | STAT-02 | — | Credentials values never returned | unit | `uv run pytest tests/test_api_settings.py -x -q` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | STAT-03 | — | Unknown PATCH keys → 422 | unit | `uv run pytest tests/test_api_settings.py -x -q` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 2 | FEED-01 | — | Episode counts from DB, not config | unit | `uv run pytest tests/test_api_feeds.py -x -q` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 2 | FEED-02 | — | Duplicate title → 409 | unit | `uv run pytest tests/test_api_feeds.py -x -q` | ❌ W0 | ⬜ pending |
| 04-02-03 | 02 | 2 | FEED-03 | — | PATCH excludes title changes | unit | `uv run pytest tests/test_api_feeds.py -x -q` | ❌ W0 | ⬜ pending |
| 04-02-04 | 02 | 2 | FEED-04 | — | DELETE missing slug → 404 | unit | `uv run pytest tests/test_api_feeds.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_api_settings.py` — stubs for STAT-02, STAT-03
- [ ] `tests/test_api_feeds.py` — stubs for FEED-01, FEED-02, FEED-03, FEED-04
- [ ] `tests/conftest.py` — shared aiohttp test client fixture (already exists)

*Existing pytest infrastructure covers the framework; only new test files needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Atomic write survives mid-write process kill | STAT-03 | Cannot simulate process kill in unit test | Run PATCH, kill process mid-write, verify config.yaml is unchanged |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
