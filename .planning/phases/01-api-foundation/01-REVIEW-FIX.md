---
phase: 01-api-foundation
fixed_at: 2026-05-14T00:00:00Z
review_path: .planning/phases/01-api-foundation/01-REVIEW.md
iteration: 1
findings_in_scope: 4
fixed: 4
skipped: 0
status: all_fixed
---

# Phase 01: Code Review Fix Report

**Fixed at:** 2026-05-14
**Source review:** `.planning/phases/01-api-foundation/01-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 4 (CR-01, WR-01, WR-02, WR-03)
- Fixed: 4
- Skipped: 0

## Fixed Issues

### CR-01 / WR-03: wrap site.start and asyncio.Event().wait() in try/finally

**Files modified:** `api/server.py`
**Commit:** 5c2e0d5
**Applied fix:** Added `try/finally` around `site.start()` and `asyncio.Event().wait()` so `runner.cleanup()` is always called — even when `CancelledError` is raised on shutdown (CR-01) or when `site.start()` raises an `OSError` such as address-in-use (WR-03 — covered by the same block).

### WR-01: guard tomllib fallback in _read_version against FileNotFoundError/KeyError

**Files modified:** `api/routes/health.py`, `tests/test_api_health.py`, `pyproject.toml`
**Commits:** 81014f3, 20ba543
**Applied fix:** Wrapped the pyproject.toml fallback path in its own `try/except (FileNotFoundError, KeyError)` that returns `"unknown"` instead of propagating as a 500. Added two unit tests (`test_returns_unknown_when_pyproject_missing`, `test_returns_unknown_when_version_key_missing`) to cover the new branches and restore 100% coverage. Added `S104` to ruff per-file-ignores for test files to suppress the false-positive binding-to-all-interfaces warning triggered by the `"0.0.0.0"` assertion string.

### WR-02: remove dead outer patch in test_serve_flag_calls_serve_and_not_pipeline

**Files modified:** `tests/test_main.py`
**Commit:** 8691a5c
**Applied fix:** Removed the outer `patch("main.serve") as mock_serve` context manager and its four dead setup lines. The inner double-patch structure was replaced with a single `patch("main.serve", mock_serve)` using an `AsyncMock`. Changed the final assertion from `assert_awaited_once()` to `assert_awaited_once_with("0.0.0.0", 8080)` to verify the correct arguments are passed.

---

_Fixed: 2026-05-14_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
