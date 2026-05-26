---
phase: 06-log-access
audited: 2026-05-26
asvs_level: 1
threats_open: 0
---

# Security Audit — Phase 06: Log Access

**Phase:** 06 — Log Access (plans 01, 02, 03)
**Threats Closed:** 6/6 (mitigate) + 3 accepted
**ASVS Level:** 1

---

## Threat Verification

### Mitigate Disposition — All CLOSED

| Threat ID | Category | Disposition | Status | Evidence |
|-----------|----------|-------------|--------|----------|
| T-06-01 | Tampering | mitigate | CLOSED | `api/server.py:37` — `log_dir: Path` is the 6th required positional parameter; `api/server.py:91` — `serve()` passes `config.app.paths.log_dir`; `api/server.py:69` — `app.add_routes(create_logs_router(log_dir))`; all test files confirmed passing 6-arg calls (e.g. `tests/test_api_health.py:28-32`) |
| T-06-02 | Information Disclosure | mitigate | CLOSED | `api/routes/logs.py:18-35` — `_validate_path` resolves path and calls `is_relative_to(log_dir.resolve())`; raises `web.HTTPBadRequest` (400) on traversal; called at `logs.py:166` in `read_log`; tested in `TestLogSecurity.test_traversal_on_read_path_returns_400` (`tests/test_api_logs.py:151`) |
| T-06-03 | Tampering | mitigate | CLOSED | `api/routes/logs.py:174-177` — `int()` parse for offset and limit wrapped in `try/except ValueError` raising `web.HTTPBadRequest` (400); negative/oversized offsets produce empty slice via `data[start : start + limit]`; tested in `TestLogRead.test_read_non_integer_offset_returns_400` and `test_read_non_integer_limit_returns_400` (`tests/test_api_logs.py:236, 247`) |
| T-06-05 | Information Disclosure | mitigate | CLOSED | `api/routes/logs.py:137` — `tail_log` calls `_validate_path(log_dir, tail)` before any file access; same `is_relative_to` guard as T-06-02; tested in `TestLogTail.test_tail_traversal_returns_400` (`tests/test_api_logs.py:356`) |
| T-06-06 | Denial of Service | mitigate | CLOSED | `api/routes/logs.py:140-141` — `interval = max(0.5, min(10.0, interval_raw))` clamps poll cadence; backfill is a single bounded `max(0, size - bytes_back)` seek-and-read (`logs.py:86-87`); tested in `TestLogTail.test_tail_interval_clamped_below` and `test_tail_interval_clamped_above` (`tests/test_api_logs.py:366, 379`) |
| T-06-07 | Denial of Service | mitigate | CLOSED | `api/routes/logs.py:159-160` — `finally: fh.close()` in `tail_log` guarantees file handle closure on any disconnect or exception |

### Accept Disposition — Documented

| Threat ID | Category | Disposition | Accepted Risk |
|-----------|----------|-------------|---------------|
| T-06-04 | Denial of Service | accept | `read_log` loads whole file into memory (`path.read_bytes()`). Log files are bounded by rotation config; endpoint is accessible only to the trusted local operator. No mitigation required. |
| T-06-08 | Denial of Service | accept | `int()` parse on `bytes` (tail) and `float()` parse on `interval` are not try/except-wrapped. Malformed values raise `ValueError` producing a 500. Risk accepted: endpoint is trusted local operator only. |
| T-06-SC | Tampering | accept | No new packages installed during Phase 06 — confirmed by RESEARCH.md Package Legitimacy Audit. Supply chain surface is unchanged. |

---

## Unregistered Flags

The 06-03-SUMMARY.md `## Threat Flags` section states: "No new threat surface beyond what is documented in the plan's threat_model." No unregistered flags.

---

## Audit Notes

- `_validate_path` is called at both `read_log` (`logs.py:166`) and `tail_log` (`logs.py:137`) entry points — traversal guard covers all log-reading entry points.
- The `bytes_back` parameter in `tail_log` (`logs.py:139`) is parsed with `int()` without a try/except, consistent with the T-06-08 accepted risk for trusted operators.
- Route registration order (`/tail` before `{tail:.*}` glob, `logs.py:134` before `logs.py:163`) is correct per D-04; the plan comment on `logs.py:132` documents the ordering constraint.
