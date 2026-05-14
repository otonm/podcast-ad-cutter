---
phase: 01-api-foundation
reviewed: 2026-05-14T00:00:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - api/__init__.py
  - api/event_bus.py
  - api/server.py
  - api/routes/__init__.py
  - api/routes/health.py
  - tests/test_api_event_bus.py
  - tests/test_api_health.py
  - tests/test_api_server.py
  - components/pipeline.py
  - tests/test_main.py
  - main.py
findings:
  critical: 1
  warning: 3
  info: 2
  total: 6
status: clean
---

# Phase 01: Code Review Report

**Reviewed:** 2026-05-14
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

This phase implements the aiohttp API foundation: an `EventBus`, a `serve()` lifecycle function using `AppRunner` + `TCPSite`, a `/api/v1/health` endpoint, and a `--serve` CLI flag wired into `main.py`. The overall structure is sound and the CLAUDE.md constraints (no `web.run_app()`, f-strings in log calls, async throughout) are respected. However, one critical bug means the server leaks its `AppRunner` on every real shutdown, two warnings concern missing error guards in `_read_version()` and a misleading test, and two info items flag dead code.

---

## Critical Issues

### CR-01: `runner.cleanup()` is never called on task cancellation — AppRunner leaks on shutdown

**File:** `api/server.py:56-57`

**Issue:** `serve()` awaits `asyncio.Event().wait()` to block indefinitely. When `asyncio.run()` cancels tasks on `KeyboardInterrupt`, `CancelledError` is raised at the `await` on line 56. Because there is no `try/finally`, the `runner.cleanup()` call on line 57 is unreachable. The `AppRunner` (including its socket, SSL context, and any registered cleanup callbacks) is never torn down. This contradicts both the docstring comment ("Block until cancelled — KeyboardInterrupt in main() → asyncio.run cancels all tasks") and the CLAUDE.md requirement for context managers on every resource.

The unit test `test_serve_cleans_up_runner` does not expose this bug because it mocks `asyncio.Event().wait` to return immediately (normal return, not cancellation), so cleanup is reached in tests but not in production.

**Fix:**
```python
async def serve(host: str, port: int) -> None:
    start_time = time.monotonic()
    event_bus = EventBus()
    app = create_app(event_bus, start_time)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"API server listening on {host}:{port}")
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
```

---

## Warnings

### WR-01: `_read_version()` tomllib fallback has no error handling — health endpoint raises 500 on missing/malformed pyproject.toml

**File:** `api/routes/health.py:26-29`

**Issue:** When the package is not installed (i.e., `PackageNotFoundError` is caught), `_read_version()` falls back to reading `pyproject.toml` directly. This path has no error handling. If `pyproject.toml` does not exist (e.g., container image built without source tree), `FileNotFoundError` propagates uncaught through the aiohttp request handler and becomes a 500 response. If the file exists but lacks the `project.version` key, a `KeyError` propagates similarly. The health endpoint should never return 500 due to a missing version string.

Additionally, `_read_version()` is called on every request rather than being cached at module load time, which means the filesystem fallback (if triggered) performs a disk read on each health check.

**Fix:**
```python
def _read_version() -> str:
    try:
        return importlib.metadata.version("podcast-ad-cutter")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        return str(data["project"]["version"])
    except (FileNotFoundError, KeyError):
        return "unknown"
```

Optionally cache the result at import time by calling `_read_version()` once at module level and referencing the cached value in the handler.

---

### WR-02: `test_serve_flag_calls_serve_and_not_pipeline` double-patches `main.serve` — outer patch setup is dead code that masks assertion intent

**File:** `tests/test_main.py:340-363`

**Issue:** The test uses a `with patch("main.serve") as mock_serve` outer context manager, then immediately re-patches `main.serve` with a different `AsyncMock` (`mock_serve_coro2`) in an inner `with patch(...)` block. The outer `mock_serve` setup (lines 354-357: setting `.return_value`, `.side_effect` twice) is entirely overridden by the inner patch and never has any effect. The final assertion (`mock_serve_coro2.assert_awaited_once()`) only verifies the inner mock. This means:
- The outer mock and its configuration lines are dead code.
- The test does not verify that `serve` was called with the correct `host` and `port` arguments (no `assert_awaited_once_with`).

**Fix:** Remove the outer patch for `main.serve` and its setup lines; patch only with `mock_serve_coro2` directly. Add argument verification:
```python
async def test_serve_flag_calls_serve_and_not_pipeline(
    self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "argv", ["main.py", "--serve"])
    mock_cfg = MagicMock()
    mock_cfg.app.log.level = "INFO"
    mock_cfg.app.log.to_file = False
    mock_cfg.app.paths.log_dir = tmp_path
    mock_serve = AsyncMock()
    with (
        patch("main.load_config", return_value=mock_cfg),
        patch("main.configure_logging"),
        patch("main.serve", mock_serve),
        patch("main.Pipeline") as mock_pipeline_cls,
    ):
        await main()
    mock_serve.assert_awaited_once_with("0.0.0.0", 8080)
    mock_pipeline_cls.assert_not_called()
```

---

### WR-03: `serve()` does not guard against startup failure — `runner.cleanup()` is not called if `site.start()` raises

**File:** `api/server.py:51-53`

**Issue:** If `site.start()` raises (e.g., `OSError: [Errno 98] Address already in use`), the `runner` has already been set up via `runner.setup()` but `runner.cleanup()` is never invoked. This leaves the `AppRunner` in an inconsistent state. The fix for CR-01 (wrapping in `try/finally`) resolves this as well since `runner.cleanup()` would be placed in the `finally` block that covers both `site.start()` and `asyncio.Event().wait()`.

**Fix:** The `try/finally` structure from CR-01's fix covers this case — no additional change needed beyond that fix.

---

## Info

### IN-01: `Pipeline._event_bus` is assigned but never read — dead assignment

**File:** `components/pipeline.py:102`

**Issue:** `self._event_bus = event_bus` stores the injected `EventBus` instance, but no method in `Pipeline` ever calls `self._event_bus.emit(...)`. The field is dead code in this phase. This is not itself a bug, but it creates a false impression that event emission is wired up when it is not, and it means tests cannot verify that the bus receives events from the pipeline.

**Fix:** Either add a comment clarifying this is stubbed for a future phase:
```python
# Stored for Phase 2 — pipeline event emission not yet wired.
self._event_bus = event_bus
```
Or defer the parameter and field entirely until the phase that implements `emit()` calls.

---

### IN-02: `main.py` imports `Pipeline` and `serve` at module level — `serve` import is unconditional even when only running the pipeline

**File:** `main.py:12-13`

**Issue:** `from api.server import serve` is imported unconditionally at the top of `main.py`. This means importing `main` always imports `aiohttp.web` and the full API stack. This is a minor coupling concern now but will grow as the API layer expands. It is not a bug.

**Fix:** Consider a lazy import inside the `if args.serve:` branch, or keep as-is and accept the coupling (acceptable for this project size). No action strictly required.

---

_Reviewed: 2026-05-14_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
