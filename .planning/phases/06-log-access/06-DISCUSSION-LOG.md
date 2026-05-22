# Phase 6: Log Access - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-22
**Phase:** 6-log-access
**Areas discussed:** Nested path encoding, Tail initial content, Log content response type, Tail polling interval, SSE event format for /tail, Path traversal validation, Log directory config

---

## Nested path encoding

| Option | Description | Selected |
|--------|-------------|----------|
| aiohttp tail match | Route as /api/v1/logs/{tail:.*} — captures full relative path including slashes | ✓ |
| URL-encoded single segment | Clients send GET /logs/episodes%2Ffeed%2Fep.log — server decodes | |
| Flat listing only | Only list/serve top-level *.log files; episode logs excluded | |

**User's choice:** aiohttp tail match

**Listing structure sub-question:**

| Option | Description | Selected |
|--------|-------------|----------|
| Flat list with relative paths | [{filename, size_bytes, last_modified}, ...] | |
| Hierarchical tree | {app_logs: [...], episode_logs: {feed-slug: [...]}} | ✓ |

**User's choice:** Hierarchical tree — `{app_logs: [...], episode_logs: {"feed-slug": [...]}}`

**Notes:** `filename` in each entry is the relative path from `logs/` root, used directly in the URL.

---

## Tail initial content

| Option | Description | Selected |
|--------|-------------|----------|
| Last N bytes on connect, then stream | Send last N bytes immediately, then stream new appended lines | ✓ |
| EOF only — new lines only | No history; only lines appended after connect | |
| You decide | Claude picks best UX | |

**User's choice:** Last N bytes on connect, then stream

**Backfill size sub-question:**

| Option | Description | Selected |
|--------|-------------|----------|
| 8 KB fixed | Covers ~100-200 log lines | |
| Configurable via ?bytes=N | Default 8192; let caller decide | ✓ |
| Last 50 lines (line-counted) | Seek backwards for 50 newlines | |

**User's choice:** Configurable via `?bytes=N`, default 8192.

---

## Log content response type

| Option | Description | Selected |
|--------|-------------|----------|
| text/plain | Raw log text; metadata in headers | ✓ |
| application/json | {content: "...", offset: N, size: N} | |

**User's choice:** `text/plain`

**Metadata location sub-question:**

| Option | Description | Selected |
|--------|-------------|----------|
| Response headers | X-Log-Size, X-Log-Offset, X-Log-Limit | ✓ |
| No metadata | Body is just the slice | |

**User's choice:** Response headers — `X-Log-Size`, `X-Log-Offset`, `X-Log-Limit`.

---

## Tail polling interval

| Option | Description | Selected |
|--------|-------------|----------|
| 1 second fixed | asyncio.to_thread polls every 1s | |
| 0.5 seconds fixed | More responsive, doubles I/O | |
| Configurable ?interval=N | Caller controls, default 1s | ✓ |

**User's choice:** Configurable `?interval=N`

**Range sub-question:**

| Option | Description | Selected |
|--------|-------------|----------|
| Default 1s, min 0.5s, max 10s | Reasonable clamping | ✓ |
| Default 1s, no min/max | Trust the caller | |
| You decide | Claude picks range | |

**User's choice:** Default 1.0s, clamp to [0.5, 10.0] silently.

**Rotation sub-question:**

| Option | Description | Selected |
|--------|-------------|----------|
| Detect rotation, reopen from start | If size shrinks, reopen from byte 0 | ✓ |
| Emit SSE error event and close stream | Client must reconnect | |
| You decide | Claude chooses safer approach | |

**User's choice:** Detect rotation (size shrink), reopen from byte 0.

---

## SSE event format for /tail

| Option | Description | Selected |
|--------|-------------|----------|
| Plain text line(s) as data field | data: <log lines>\n\n — no JSON wrapping | ✓ |
| JSON per event: {lines, byte_offset} | Enables client-side reconnect tracking | |
| You decide | Claude follows existing SSE patterns | |

**User's choice:** Plain text in `data:` field.

---

## Path traversal validation

| Option | Description | Selected |
|--------|-------------|----------|
| Resolve absolute path, check prefix, return 400 | (log_dir / tail).resolve().is_relative_to(log_dir.resolve()) | ✓ |
| Reject '..' in raw string, return 404 | String check before path ops | |
| You decide | Claude uses safest approach | |

**User's choice:** Resolve + `is_relative_to()` check, return **400** on failure.

---

## Log directory config

| Option | Description | Selected |
|--------|-------------|----------|
| Constructor arg log_dir: Path | create_logs_router(log_dir: Path) | ✓ |
| Read from app dict in handlers | app["log_dir"] in each handler | |
| You decide | Claude follows established factory pattern | |

**User's choice:** Constructor argument `log_dir: Path`.

---

## Claude's Discretion

- Exact route registration order for `/tail` suffix vs glob route
- Whether `asyncio.to_thread` wraps per-poll `read()` with open handle or per-call open
- SSE event `id:` field (omit — EVT-02 deferred)

## Deferred Ideas

- Last-Event-ID replay for /tail — deferred to v2 (EVT-02)
- Log search/filtering (`?contains=ERROR`) — future enhancement
- Log deletion (`DELETE /api/v1/logs/{filename}`) — not requested
