# Domain Pitfalls

**Project:** Podcast Ad Cutter — aiohttp REST + SSE API layer
**Domain:** Async Python pipeline + co-located web server (same asyncio event loop)
**Researched:** 2026-05-14

---

## Summary

Seven distinct pitfall categories apply to this project. Three are critical — they cause silent data corruption, server deadlocks, or unrecoverable process state that manifests only under load or at shutdown. Four are moderate — they degrade reliability or developer experience without causing data loss.

The single most dangerous pitfall is **sharing the aiosqlite connection between the pipeline and API read handlers**. aiosqlite serialises all operations through a single background thread; interleaving API reads mid-write transaction produces "database is locked" errors at best and silent partial reads at worst. The second most dangerous is **ffmpeg stdout/stderr pipe deadlock** — a missed read on either pipe stalls the subprocess and freezes the event loop coroutine waiting for it, blocking all web request handling. Every other pitfall is resolvable with a clear pattern applied at the right phase.

---

## Critical Pitfalls

### Pitfall C-1: aiosqlite Connection Shared Between Pipeline and API

**What goes wrong:**
The current architecture opens exactly one `Database` context manager per `Pipeline.run()` call and passes its single aiosqlite connection to all six `*Store` classes. If the API layer reads from the same connection while the pipeline is mid-write transaction, aiosqlite's internal thread serialises the operations — but SQLite itself can return `SQLITE_BUSY` ("database is locked") for any read that races a write under the default journal mode. Even with WAL mode, a long-running pipeline write transaction that hasn't committed blocks all reads that arrive during it.

