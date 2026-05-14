# Walking Skeleton — Podcast Ad Cutter Web API

**Phase:** 1
**Generated:** 2026-05-14

## Capability Proven End-to-End

Running `python main.py --serve` starts an aiohttp server that stays alive and answers `GET /api/v1/health` with a live uptime and version — exercising CLI dispatch → server lifecycle → route handler in one slice, while bare `python main.py` still runs the pipeline once and exits.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| HTTP framework | aiohttp 3.13.5 (already a project dep) | Native async; SSE built-in for later phases; no new dependency (D-05) |
| Server lifecycle | `AppRunner` + `TCPSite`, never `web.run_app()` | `web.run_app()` blocks the loop; AppRunner lets server + pipeline share one asyncio loop (CLAUDE.md hard rule) |
| App construction | Factory function `create_app(event_bus, start_time) -> web.Application` | aiohttp convention; testable with `TestClient` without binding a port (D-08) |
| Event transport | In-process `EventBus` — one `asyncio.Queue` per subscriber, broadcast-all, drop-silently when no subscribers | Decouples pipeline from web layer; single-UI local tool needs no per-type filtering or buffering (D-01–D-04) |
| Layer placement | New `api/` top-level package; `components/`/`utils/` never import HTTP; `EventBus` lives at `api/event_bus.py`, injected into `Pipeline` | Clear layer separation; mirrors `database/`, `components/` package structure (D-05, D-06) |
| Route organization | One file per phase domain under `api/routes/` (`health.py` now; `events.py`, `control.py`, etc. later) | Each phase adds its own route module without touching others (D-07) |
| Dual-mode entry | `main()` dispatches on `args.serve`; `--serve` → `await serve(host, port)`, else existing one-shot pipeline run | Preserves CLI behavior for cron/Docker; each branch independently testable (D-09, D-10) |
| Host/port config | CLI args only — `--host` (default `0.0.0.0`), `--port` (default `8080`) | No `config.yaml` changes in Phase 1 (D-11) |
| Version resolution | `importlib.metadata.version()` with `tomllib` read of `pyproject.toml` as fallback | `pyproject.toml` has no `[build-system]` table, so `importlib.metadata` raises `PackageNotFoundError` today (D-12, RESEARCH Pitfall 1) |
| Error envelope | Success returns the resource directly; errors return `{"error": msg, "detail": {...}}` | Convention locked in Phase 1 so all later phases follow it (D-14) |
| Directory layout | `api/__init__.py`, `api/event_bus.py`, `api/server.py`, `api/routes/__init__.py`, `api/routes/health.py`; tests as flat `tests/test_api_*.py` | Matches existing flat `tests/test_*.py` convention and analog files |

## Stack Touched in Phase 1

- [x] Project scaffold — `api/` package added; existing pytest + ruff + uv tooling reused
- [x] Routing — one real route: `GET /api/v1/health`
- [ ] Database — not touched in Phase 1 (API gets its own read-only WAL connection in a later phase; pipeline DB connection is never shared — CLAUDE.md hard rule)
- [x] UI — N/A (this milestone is API-only; no UI until a later milestone)
- [x] Deployment — documented local full-stack run command: `uv run python main.py --serve` then `curl localhost:8080/api/v1/health`

## Out of Scope (Deferred to Later Slices)

- All pipeline control endpoints (`/api/v1/run`, `/run/stop`, per-feed/per-episode control) — Phase 3
- SSE event streaming (`GET /api/v1/events`) — Phase 2; `EventBus` exists now but emits nothing in Phase 1
- Settings and feed management endpoints — Phase 4
- Database viewer endpoints — Phase 5
- Log access endpoints — Phase 6
- Authentication / API keys (SEC-01) — v2; v1 is trusted local network only
- CORS middleware (INFRA-03) — v2, when a UI milestone begins
- SSE heartbeat, Last-Event-ID replay — v2
- `[build-system]` table in `pyproject.toml` — deferred; `tomllib` fallback covers version resolution today
- Explicit SIGTERM/signal handling for Docker — Phase 6 concern; Phase 1 relies on task cancellation

## Subsequent Slice Plan

Each later phase adds one vertical slice on top of this skeleton without altering its architectural decisions:

- Phase 2: A connected SSE client receives live stage-transition and progress events while the pipeline runs (`EventBus` starts emitting; `api/routes/events.py` added)
- Phase 3: A client can start, stop, and inspect a pipeline run; per-feed and per-episode control (`api/routes/control.py`, `/api/v1/status`)
- Phase 4: All settings and feed config readable/modifiable via API; atomic writes to `config.yaml`
- Phase 5: All database tables exposed as read-only REST endpoints with pagination and filtering
- Phase 6: All log files listable, downloadable, and tailable in real time via SSE
