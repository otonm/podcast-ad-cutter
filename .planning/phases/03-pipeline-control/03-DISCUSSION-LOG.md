# Phase 3: Pipeline Control - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-16
**Phase:** 3-pipeline-control
**Areas discussed:** Stop signal, Concurrent run scope, Status endpoint depth, Episode control safety, Run trigger response, Partial stage reset

---

## Stop Signal

| Option | Description | Selected |
|--------|-------------|----------|
| Cancel immediately | asyncio.Task.cancel() — CancelledError propagates; stage incomplete, retried on next run | |
| Finish current episode, then stop | Set a stop flag; pipeline checks after each episode | ✓ (default) |
| Finish current stage, then stop | Flag checked after each DB write (guard level) | |
| Force option | ?force=true query param for immediate cancel | ✓ (additive) |

**User's choice:** Graceful stop (finish current episode) by default. Add `?force=true` query param to POST /api/v1/run/stop for immediate Task.cancel().
**Notes:** Two-mode stop: default = graceful, force = immediate cancel. Query param chosen over separate endpoint or request body.

---

## Concurrent Run Scope

| Option | Description | Selected |
|--------|-------------|----------|
| 409 — one run at a time | Both full-run and per-feed-run return 409 if any run is active | ✓ |
| Allow parallel per-feed task | Separate Pipeline instance; two concurrent DB connections in WAL mode | |

**User's choice:** Single run at a time — strict 409 gate across all trigger endpoints.
**Notes:** Concurrent runs would race on DB writes; simplest and safest for a single-user tool.

---

## Status Endpoint Depth

| Option | Description | Selected |
|--------|-------------|----------|
| Required minimum only | idle/running + active feed slug + per-feed counts + run start time | |
| Include current episode GUID | Add GUID of episode actively being processed | ✓ |
| Include remaining count explicitly | Add episodes_remaining field | (revisited → no) |
| Derivable remaining | Client computes total - done - failed | ✓ |

**User's choice:** Include current_episode_guid. Per-feed counts (total/done/failed) included; remaining is derivable client-side and not added explicitly.
**Notes:** User initially selected "include remaining count" then revisited and agreed it's derivable. Status includes a third state: "stopping" for graceful stop in-progress.

---

## Episode Control Safety

| Option | Description | Selected |
|--------|-------------|----------|
| 409 while any run is active | Reject CTRL-04/CTRL-05 if pipeline is running | ✓ |
| Allow but warn | Accept with warning field in response | |
| Allow — optimistic | Last-write-wins; pipeline re-checks on next loop | |

**User's choice:** 409 while any run is active.
**Notes:** CTRL-04 full reset → reset to 'pending' (restart from scratch). Stage param accepts all 5 stages (download, transcribe, topic, ad-detect, edit); invalid stage → 422.

---

## Run Trigger Response

| Option | Description | Selected |
|--------|-------------|----------|
| 202 Accepted | Run starts as background task; 202 signals "accepted, processing in background" | ✓ |
| 200 OK | Less precise — implies operation completed | |

**User's choice:** 202 Accepted with body `{"status": "started", "started_at": "..."}`.

---

## Partial Stage Reset

| Option | Description | Selected |
|--------|-------------|----------|
| All 5 stages | download, transcribe, topic, ad-detect, edit | ✓ |
| ML stages only | transcribe, topic, ad-detect | |

**User's choice:** All 5 stages are valid targets for the optional stage param on CTRL-04.

---

## Claude's Discretion

- Shared state object shape (`dataclass` or `TypedDict` stored in `app["run_state"]`)
- Route file name (`api/routes/control.py`) and factory function signature
- Whether to use `asyncio.Event` or a simple boolean flag for graceful stop signal

## Deferred Ideas

None — discussion stayed within phase scope.
