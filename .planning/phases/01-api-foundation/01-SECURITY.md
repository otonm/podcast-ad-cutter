---
phase: 01-api-foundation
audited: 2026-05-15
threats_found: 5
threats_closed: 5
threats_open: 0
asvs_level: 1
---

# Phase 01: Security Audit

## Threat Register

| ID | Category | Component | Disposition | Status | Evidence |
|----|----------|-----------|-------------|--------|----------|
| T-01-01 | Information disclosure | `api/server.py:serve` — `0.0.0.0` default binding | accept | CLOSED | `main.py:71` — `# noqa: S104 — intentional; firewall is operator responsibility (T-01-01)`; help text at `main.py:66–67` documents all-interface exposure and instructs operator to use `--host` to restrict. Rationale holds: no auth data exposed by binding; risk is operator-scoped. |
| T-01-02 | Information disclosure | `api/routes/health.py` — health response body | accept | CLOSED | `health.py:50–53` — response is `{"status": "ok", "uptime_seconds": <float>, "version": <str>}`. No secrets, no PII, no internal filesystem paths, no host/config details. Version string sourced from `importlib.metadata` / `pyproject.toml[project][version]` only. Rationale holds. |
| T-01-03 | Tampering | `api/routes/health.py` — `GET /api/v1/health` | mitigate | CLOSED | `health.py:49` — handler signature is `async def health(_request: web.Request) -> web.Response`. No access to `_request.query`, `_request.match_info`, `_request.json()`, or any other input surface. Handler constructs its response entirely from `time.monotonic()`, `start_time` (a captured float), and `_read_version()`. Zero injection surface confirmed. |
| T-01-04 | Denial of service | `api/server.py` — unauthenticated endpoint | accept | CLOSED | Single-user local tool on a trusted network; no rate limiting in v1. Threat model explicitly defers to v2 with SEC-01 (API key auth) note in PLAN.md. Rationale holds for stated deployment scope. |
| T-01-05 | Tampering | `api/event_bus.py:emit` — subscriber list iteration | mitigate | CLOSED | `event_bus.py:66` — `for q in list(self._subscribers):` — `list()` creates a snapshot at call time; a concurrent `unsubscribe()` removing from `self._subscribers` cannot corrupt the in-flight iteration. Pattern present exactly as declared. |

## Accepted Risks

### T-01-01 — 0.0.0.0 default binding (Information disclosure)

- **Component:** `api/server.py:serve` via `main.py` argparse default
- **Risk:** Server listens on all network interfaces by default, making the port reachable from any host on the local network.
- **Rationale accepted:** v1 is a single-user local tool. No authentication exists in this phase by design (locked decision). Operator firewall is the stated control. Inline `noqa: S104` comment at `main.py:71` and argparse help text at `main.py:66–67` document this decision in code.
- **Residual control:** `--host` flag allows restriction to loopback at operator discretion.

### T-01-02 — Health response body (Information disclosure)

- **Component:** `api/routes/health.py` — `GET /api/v1/health`
- **Risk:** Endpoint is unauthenticated and publicly readable.
- **Rationale accepted:** Response payload contains only `status`, `uptime_seconds`, and application `version`. None of these fields carry secrets, PII, internal paths, or infrastructure topology. Confirmed by direct code inspection at `health.py:50–53`.

### T-01-04 — Unauthenticated endpoint (Denial of service)

- **Component:** `api/server.py` — all routes
- **Risk:** No rate limiting; any client can flood the server.
- **Rationale accepted:** Trusted local network, single user. Risk is bounded by deployment scope. Threat model references SEC-01 for v2 if scope expands.

## Unregistered Flags

SUMMARY.md `## Threat Flags` states: "No new security surface beyond what was defined in the plan's threat model."

No unregistered flags to record.

## Audit Trail

### 2026-05-15

| Metric | Count |
|--------|-------|
| Threats found | 5 |
| Closed | 5 |
| Open | 0 |

**Verification method per threat:**

- T-01-01 (accept): Confirmed help text `main.py:66–67` and inline `noqa: S104` comment `main.py:71` document operator firewall responsibility. Accept rationale holds.
- T-01-02 (accept): Confirmed response construction at `health.py:50–53` — only `status`, `uptime_seconds`, `version` emitted. No leakage found.
- T-01-03 (mitigate): Grep for `request.query`, `request.match_info`, `request.json`, `body`, `params` in `health.py` returned no matches. Handler is a read-only function consuming zero client input.
- T-01-04 (accept): No rate-limiting middleware present in `server.py` or `health.py`. Accept disposition confirmed as intentional for v1 scope.
- T-01-05 (mitigate): Grep confirmed `list(self._subscribers)` at `event_bus.py:66` — snapshot copy taken before iteration. Mitigation present exactly as declared.
