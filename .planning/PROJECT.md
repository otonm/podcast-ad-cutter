# Podcast Ad Cutter — Web API

## What This Is

A REST + SSE web API layer built into the existing podcast ad cutter pipeline, enabling a future web UI to observe and control the app. The API exposes pipeline control commands, real-time progress streaming, settings management, feed CRUD, log access, and read-only database views — all within the same asyncio process as the pipeline.

## Core Value

A web UI can start a run, watch it progress in real time, and inspect every result without touching the filesystem or CLI.

## Requirements

### Validated

<!-- Capabilities that already exist in the codebase. -->

- ✓ Pipeline orchestrates RSS feed download, parse, transcription, ad detection, audio editing, feed publishing — existing
- ✓ Per-episode state machine with SQLite checkpointing (resume on restart without re-work) — existing
- ✓ LLM transcription via LiteLLM/Groq with cost tracking — existing
- ✓ LLM ad detection with confidence scoring; ffmpeg audio editing to cut ads — existing
- ✓ Pydantic-validated YAML config + env-var credentials — existing
- ✓ Per-episode log files + general app log — existing
- ✓ CLI entry point (`main.py`) with argparse — existing
- ✓ Docker deployment support — existing

### Active

<!-- New capabilities targeted by this milestone. -->

- [ ] `--serve` flag starts aiohttp API server in dual mode (bare run still exits after pipeline completes)
- [ ] Pipeline control: trigger full run, stop/cancel running pipeline
- [ ] Per-feed control: trigger or stop a specific feed by slug/ID
- [ ] Per-episode control: skip or re-process an episode (full reset or from a chosen stage)
- [ ] SSE endpoint streams per-episode stage progress (download → transcribe → topic → ad-detect → edit) + run-level counters + download/encode percentage
- [ ] Settings: GET current config, PATCH to update and persist to config.yaml (applies on next run)
- [ ] Feed CRUD: add, remove, update feed entries in config.yaml via API
- [ ] Log access: list all log files; GET full content of any log; SSE tail a log in real time
- [ ] DB viewer: read-only REST endpoints for episodes, transcriptions, ad detections, cost tracking
- [ ] No authentication (trusted local network; add later if needed)

### Out of Scope

- Authentication/authorization — not needed for local network deployment; deferred
- GraphQL or WebSocket — REST + SSE covers all use cases without added complexity
- Live config apply (mid-run) — config changes apply only on next run; too risky to hot-reload
- Web UI itself — this milestone is the API only; UI is a future milestone

## Context

The existing pipeline is a pure CLI tool: `main.py` parses args, loads config, constructs `Pipeline`, calls `Pipeline.run()`, then exits. There is no server mode. The entire app is async (single asyncio event loop), which makes running an aiohttp server alongside the pipeline trivial — both share the same loop.

`aiohttp` is already a project dependency (used for HTTP downloads in `FeedDownloader`), so no new HTTP framework dep is needed. SSE is supported natively by `aiohttp.web`.

Progress events currently have no aggregation point — the pipeline components call component-local methods. A lightweight in-process event bus or progress callback registry will be needed to let the API push SSE events without coupling components to the web layer.

The SQLite database (`data/data.db`) is opened per-run via `async with Database(...)`. In server mode, the API needs read access to the DB between pipeline runs — either a long-lived read connection or a short-lived connection per request.

## Constraints

- **Tech stack**: aiohttp for API server — already a dependency, no new framework
- **Process model**: Same process as pipeline — shared asyncio event loop; no IPC or subprocess management
- **Config isolation**: No component below `Pipeline` imports from `config/`; API must follow the same rule
- **State machine**: Episode re-processing must go through the existing state machine guards, not bypass them — reset DB state, then re-run `_process_episode_until_final`
- **Async throughout**: All API handlers must be async; no blocking calls in the event loop

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| aiohttp as API server | Already a dep; native async; SSE built-in — avoids ASGI stack | — Pending |
| Same-process server | SSE progress trivial without IPC; simplest architecture for a single-user local tool | — Pending |
| Dual mode (`--serve` flag) | Preserves CLI behavior for cron/Docker single-run use cases | — Pending |
| Config changes apply on next run | Prevents partial-run config drift; safe default | — Pending |
| No auth for v1 | Local network deployment only; complexity-free for a first API | — Pending |
| In-process event bus for SSE | Decouples pipeline components from web layer; components emit events, API subscribes | — Pending |

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
*Last updated: 2026-05-14 after initialization*
