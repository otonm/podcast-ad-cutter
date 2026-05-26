---
phase: 06-log-access
threats_total: 9
threats_open: 0
threats_closed: 6
threats_accepted: 3
asvs_level: 1
audited_at: 2026-05-26T06:03:47Z
register_authored_at_plan_time: true
---

# Phase 06 — Log Access: Security Review

## Threat Register

| Threat ID | Category | Component | Disposition | Status | Evidence |
|-----------|----------|-----------|-------------|--------|----------|
| T-06-01 | Tampering | create_app signature / call sites | mitigate | CLOSED | `api/server.py:37` — `log_dir: Path` added as 6th required positional arg; `serve()` passes `config.app.paths.log_dir`; all test call sites verified green |
| T-06-02 | Information Disclosure | read_log path traversal | mitigate | CLOSED | `api/routes/logs.py:18-35` — `_validate_path` uses `is_relative_to(log_dir.resolve())`; raises `HTTPBadRequest` (400); tested `TestLogSecurity.test_traversal_on_read_path_returns_400` |
| T-06-03 | Tampering | offset/limit integer parsing | mitigate | CLOSED | `api/routes/logs.py:174-177` — `int()` in `try/except ValueError` → `HTTPBadRequest` (400); negative/oversized offsets produce empty slice via Python slicing |
| T-06-04 | Denial of Service | read_bytes full-file load | accept | CLOSED | Accepted: log files bounded by rotation config (`keep_last`); trusted local-operator tool; no explicit size cap required by LOG-02 |
| T-06-05 | Information Disclosure | tail_log path traversal | mitigate | CLOSED | `api/routes/logs.py:137` — `_validate_path(log_dir, tail)` called before any file I/O; same `is_relative_to` guard; tested `TestLogTail.test_tail_traversal_returns_400` |
| T-06-06 | Denial of Service | unbounded poll cadence / oversized ?bytes | mitigate | CLOSED | `api/routes/logs.py:141` — `interval = max(0.5, min(10.0, interval_raw))`; backfill single bounded seek; tested interval clamping |
| T-06-07 | Denial of Service | file-handle leak on client disconnect | mitigate | CLOSED | `api/routes/logs.py:159-160` — `finally: fh.close()` unconditional on any disconnect or exception |
| T-06-08 | Denial of Service | int()/float() parse for tail bytes/interval | accept | CLOSED | Accepted: malformed values raise `ValueError` → 500; trusted local operator; no explicit error contract in LOG-03 |
| T-06-SC | Tampering | supply chain (pip/npm/cargo) | accept | CLOSED | Accepted: zero new packages installed Phase 06; supply chain surface unchanged |

## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| `serve()` → `create_app` | `log_dir` flows from config (trusted, operator-controlled) into the app factory |
| client → `read_log` | Untrusted `{tail}` path segment crosses into filesystem access |
| client → `read_log` query | Untrusted `?offset`/`?limit` integers control byte-slice arithmetic |
| client → `tail_log` | Untrusted `{tail}` path segment crosses into filesystem read access |
| client → `tail_log` query | Untrusted `?bytes`/`?interval` control read size and poll cadence |
| client disconnect → server | Abrupt disconnect must not leak open file handle or spin poll loop |

## Accepted Risks

| Threat ID | Risk | Rationale |
|-----------|------|-----------|
| T-06-04 | Whole-file `read_bytes()` load | Bounded by rotation config; local trusted-operator tool |
| T-06-08 | Unguarded `int(bytes)` / `float(interval)` in tail_log | Malformed → 500; no explicit error contract; trusted local operator |
| T-06-SC | No supply chain controls | Zero new packages this phase |

## Security Audit 2026-05-26

| Metric | Count |
|--------|-------|
| Threats found | 9 |
| Closed (mitigated) | 6 |
| Closed (accepted) | 3 |
| Open | 0 |
