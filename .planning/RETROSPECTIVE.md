# Retrospective

## Milestone: v1.0 — Web API

**Shipped:** 2026-05-22
**Phases:** 6 | **Plans:** 13 | **Timeline:** 2026-05-14 → 2026-05-22 (8 days)

### What Was Built

- Dual-mode entry: `--serve` flag starts aiohttp API alongside pipeline; CLI mode unchanged
- SSE progress stream with per-episode stage transitions, counters, and download/encode percentages
- Full pipeline control (start/stop runs, per-feed and per-episode targeting)
- Atomic settings + feed CRUD with Pydantic validation
- Read-only DB viewer (episodes, transcriptions, ads, costs) with pagination and feed filtering
- Log access with byte-range pagination, path-traversal guard, and real-time SSE tail

### What Worked

- **aiohttp throughout**: Already a dep, native async, SSE built-in — zero new framework friction. The same choice held cleanly across all 6 phases.
- **TDD discipline**: Red-green-refactor on every plan produced 100% coverage as a side effect, not a retrofit. Caught several exception-branch gaps before they reached production.
- **Vertical slicing**: Each phase delivered a working end-to-end slice rather than a horizontal layer. Phase 1 → Phase 6 always had a runnable app.
- **AppRunner+TCPSite pattern**: Locked in Phase 1, reused everywhere. Non-blocking server lifecycle never caused issues.
- **`finally` block discipline**: subscriber unregister, file handle close, run_state reset — all exit-path cleanup went in `finally`. Zero resource leak issues across the milestone.

### What Was Inefficient

- **REQUIREMENTS.md checkboxes not updated during execution**: 12/22 boxes unchecked at milestone close despite all phases being complete. The traceability table drifted from reality. Should tick checkboxes as part of each phase's docs step.
- **No milestone audit**: Skipped `/gsd:audit-milestone` before close. Added minor friction to the close process (had to manually verify completeness).
- **STATE.md performance metrics table**: Partially filled throughout the milestone — velocity data is sparse. Metrics tracking could be more consistent.

### Patterns Established

- **409 gate pattern**: Synchronous `run_state.state != "idle"` check before `asyncio.create_task` — correct because asyncio is single-threaded (no TOCTOU)
- **`_run_pipeline_task` wrapper contract**: try/except CancelledError with re-raise, except Exception with logger.exception, finally reset_to_idle — every exit path covered
- **`_validate_path` traversal guard**: `path.is_relative_to(base_dir)` after `(base_dir / filename).resolve()` — blocks both `../` and encoded-slash traversal
- **Atomic config write**: Pydantic validate → write to temp file → `os.replace()` — consistent across settings and feed endpoints
- **Factory pattern for routers**: `create_logs_router(log_dir)`, `create_db_router(db_path)` — dependency injection via factory, testable without server

### Key Lessons

- **`asyncio.Event` must be instantiated inside a running loop**: Module-level instantiation silently breaks in Python 3.10+. Always create asyncio primitives inside coroutines.
- **`CancelledError` must re-raise**: Swallowing it in a task wrapper breaks asyncio's task lifecycle. This is a load-bearing constraint, not a style preference.
- **`is_relative_to()` not just `startswith()`**: URL normalization doesn't decode encoded slashes before path resolution on all platforms. Use `pathlib.Path.is_relative_to()` as the guard.
- **SSE disconnect on client gone**: `ClientConnectionResetError` during final write after disconnect is normal; swallow it at the tail handler level.
- **Read REQUIREMENTS.md documentation in the same commit as implementation**: Documentation debt compounds — 12 unchecked boxes required manual reconciliation at close.

### Cost Observations

- Model mix: Not tracked per-session
- Sessions: ~15 estimated
- Notable: TDD approach front-loaded work into test writing, but paid off with zero rework cycles on any plan. No plan required a second pass.

---

## Cross-Milestone Trends

_Will populate after v1.1+_
