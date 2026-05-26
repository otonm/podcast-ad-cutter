# Podcast Ad Cutter

## What This Is

An async Python pipeline that downloads podcast RSS feeds, transcribes episodes via LLM (Groq/LiteLLM), detects ad segments with confidence scoring, cuts audio via ffmpeg, and publishes cleaned feeds — with a full REST + SSE web API layer enabling remote observation and control without filesystem or CLI access.

## Core Value

A web UI can start a run, watch it progress in real time, and inspect every result without touching the filesystem or CLI.

## Requirements

### Validated

<!-- Capabilities proven in production / shipped milestones -->

- ✓ Pipeline orchestrates RSS feed download, parse, transcription, ad detection, audio editing, feed publishing — existing pre-v1.0
- ✓ Per-episode state machine with SQLite checkpointing (resume on restart without re-work) — existing pre-v1.0
- ✓ LLM transcription via LiteLLM/Groq with cost tracking — existing pre-v1.0
- ✓ LLM ad detection with confidence scoring; ffmpeg audio editing to cut ads — existing pre-v1.0
- ✓ Pydantic-validated YAML config + env-var credentials — existing pre-v1.0
- ✓ Per-episode log files + general app log — existing pre-v1.0
- ✓ CLI entry point (`main.py`) with argparse — existing pre-v1.0
- ✓ Docker deployment support — existing pre-v1.0
- ✓ `--serve` flag starts aiohttp API server in dual mode (bare run still exits after pipeline completes) — v1.0
- ✓ SSE endpoint streams per-episode stage progress (download → transcribe → topic → ad-detect → edit) + run-level counters + download/encode percentage — v1.0
- ✓ Pipeline control: trigger full run, stop/cancel running pipeline, per-feed targeting — v1.0
- ✓ Episode skip/reprocess endpoints with DB state reset and cascade delete — v1.0
- ✓ Settings: GET current config (credentials redacted), PATCH to update and persist atomically (applies on next run) — v1.0
- ✓ Feed CRUD: add, remove, update feed entries in config.yaml via API — v1.0
- ✓ DB viewer: read-only REST endpoints for episodes, transcriptions, ad detections, cost tracking — v1.0
- ✓ Log access: list all log files; GET full content with byte-range pagination; SSE tail in real time — v1.0
- ✓ Path-traversal guard on all log endpoints — v1.0
- ✓ No authentication (trusted local network; add later if needed) — v1.0

### Active

<!-- New capabilities targeted by next milestone -->

_Planning next milestone — requirements to be defined via /gsd:new-milestone_

### Out of Scope

- Authentication/authorization — deferred to SEC-01 in v2 (local network deployment only)
- GraphQL or WebSocket — REST + SSE covers all use cases without added complexity
- Live config apply (mid-run) — config changes apply only on next run; too risky to hot-reload
- Web UI itself — this was the API milestone; UI is a future milestone
- EVT-02: Last-Event-ID SSE replay — v2
- EVT-03: SSE heartbeat — v2
- DB-05: Topics endpoint — v2
- INFRA-03: CORS middleware — v2 (until UI and API are on different origins)

## Context

**Current state (v1.0):** Complete REST + SSE web API over the existing pipeline. 22 endpoints across 6 route groups (health, events, status/control, settings, feeds, db viewer, logs). All HTTP concerns isolated in `api/` package. ~65K LOC Python.

**Tech stack:** Python 3.12, aiohttp (server + HTTP client), aiosqlite, Pydantic, LiteLLM, ffmpeg. Single asyncio process — pipeline and API share the same event loop.

**Deployment:** Docker or bare Python. `python main.py` for single pipeline run; `python main.py --serve` for persistent API server mode.

The existing pipeline was a pure CLI tool before v1.0. The API was added without breaking any CLI behavior — all existing call sites unchanged.

## Constraints

- **Tech stack**: aiohttp for API server — already a dependency, no new framework
- **Process model**: Same process as pipeline — shared asyncio event loop; no IPC or subprocess management
- **Config isolation**: No component below `Pipeline` imports from `config/`; API must follow the same rule
- **State machine**: Episode re-processing must go through the existing state machine guards, not bypass them — reset DB state, then re-run `_process_episode_until_final`
- **Async throughout**: All API handlers must be async; no blocking calls in the event loop
- **Never `web.run_app()`**: Always use `AppRunner` + `TCPSite` (non-blocking lifecycle)
- **Config writes atomic**: Validate through Pydantic first, write to temp file, use `os.replace()` for the swap
- **SSE disconnect**: Always unregister subscriber queue in `finally` block
- **Never share aiosqlite connection**: Pipeline and API read handlers use separate connections; API uses WAL mode read-only connections

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| aiohttp as API server | Already a dep; native async; SSE built-in — avoids ASGI stack | ✓ Confirmed — no friction across 6 phases |
| Same-process server | SSE progress trivial without IPC; simplest architecture for a single-user local tool | ✓ Confirmed — shared event loop worked throughout |
| Dual mode (`--serve` flag) | Preserves CLI behavior for cron/Docker single-run use cases | ✓ Confirmed — `AppRunner`+`TCPSite` (non-blocking) used |
| Config changes apply on next run | Prevents partial-run config drift; safe default | ✓ Confirmed — atomic write via temp file + `os.replace()` |
| No auth for v1 | Local network deployment only; complexity-free for a first API | ✓ Confirmed — deferred to SEC-01 in v2 |
| In-process event bus for SSE | Decouples pipeline components from web layer; components emit events, API subscribes | ✓ Confirmed — broadcast-all model with list() snapshot |
| Dedicated read-only DB connection for API | Separate from pipeline connection; WAL mode enables concurrent reads | ✓ Confirmed — aiosqlite WAL + read-only per-request pattern |
| `_validate_path` traversal guard | `is_relative_to()` blocks encoded-slash traversal that URL normalisation doesn't catch | ✓ Confirmed — used in both read_log and tail_log |
| SSE file tail with open handle + finally | Keep file handle across poll cycles; `finally: fh.close()` prevents leak on disconnect | ✓ Confirmed — rotation detection via st_size shrink also implemented |
| asyncio.Event in serve() not module-level | asyncio.Event requires a running loop; module-level instantiation breaks before loop starts | ✓ Confirmed — instantiated inside async def serve() |
| CancelledError must re-raise in wrapper | Swallowed CancelledError breaks asyncio task lifecycle | ✓ Confirmed — load-bearing in _run_pipeline_task |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-26 after v1.0 milestone complete — all 6 phases shipped*
