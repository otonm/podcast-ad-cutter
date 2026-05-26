---
slug: ctrl-c-invalidstateerror-shutd
status: fixing
trigger: "ctrl+c when running the server causes KeyboardInterrupt/CancelledError/InvalidStateError"
created: 2026-05-26
updated: 2026-05-26
---

# Debug Session: ctrl-c-invalidstateerror-shutd

## Symptoms

- **Expected:** Clean shutdown when Ctrl-C pressed
- **Actual:** `asyncio.exceptions.InvalidStateError: invalid state` printed to stderr; noisy shutdown
- **Error:** `web_protocol.py:546 self._handler_waiter.set_result(None)` → `InvalidStateError`
- **Trigger:** Active SSE tail connection (`/api/v1/logs/{name}/tail`) when Ctrl-C pressed
- **Confirmed:** User pressed Ctrl-C twice (`^C^C` visible in output)

## Current Focus

- **hypothesis:** Double Ctrl-C causes Python 3.12 `asyncio.Runner._on_sigint` to raise `KeyboardInterrupt()` directly inside `asyncio.run()`, bypassing `serve()`'s `finally: await runner.cleanup()`. Without proper aiohttp shutdown, the `tail_log` task is abruptly cancelled by asyncio's post-exit cleanup, and aiohttp's `_handler_waiter.set_result(None)` is called on an already-cancelled future.
- **root_cause:** `asyncio.Runner._on_sigint` first Ctrl-C cancels main task; second Ctrl-C raises `KeyboardInterrupt()` which exits `run_forever()` without running `serve()` finally block. `runner.cleanup()` is never called.
- **fix:** Install `loop.add_signal_handler(SIGINT/SIGTERM, shutdown.set)` in `serve()`, replacing asyncio's default handler. Both Ctrl-C presses call `shutdown.set()` (no-op on second), no `KeyboardInterrupt` raised, `runner.cleanup()` always runs.
- **next_action:** Apply fix to api/server.py — write failing test, implement signal handling, verify

## Evidence

- Traceback at `asyncio/runners.py:157` shows `raise KeyboardInterrupt()` — this is the SECOND Ctrl-C path in `_on_sigint`
- `^C^C` visible in server output confirms double Ctrl-C
- Error in `web_protocol.py:546` confirms `_handler_waiter` was cancelled before `set_result(None)` was called
- `serve()` has `finally: await runner.cleanup()` which IS correct but NOT reached when second Ctrl-C aborts `asyncio.run()`
- `_run_tail_stream` correctly propagates `CancelledError` via `finally: fh.close()` — not the bug source

## Resolution

- **root_cause:** Double Ctrl-C bypasses serve() finally block; runner.cleanup() never called; aiohttp handler tasks abruptly cancelled; _handler_waiter in terminal state when set_result called
- **fix:** loop.add_signal_handler() in serve() — graceful shutdown on both Ctrl-C presses
- **files_changed:** api/server.py, tests/test_api_server.py
