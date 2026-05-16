---
phase: "02"
slug: sse-progress-stream
status: verified
threats_open: 0
asvs_level: 1
created: 2026-05-16
---

# Phase 02 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| HTTP (SSE client → server) | External client connects to GET /api/v1/events over TCP | PipelineEvent payloads: guid, feed_slug, stage, outcome, error message, progress percentage |
| In-process (Pipeline → EventBus → SSE route) | asyncio.Queue passing PipelineEvent objects between coroutines in the same process | Same PipelineEvent fields — no serialization across process boundary |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-02-01 | Information Disclosure | pipeline.py | accept | Payloads contain guid, feed_slug, outcome, error text, progress percent — identical to existing log output. No credentials, full file paths, or API keys. | closed |
| T-02-02 | Denial of Service | api/event_bus.py | accept | emit() uses put_nowait() on an unbounded asyncio.Queue; a stalled subscriber can grow the queue without bound. Accepted: single-user local tool with no public network exposure. Deferred per CONTEXT/RESEARCH §8. | closed |
| T-02-03 | Tampering | pipeline.py | mitigate | emit() is in-process only — no external trust boundary crossed. No external input reaches emit call sites. | closed |
| T-02-04 | Repudiation | pipeline.py | mitigate | Every emit() call is paired with the existing logger output; no observability is removed by the event instrumentation. | closed |
| T-02-05 | Denial of Service | api/routes/events.py | accept | Each GET /api/v1/events creates one subscriber queue with unbounded size. A client could open many connections to exhaust memory. Accepted: single-user local tool, no public network exposure per PROJECT.md. | closed |
| T-02-06 | Denial of Service (resource leak) | api/routes/events.py | mitigate | Subscriber queue is unsubscribed in a finally block on disconnect or cancellation (CLAUDE.md mandate). Verified by test_events_route_unsubscribes_on_disconnect. | closed |
| T-02-07 | Information Disclosure | api/routes/events.py | accept | SSE payloads contain the same fields as T-02-01. Route is bound to the same local listener as the rest of the API (localhost-only default per PROJECT.md). | closed |
| T-02-08 | Tampering (cache poisoning) | api/routes/events.py | mitigate | Response sets Cache-Control: no-cache and X-Accel-Buffering: no to prevent intermediate proxy buffering or cached replay. Verified by test_events_route_sets_sse_headers. | closed |
| T-02-09 | Input Validation | api/routes/events.py | mitigate | GET request body is unused. No query parameters are read in Phase 2 (Last-Event-ID replay is EVT-02, deferred). Zero attack surface from request input. | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-02-01 | T-02-02 | Unbounded asyncio.Queue growth under stalled subscriber. Single-user local tool; no public exposure. Deferred to a future phase when the tool becomes multi-user or network-exposed. | oton | 2026-05-16 |
| AR-02-02 | T-02-05 | Unlimited concurrent SSE connections. Same scope constraint as AR-02-01. | oton | 2026-05-16 |
| AR-02-03 | T-02-01, T-02-07 | Event payloads expose operational metadata (guid, feed_slug, error text). Equivalent to existing log output; no new sensitive data surface. Acceptable for a local tool. | oton | 2026-05-16 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-05-16 | 9 | 9 | 0 | gsd-secure-phase (short-circuit: register_authored_at_plan_time=true, threats_open=0) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-05-16
