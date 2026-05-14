# Phase 1: API Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-14
**Phase:** 1-API Foundation
**Areas discussed:** EventBus design, API layer structure, main.py dual-mode entry, Health check response shape

---

## EventBus Design

| Option | Description | Selected |
|--------|-------------|----------|
| Typed event dataclass | `emit(event: PipelineEvent)` with `type` discriminator field; mypy-checkable; easy JSON serialization | ✓ |
| String type + dict payload | `emit('episode.stage', {...})`; flexible but loses type safety | |

**User's choice:** Typed event dataclass

---

| Option | Description | Selected |
|--------|-------------|----------|
| Broadcast-all | Every subscriber queue gets every event; one asyncio.Queue per SSE client | ✓ |
| Filtered by event type | Subscribers register for specific event types; dispatch dict in EventBus | |

**User's choice:** Broadcast-all

---

| Option | Description | Selected |
|--------|-------------|----------|
| Define full enum now | Full PipelineEventType enum with all expected types defined in Phase 1; Phase 2 fills in emit() calls | ✓ |
| Only what Phase 1 needs | Minimal enum in Phase 1; expand in Phase 2 | |

**User's choice:** Define full enum now

---

| Option | Description | Selected |
|--------|-------------|----------|
| Drop silently | emit() is no-op when no subscribers connected; no memory growth | ✓ |
| Small in-memory buffer | Keep last N events for reconnecting clients (EVT-02 — v2 requirement) | |

**User's choice:** Drop silently

---

## API Layer Structure

| Option | Description | Selected |
|--------|-------------|----------|
| New `api/` top-level package | Mirrors `database/`, `components/`; clean HTTP/pipeline separation | ✓ |
| Inside `components/` | Consistent with component pattern but blurs pipeline/API boundary | |
| Flat files at root | Simple for Phase 1 but doesn't scale to 22 endpoints | |

**User's choice:** New `api/` top-level package

---

| Option | Description | Selected |
|--------|-------------|----------|
| Inside `api/` package | `api/event_bus.py`; owned by API layer, injected into Pipeline | ✓ |
| In `utils/` | `utils/event_bus.py`; consistent with other utilities but EventBus isn't a generic utility | |

**User's choice:** `api/event_bus.py`

---

| Option | Description | Selected |
|--------|-------------|----------|
| One file per phase domain | `api/routes/health.py`, `api/routes/events.py`, etc.; each phase adds its own module | ✓ |
| Single routes.py | All handlers in one file; simple now, grows unwieldy | |
| You decide | Leave to planner/executor | |

**User's choice:** One file per phase domain

---

| Option | Description | Selected |
|--------|-------------|----------|
| Factory function `create_app(event_bus)` | aiohttp convention; returns `web.Application`; easy to test with TestClient | ✓ |
| ApiServer class | `start()`/`stop()` methods; more encapsulation; adds ceremony without benefit | |

**User's choice:** Factory function `create_app(event_bus)`

---

## main.py Dual-Mode Entry

| Option | Description | Selected |
|--------|-------------|----------|
| Extract `serve()` coroutine | `main()` dispatches to `serve(config, host, port)` or `run_pipeline(config)`; each branch independently testable | ✓ |
| Inline in main() | All server setup in main() with if/else; harder to test serve mode | |

**User's choice:** Extract `serve()` coroutine

---

| Option | Description | Selected |
|--------|-------------|----------|
| Only on API request | Server starts, idles; pipeline triggered by `POST /api/v1/run` (Phase 3) | ✓ |
| Pipeline runs at startup | Immediately triggers pipeline on serve mode entry | |

**User's choice:** Only on API request

---

| Option | Description | Selected |
|--------|-------------|----------|
| Hardcoded 0.0.0.0:8080, add config later | Minimal Phase 1 scope | |
| Config-driven from day 1 (initially selected) | config.yaml + CLI args | |
| CLI args only (--host, --port) | Extends argparse; defaults 0.0.0.0:8080; no config.yaml changes | ✓ |

**User's choice:** CLI args only (`--host`, `--port`) with defaults `0.0.0.0:8080`
**Notes:** User initially selected "Config-driven from day 1" then reconsidered — went back one step and chose CLI args only to keep Phase 1 scope minimal.

---

## Health Check Response Shape

| Option | Description | Selected |
|--------|-------------|----------|
| `importlib.metadata` | `importlib.metadata.version('podcast-ad-cutter')` — reads from pyproject.toml; zero maintenance | ✓ |
| Hardcoded string | `VERSION = '0.1.0'`; simple but requires manual sync | |

**User's choice:** `importlib.metadata`

---

| Option | Description | Selected |
|--------|-------------|----------|
| `status + uptime_seconds + version` | Minimal; covers INFRA-02 exactly | ✓ |
| `status + uptime_seconds + version + started_at` | Adds ISO 8601 start timestamp; useful for dashboard display | |

**User's choice:** `{"status": "ok", "uptime_seconds": 123.4, "version": "0.1.0"}`

---

| Option | Description | Selected |
|--------|-------------|----------|
| Define standard error envelope now | Success: resource directly. Error: `{"error": "msg", "detail": {...}}`. All phases follow this. | ✓ |
| Let each phase decide | Higher risk of inconsistency by Phase 6 | |

**User's choice:** Define standard error envelope in Phase 1

---

## Claude's Discretion

- Exact `PipelineEvent` dataclass field names and `PipelineEventType` enum member names
- `api/__init__.py` contents (expose `create_app` at package level)
- Whether `serve()` coroutine lives in `main.py` or `api/server.py` (prefer `api/server.py`)

## Deferred Ideas

None — discussion stayed within phase scope.