There is a documented aiosqlite bug (issue #251) where `SQLITE_BUSY` is returned immediately even with `timeout` set, in high-concurrency insert/delete/select scenarios. This means "database is locked" can surface before the configured timeout expires.

**Why it happens:**
aiosqlite uses one background thread per connection. All requests to that connection queue through a single `asyncio.Queue`. If the pipeline holds a write lock and the API issues a read on the same connection object, the read blocks that queue. Worse, if the pipeline uses an implicit transaction (the default in aiosqlite), the connection may hold a write lock for the entire duration of a multi-step `_process_episode_until_final` call — potentially minutes for a transcription + ad-detection stage.

**Consequences:**
- API read endpoints return 500 errors or stall during pipeline runs.
- At high interleaving frequency the `busy_timeout` is exhausted and the API fails.
- Silent wrong reads: a `SELECT` that starts between two related `INSERT`s (e.g., episode row inserted before ad_segments rows) can return partial data.

**Warning signs:**
- `OperationalError: database is locked` in API handler logs during a pipeline run.
- API response latency spikes to match pipeline stage duration (minutes).
- DB reads succeed reliably when no pipeline run is active, fail inconsistently when one is running.

**Prevention:**
1. Enable WAL mode at DB initialisation: `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=10000`. WAL mode allows unlimited concurrent readers while a single writer is active. This alone handles most read-under-write scenarios.
2. Open a **separate read-only aiosqlite connection** for API handlers — never share the pipeline's connection object. The API opens `aiosqlite.connect(db_path)` per request (or keeps a long-lived read connection) independently of `Database`.
3. For the read-only API connection, set `PRAGMA query_only=ON` to prevent accidental writes from API handlers.
4. Keep pipeline write transactions as short as possible: commit after each stage guard, not at the end of the full episode processing loop.

**Phase:** Must be addressed in the phase that introduces the DB viewer endpoints and the SSE progress endpoint (any phase that opens a DB connection from an API handler).

---

### Pitfall C-2: ffmpeg Subprocess Pipe Deadlock Freezes the Event Loop

**What goes wrong:**
`asyncio.create_subprocess_exec` with `stdout=PIPE` and `stderr=PIPE` creates OS-level pipes. If the subprocess writes enough output to fill the pipe buffer (typically 64 KB on Linux) before the coroutine reads from it, the subprocess blocks on `write()`. The event loop coroutine waiting on the subprocess never resumes because the subprocess is blocked waiting for the pipe to drain. The event loop is not frozen globally — other tasks can run — but the pipeline coroutine processing that episode is stuck. Since the pipeline holds the DB connection's write lock during audio editing stages, this also blocks any API reads that hit the same connection (see C-1).

**Why it happens:**
ffmpeg writes extensive progress output to stderr. On long encodes, stderr output can easily exceed 64 KB. The existing `utils/ffmpeg.py` uses `asyncio.create_subprocess_exec` — the question is whether it reads both `stdout` and `stderr` concurrently. If `communicate()` is used it handles this, but if the code reads one pipe then awaits the other, the unread pipe can deadlock.

**Consequences:**
- Episode processing hangs indefinitely with no timeout.
- The pipeline run never completes; the process must be killed manually.
- Concurrent API requests that share the DB connection stall for the duration.

**Warning signs:**
- Pipeline run stalls at "editing" stage with no log output from ffmpeg for minutes.
- `ps aux` shows the ffmpeg process in state `S` (sleeping) — it is blocked on a write.
- Memory usage of the aiohttp process is flat (no leak) but CPU is near-zero — the coroutine is awaiting a never-resolving future.

**Prevention:**
- Concurrently drain both `stdout` and `stderr` using `asyncio.create_task`. Never await one pipe then the other sequentially.
- Alternatively, use `process.communicate()` which handles concurrent pipe draining internally. However, `communicate()` buffers all output in memory — acceptable for stderr but avoid for large stdout.
- Add a hard timeout on ffmpeg calls via `asyncio.wait_for(process.wait(), timeout=N)` combined with `process.kill()` on timeout.
- Audit `utils/ffmpeg.py` before building the API — verify both pipes are drained concurrently.

**Phase:** Audit in the first phase (before any web server code). A pre-existing pipe deadlock will become much harder to diagnose once the server is co-located.

---

### Pitfall C-3: SSE Client Disconnect Leaks asyncio.Queue and Blocks Progress Events

**What goes wrong:**
The planned in-process event bus will distribute progress events to each connected SSE client via a per-connection `asyncio.Queue`. If a client disconnects abruptly (browser tab closed, network drop) without the server detecting it, the handler coroutine continues `await queue.get()` forever, the queue is never removed from the subscriber registry, and every future event is enqueued into it. Over many connections, memory grows without bound.

More subtly: when writing to an SSE response after client disconnect, aiohttp raises `ConnectionResetError` or `OSError`. If the handler does not catch these on `resp.write()`, the exception propagates out of the handler task and the queue is never unregistered.

**Why it happens:**
HTTP/1.1 does not have a server-push notification that the client has disconnected — the server only discovers this when it next writes to the socket. For long-lived SSE streams, this can be delayed until the next event is sent. `aiohttp` documents that after the peer is gone, reading or writing raises `OSError` or a subclass like `ConnectionResetError`. The `aiohttp-sse` library exposes `resp.is_connected()` to check this, but polling intervals matter.

A known related issue: aiohttp SSE connections can silently time out after 5 minutes if the client uses default `ClientSession` timeout settings. In a browser context, browsers automatically reconnect SSE, but a stale server-side handler and queue may persist for those 5 minutes.

**Consequences:**
- Memory grows with each disconnected client over a long server run.
- The event bus accumulates dead subscribers; event delivery loops iterate over stale entries.
- In the worst case, `asyncio.Queue` maxsize is unbounded — if events are produced faster than a dead subscriber's queue is GC'd, memory spikes.

**Warning signs:**
- RSS memory of the process grows monotonically after many browser refreshes of the progress page.
- `len(subscriber_registry)` (if instrumented) never decreases.
- Log shows SSE handler tasks that are not awaited and never complete.

**Prevention:**
1. Wrap every SSE `resp.write()` in `try/except (ConnectionResetError, OSError)` and on exception: unregister the queue, break out of the send loop.
2. Use `asyncio.Queue(maxsize=N)` with a bounded size (e.g., 100 events). On `QueueFull`, treat the subscriber as dead and unregister it.
3. Register and unregister subscribers in a `try/finally` block so disconnect-via-exception always cleans up.
4. Send a periodic heartbeat (comment-only SSE event `": heartbeat\n\n"`) every 15–30 seconds. A failed write on the heartbeat detects disconnects promptly rather than waiting for the next real event.
5. Set `resp.enable_chunked_encoding()` and send keep-alive headers to prevent intermediate proxies from killing idle connections.

**Phase:** Must be addressed in the phase that implements the SSE progress endpoint and event bus.

---

### Pitfall C-4: Config YAML Partial Write Corrupts State on PATCH

**What goes wrong:**
A PATCH to update `config.yaml` that writes non-atomically (open → truncate → write → close) leaves a window where a concurrent pipeline run reading the same file sees a truncated or partial YAML document. `pydantic` validation will raise `ConfigError`, crashing the pipeline. If the write also crashes mid-way (disk full, process kill), the config file is corrupted permanently.

**Why it happens:**
Python's default `open(path, 'w')` truncates the file before writing. If the write is interrupted, the file is empty or partial. The existing codebase already handles this for RSS output (`FeedPublisher` uses temp file + `replace`), but the config write path for the new PATCH endpoint does not yet exist and will be written from scratch — this is where the pattern is most likely to be omitted.

**Consequences:**
- Config file lost; operator must restore from backup or recreate.
- If a pipeline run loads the partial config, it crashes at startup validation rather than at the problematic config key — making root cause diagnosis harder.

**Warning signs:**
- Config file is 0 bytes or contains only the first N lines of YAML.
- `ConfigError: YAML parse error` on next pipeline start after a PATCH.

**Prevention:**
- Write PATCH changes to a temp file in the same directory (same filesystem → `os.replace()` is atomic on POSIX), then atomically replace: `tmp.write_text(yaml_content); tmp.replace(config_path)`.
- Parse and validate the updated config through `AppConfig` **before** writing the temp file. Reject the PATCH with HTTP 422 if validation fails.
- Never hold a file lock on the config during a pipeline run — the pipeline loads config once at startup and holds it in memory. Atomic replace means the running pipeline is unaffected; the new config takes effect on next run.

**Phase:** Must be addressed in the phase that implements the Settings PATCH endpoint.

---

## Moderate Pitfalls

### Pitfall M-1: Pipeline Run Concurrency — File and State Conflicts

**What goes wrong:**
If the API allows triggering a second pipeline run while one is already running (either for the same feed or globally), two concurrent runs will:
- Write to the same output directory (`output/<slug>/`), with one run potentially overwriting or deleting files the other is still processing.
- Open the same aiosqlite connection via `async with Database(...)` — since the current architecture opens exactly one connection per `Pipeline.run()`, a second run creates a second connection. With default journal mode, the second writer gets `SQLITE_BUSY`. With WAL mode, both writers compete and one will retry or fail.
- Corrupt the in-memory `_Stores.transcribed_guids` / `ad_detected_guids` sets: each run loads them once; a GUID written by run A may not appear in run B's already-loaded set, causing re-processing.

**Warning signs:**
- Two pipeline runs log the same episode being processed simultaneously.
- `FileExistsError` or silent file overwrites in the output directory.
- Duplicate DB rows for the same GUID (violates UNIQUE constraints → `IntegrityError`).

**Prevention:**
- Use an `asyncio.Lock` or a simple boolean flag to enforce at-most-one-run-globally at the API layer. Reject a second trigger with HTTP 409 Conflict if a run is active.
- Alternatively, queue run requests and process them serially.
- Do not attempt to support truly concurrent runs in the first API version — the architecture (single DB connection, shared output dir, in-memory GUID sets) is not designed for it.

**Phase:** Must be addressed in the phase that implements pipeline control endpoints (trigger/stop).

---

### Pitfall M-2: LiteLLM Internal Sync Operations Blocking the Event Loop

**What goes wrong:**
LiteLLM's `acompletion()` is documented as async, but several known issues reveal that parts of its internal implementation are not truly non-blocking. Specifically: (1) its `LoggingWorker` uses `asyncio.Queue` bound to the event loop at import time — in long-running server mode with a persistent event loop, this is generally fine, but LiteLLM bugs have caused `Queue is bound to a different event loop` errors in certain configurations; (2) `litellm.get_model_info()` is synchronous and performs file I/O on its internal model database — this is called within `AdDetector._get_context_window()` on the hot path; (3) internal mock/delay paths use `time.sleep()` rather than `asyncio.sleep()` (confirmed in litellm discussion #9852).

In server mode with `aiohttp` handling concurrent requests, any synchronous blocking call in the event loop starves all other coroutines for its duration. A `time.sleep(0.1)` in LiteLLM's mock path during development testing will block the web server for 100 ms.

**Warning signs:**
- Web request latency spikes align exactly with LiteLLM call durations.
- `asyncio` debug mode (`PYTHONASYNCIODEBUG=1`) reports coroutines that held the event loop for > 0.1s — these will point to LiteLLM internals.
- In testing, `litellm.get_model_info()` blocking shows up as a synchronous disk read in profiling.

**Prevention:**
- Wrap `litellm.get_model_info()` calls in `asyncio.get_event_loop().run_in_executor(None, ...)` if profiling confirms blocking.
- Enable asyncio debug mode during development: `asyncio.get_event_loop().set_debug(True)` — it logs any callback that blocks > 100 ms.
- Pin LiteLLM to a specific minor version and review the changelog before upgrades (it updates very frequently — per CONCERNS.md, the dep has no upper bound).
- In tests, use `litellm.mock_response` carefully; confirm the mock path uses `asyncio.sleep` not `time.sleep`.

**Phase:** Audit in the phase that introduces the web server. Add asyncio debug mode to the dev-run config.

---

### Pitfall M-3: Process Shutdown Ordering — Pipeline Not Cancelled Before DB Closes

**What goes wrong:**
aiohttp's `run_app()` shutdown sequence is: stop new connections → call `on_shutdown` → wait for handlers → call `on_cleanup`. If the pipeline run is modelled as an `asyncio.Task` created via a `cleanup_ctx` (the correct aiohttp pattern for background tasks), its cancellation happens inside the cleanup context function. However, there is a known aiohttp issue (#5672, #3593): if a cleanup context function's task cancellation raises `CancelledError` and this is not properly suppressed with `contextlib.suppress`, the cleanup chain aborts — subsequent contexts (e.g., DB close) are never executed, leaving the aiosqlite connection unclosed and the WAL file in an uncommitted state.

Additionally, if the pipeline task is cancelled mid-write (e.g., mid-INSERT), aiosqlite's background thread may have already submitted the SQL to SQLite but not yet committed. The `CancelledError` interrupts the `await` but SQLite's internal state is intact — the write is simply rolled back. This is safe, but operators need to understand the episode will be re-processed on next start (the state machine handles this via checkpoint logic).

**Warning signs:**
- After `Ctrl+C`, the DB file has a `-wal` companion that is not merged (WAL checkpoint not run).
- Log shows "cleanup context aborted" or subsequent `on_cleanup` signals not fired.
- Next run after forced shutdown re-processes episodes that appeared complete in the previous run's logs.

**Prevention:**
- Model the pipeline task in a `cleanup_ctx` generator, cancelling it in the `finally` block with `contextlib.suppress(asyncio.CancelledError)`:
  ```python
  async def pipeline_ctx(app):
      task = asyncio.create_task(run_pipeline())
      yield
      task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
          await task
  ```
- Register `on_shutdown` signals to set a cancellation event that the pipeline's main loop checks, allowing a softer stop (finish current episode, then exit) before hard task cancellation.
- Run `PRAGMA wal_checkpoint(FULL)` before closing the DB connection in the cleanup context.
- Test shutdown under Python 3.11+ where `CancelledError` is `BaseException`, not `Exception` — broad `except Exception` in the pipeline will **not** catch it, which is correct, but any cleanup in those except blocks will be skipped.

**Phase:** Must be addressed in the phase that introduces `--serve` mode and the aiohttp server scaffolding.

---

### Pitfall M-4: Testing SSE Endpoints — Event Loop and Streaming Confusion

**What goes wrong:**
SSE tests fail in two distinct ways that are easy to confuse:

1. **Event loop isolation:** `pytest-asyncio` creates a fresh event loop per test by default. LiteLLM's internal `LoggingWorker` binds to the event loop at first import. On subsequent tests in the same session, LiteLLM tries to use the previous event loop's `Queue`, causing `RuntimeError: Queue is bound to a different event loop` (confirmed in litellm issue #14521). This is a known LiteLLM bug that appears specifically in parametrised async test suites.

2. **SSE stream collection:** The `aiohttp` test client's `resp.content.read()` waits for the connection to close. SSE connections do not close until the server decides to. A test that calls `client.get('/events')` and then `await resp.read()` will hang forever if the SSE handler is an infinite loop. SSE tests must read N events then explicitly close the response or use a timeout.

**Warning signs:**
- Test suite passes in isolation but fails with `RuntimeError: Queue is bound to a different event loop` when multiple async tests run sequentially.
- SSE endpoint tests hang indefinitely with no assertion failure.
- Coverage drops because SSE handler branches are only exercised when the connection is closed by the client — normal test flow never reaches them.

**Prevention:**
- Configure `pytest-asyncio` with `asyncio_mode = "auto"` and `loop_scope = "session"` in `pyproject.toml` — a single event loop per session avoids LiteLLM's per-import binding issue.
- For SSE endpoint tests: use `async with client.get('/events') as resp:` and read events line-by-line from `resp.content`. Set `asyncio.wait_for(..., timeout=5)` around the read. Close the response explicitly after collecting the expected events.
- Test the event bus and progress emission separately from the SSE transport: unit-test that events are enqueued correctly; integration-test that the SSE handler reads from a queue and writes the correct bytes.
- Mock the pipeline entirely in SSE tests — inject events directly into the queue rather than triggering a real pipeline run.

**Phase:** Must be addressed in every phase that introduces new SSE or API endpoints. Establish the test patterns in the first phase and document them.

---

## Phase Mapping

| Phase Topic | Pitfall | Priority | Mitigation Approach |
|-------------|---------|----------|---------------------|
| Server scaffolding (`--serve` flag, aiohttp startup) | M-3: Shutdown ordering | High | `cleanup_ctx` pattern + `contextlib.suppress(CancelledError)` + WAL checkpoint |
| Server scaffolding | M-2: LiteLLM blocking | Medium | Enable asyncio debug mode; audit `get_model_info()` call |
| Pre-API audit | C-2: ffmpeg pipe deadlock | High | Audit `utils/ffmpeg.py` concurrent pipe draining before any web code |
| Pipeline control endpoints (trigger/stop) | M-1: Concurrent runs | High | Global `asyncio.Lock` + HTTP 409 on second trigger |
| SSE progress endpoint + event bus | C-3: Queue leak on disconnect | High | Bounded queue + heartbeat + `try/finally` unregister |
| SSE progress endpoint + event bus | M-4: SSE test patterns | Medium | Session-scoped event loop + line-by-line streaming reads in tests |
| DB viewer endpoints (read-only REST) | C-1: Shared DB connection | High | Separate read-only aiosqlite connection + WAL mode + `PRAGMA query_only=ON` |
| Settings PATCH endpoint | C-4: Config partial write | High | Temp-file + `os.replace()` + validate before writing |
| All API phases | M-4: LiteLLM event loop binding | Medium | `asyncio_mode=auto`, `loop_scope=session` in pytest config |

---

## Sources

- aiohttp-sse library and disconnect detection: https://github.com/aio-libs/aiohttp-sse
- aiohttp client disconnect handling and `is_connected()`: https://github.com/aio-libs/aiohttp/issues/4770
- aiohttp graceful shutdown sequence and `cleanup_ctx` pattern: https://docs.aiohttp.org/en/stable/web_advanced.html
- aiohttp `on_cleanup` abort with `CancelledError` (known issue): https://github.com/aio-libs/aiohttp/issues/5672
- aiohttp active tasks vs cleanup ordering: https://github.com/aio-libs/aiohttp/issues/3593
- aiosqlite shared connection transaction pitfall: https://github.com/omnilib/aiosqlite/issues/19
- aiosqlite "database is locked" despite timeout (known bug): https://github.com/omnilib/aiosqlite/issues/251
- SQLite WAL mode concurrency: https://sqlite.org/wal.html
- SQLite concurrent writes and "database is locked": https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/
- SQLite + asyncio best practices: https://piccolo-orm.readthedocs.io/en/1.3.2/piccolo/tutorials/using_sqlite_and_asyncio_effectively.html
- asyncio subprocess pipe deadlock: https://docs.python.org/3/library/asyncio-subprocess.html
- asyncio event loop blocking: https://docs.python.org/3/library/asyncio-dev.html
- LiteLLM `acompletion` event loop binding issues: https://github.com/BerriAI/litellm/issues/14521
- LiteLLM non-true async implementation: https://github.com/BerriAI/litellm/issues/10104
- LiteLLM mock `time.sleep` blocking: https://github.com/BerriAI/litellm/discussions/9852
- Atomic file writes in Python: https://sahmanish20.medium.com/better-file-writing-in-python-embrace-atomic-updates-593843bfab4f
- asyncio shared state pitfalls: https://www.inngest.com/blog/no-lost-updates-python-asyncio
- asyncio graceful shutdown patterns: https://roguelynn.com/words/asyncio-graceful-shutdowns/
- SSE asyncio.Queue memory patterns: https://medium.com/@Rachita_B/lookout-for-these-cryptids-while-working-with-server-sent-events-43afabb3a868
- aiohttp testing documentation: https://docs.aiohttp.org/en/stable/testing.html
- pytest-asyncio + aiohttp SSE test patterns: https://github.com/aio-libs/aiohttp-sse/blob/master/tests/test_sse.py

---

*Pitfall research: 2026-05-14*
