---
phase: 6
slug: log-access
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-22
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (asyncio_mode = "auto") |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `uv run pytest tests/test_api_logs.py tests/test_api_server.py -x` |
| **Full suite command** | `uv run pytest --cov=. && uv run ruff` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/test_api_logs.py tests/test_api_server.py -x`
- **After every plan wave:** Run `uv run pytest --cov=. && uv run ruff`
- **Before `/gsd:verify-work`:** Full suite must be green with 100% coverage
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | LOG-01 | — | N/A | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 | ⬜ pending |
| 06-01-02 | 01 | 1 | LOG-02 | path traversal | Returns 400 on `../` in tail | unit | `uv run pytest tests/test_api_logs.py::TestLogSecurity -x` | ❌ Wave 0 | ⬜ pending |
| 06-01-03 | 01 | 1 | LOG-02 | — | N/A | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 | ⬜ pending |
| 06-01-04 | 01 | 1 | LOG-03 | — | N/A | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 | ⬜ pending |
| 06-01-05 | 01 | 1 | LOG-01..03 | — | create_app wiring | unit | `uv run pytest tests/test_api_server.py -x` | ✅ exists | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_api_logs.py` — all LOG-01, LOG-02, LOG-03 test stubs
  - `TestLogList` — GET /api/v1/logs returns hierarchical JSON; empty when no logs dir
  - `TestLogRead` — full content; byte-range pagination; 404 on missing file; Content-Type header; X-Log-* headers
  - `TestLogSecurity` — path traversal returns 400 for `../`; path traversal on /tail endpoint returns 400
  - `TestLogTail` — SSE Content-Type; backfill sends last N bytes; new content appears as SSE event; rotation detection

*Existing pytest-asyncio infrastructure covers all async test needs — no new packages.*

---

## Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| LOG-01 | GET /api/v1/logs returns `{"app_logs": [...], "episode_logs": {...}}` | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-01 | app_logs contains top-level .log files with filename, size_bytes, last_modified | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-01 | episode_logs grouped by feed slug | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-01 | returns empty lists when log_dir does not exist | unit | `uv run pytest tests/test_api_logs.py::TestLogList -x` | ❌ Wave 0 |
| LOG-02 | GET /api/v1/logs/{filename} returns full content as text/plain | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-02 | ?offset=N&limit=N returns correct byte slice | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-02 | X-Log-Size, X-Log-Offset, X-Log-Limit response headers set correctly | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-02 | path traversal (../) returns 400 | unit | `uv run pytest tests/test_api_logs.py::TestLogSecurity -x` | ❌ Wave 0 |
| LOG-02 | missing file returns 404 | unit | `uv run pytest tests/test_api_logs.py::TestLogRead -x` | ❌ Wave 0 |
| LOG-03 | GET /api/v1/logs/{filename}/tail returns text/event-stream | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | backfill: last N bytes sent as first SSE data event | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | new content appended to file appears as SSE event | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | rotation detected (st_size < last_pos) restarts from byte 0 | unit | `uv run pytest tests/test_api_logs.py::TestLogTail -x` | ❌ Wave 0 |
| LOG-03 | path traversal on tail endpoint returns 400 | unit | `uv run pytest tests/test_api_logs.py::TestLogSecurity -x` | ❌ Wave 0 |

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| SSE tail streams in real time from a live file | LOG-03 | Requires live file append + SSE client timing | `curl -N http://localhost:8080/api/v1/logs/<file>/tail` while appending to the file |
