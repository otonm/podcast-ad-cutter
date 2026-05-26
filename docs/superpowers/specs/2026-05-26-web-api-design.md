# Podcast Ad Cutter — Web API Spec

**Version:** 1.0  
**Base path:** `/api/v1`  
**Transport:** HTTP/1.1, JSON request and response bodies unless noted  
**Content-Type:** `application/json` on all JSON endpoints; `text/event-stream` on SSE endpoints; `text/plain` on log download

---

## 1. Concepts

### 1.1 Feed Slugs

Feed titles are converted to URL-safe slugs using the `python-slugify` library (`slugify(title)`). The slug is the canonical URL identifier for a feed. Example: `"My Podcast"` → `my-podcast`.

Slugs are computed on the fly from the config file; they are **not stored in the database**. If you rename a feed title, its slug changes and old slug URLs return 404.

### 1.2 Pipeline States

The pipeline is a state machine. The `state` field in `/api/v1/status` takes one of three values:

| Value | Meaning |
|---|---|
| `idle` | No run active. Mutations (skip, reprocess) are permitted. |
| `running` | A pipeline run is in progress. Mutations are blocked (409). |
| `stopping` | A graceful stop was requested; waiting for the current stage to finish. |

State transitions:
- `idle → running` on POST `/run` or POST `/feeds/{slug}/run`
- `running → stopping` on POST `/run/stop` (graceful)
- `running/stopping → idle` when the pipeline task exits (any outcome)
- `running → idle` immediately on POST `/run/stop?force=true` (task cancelled; state reset in the finally block)

### 1.3 Episode Pipeline States

Each episode carries a `pipeline_state` field (computed, not stored directly). The resolution order is:

1. `skipped` — `episodes.skipped = 1` in DB (takes highest priority, always wins)
2. `complete` — output audio file exists on disk at `output_dir/<feed-slug>/<DD.MM.YYYY>-<title-slug>.<ext>`
3. `processed` — row in `ad_detection_runs` for this GUID
4. `transcribed` — row in `transcriptions` for this GUID
5. `downloaded` — row in `episode_audio_metadata` for this GUID
6. `pending` — no match in any join table

Note: `complete` can upgrade from `downloaded`, `transcribed`, or `processed` state — the output file check happens regardless of DB state.

### 1.4 Reprocess Stages

When resetting an episode, the optional `stage` parameter controls which DB records are cleared:

| Stage | Clears from |
|---|---|
| `download` | episode_audio_metadata |
| `transcribe` | transcriptions, transcription_segments |
| `topic` | topics |
| `ad-detect` | ad_detection_runs, ad_segments |
| `edit` | (output file on disk; marks episode for re-editing) |

If `stage` is omitted, all stages are cleared (full reset).

---

## 2. Endpoints

### 2.1 Health

#### `GET /api/v1/health`

Returns server liveness and uptime. No dependencies on the database or pipeline state.

**Response 200**
```json
{
  "status": "ok",
  "uptime_seconds": 142.37,
  "version": "1.2.0"
}
```

| Field | Type | Description |
|---|---|---|
| `status` | `"ok"` | Always `"ok"` when the server responds |
| `uptime_seconds` | `float` | Seconds since process start, rounded to 2 dp |
| `version` | `string` | Package version from `pyproject.toml`; `"unknown"` if unresolvable |

---

### 2.2 Pipeline Control

#### `GET /api/v1/status`

Returns the current run state. Always 200; never errors.

**Response 200**
```json
{
  "state": "running",
  "started_at": "2026-05-26T10:00:00.000000+00:00",
  "active_feed_slug": "my-podcast",
  "current_episode_guid": "abc123",
  "feeds": {
    "my-podcast": {
      "episodes_total": 10,
      "episodes_done": 3,
      "episodes_failed": 1
    }
  }
}
```

| Field | Type | Description |
|---|---|---|
| `state` | `string` | `"idle"`, `"running"`, or `"stopping"` |
| `started_at` | `string\|null` | ISO-8601 UTC timestamp; `null` when idle |
| `active_feed_slug` | `string\|null` | Slug of the feed being processed; `null` for full runs or when idle |
| `current_episode_guid` | `string\|null` | GUID of the episode currently processing; `null` when idle |
| `feeds` | `object` | Map of `slug → FeedRunCounts`; empty `{}` when idle |

**FeedRunCounts fields:**

| Field | Type |
|---|---|
| `episodes_total` | `integer` |
| `episodes_done` | `integer` |
| `episodes_failed` | `integer` |

---

#### `POST /api/v1/run`

Starts a full pipeline run across all enabled feeds.

**Request body:** none required

**Response 202**
```json
{
  "status": "started",
  "started_at": "2026-05-26T10:00:00.000000+00:00"
}
```

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 409 | State is not `idle` | `{"error": "a run is already active"}` |

---

#### `POST /api/v1/run/stop`

Requests the active run to stop.

**Query parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `force` | `boolean` | `false` | If `true`, cancels the asyncio task immediately (no stage cleanup). If `false`, sets a stop event that the pipeline checks between stages. |

**Response 202**
```json
{
  "status": "stopping",
  "mode": "graceful"
}
```

`mode` is either `"graceful"` or `"force"`.

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 409 | State is `idle` | `{"error": "no run is active"}` |

---

#### `POST /api/v1/feeds/{slug}/run`

Starts a pipeline run for a single feed identified by slug.

**Path parameters:**

| Param | Description |
|---|---|
| `slug` | URL-safe feed title slug (see §1.1) |

**Response 202**
```json
{
  "status": "started",
  "feed": "my-podcast",
  "started_at": "2026-05-26T10:00:00.000000+00:00"
}
```

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 404 | Slug not found in config | `{"error": "feed not found"}` |
| 409 | State is not `idle` | `{"error": "a run is already active"}` |

---

#### `POST /api/v1/episodes/{guid}/skip`

Marks an episode as skipped. A skipped episode is excluded from all future pipeline runs without being deleted.

**Precondition:** pipeline state must be `idle`.

**Path parameters:**

| Param | Description |
|---|---|
| `guid` | Episode GUID (from the podcast RSS feed) |

**Response 200**
```json
{
  "status": "skipped",
  "guid": "abc123"
}
```

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 404 | GUID not found in DB | `{"error": "episode not found: <guid>"}` |
| 409 | Run is active | `{"error": "cannot modify episodes while a run is active"}` |

---

#### `POST /api/v1/episodes/{guid}/reprocess`

Resets an episode's processing state so it will be picked up again on the next run.

**Precondition:** pipeline state must be `idle`.

**Path parameters:**

| Param | Description |
|---|---|
| `guid` | Episode GUID |

**Query parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `stage` | `string` | none | Stage to reset from. Valid values: `download`, `transcribe`, `topic`, `ad-detect`, `edit`. If omitted, all stages are cleared. |

**Response 200**
```json
{
  "status": "reset",
  "guid": "abc123",
  "from_stage": "transcribe"
}
```

`from_stage` is `null` when no stage was provided (full reset).

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 404 | GUID not found in DB | `{"error": "episode not found: <guid>"}` |
| 409 | Run is active | `{"error": "cannot modify episodes while a run is active"}` |
| 422 | Invalid `stage` value | `{"error": "invalid stage: <value>"}` |

---

### 2.3 Feeds (Config Management)

All feed endpoints read from and write to `config.yaml` on disk. Writes are **atomic** (temp file → `os.replace`). The feeds list in config drives the pipeline; there is no separate DB table for feeds.

#### `GET /api/v1/feeds`

Returns all configured feeds enriched with an episode count from the database.

**Response 200** — array of feed objects:
```json
[
  {
    "slug": "my-podcast",
    "title": "My Podcast",
    "url": "https://example.com/feed.rss",
    "enabled": true,
    "episodes_to_keep": 10,
    "episode_count": 42
  }
]
```

| Field | Type | Description |
|---|---|---|
| `slug` | `string` | Computed from title (see §1.1) |
| `title` | `string` | Feed title as stored in config |
| `url` | `string` | RSS feed URL |
| `enabled` | `boolean` | Whether the feed is processed in runs |
| `episodes_to_keep` | `integer` | Retention count (≥ 1) |
| `episode_count` | `integer` | Count of episodes in DB with matching podcast title |

---

#### `POST /api/v1/feeds`

Adds a new feed to the config.

**Request body:**
```json
{
  "title": "My Podcast",
  "url": "https://example.com/feed.rss",
  "enabled": true,
  "episodes_to_keep": 10
}
```

| Field | Required | Type | Constraints |
|---|---|---|---|
| `title` | yes | `string` | Must be unique (case-sensitive) |
| `url` | yes | `string` | RSS feed URL |
| `enabled` | no | `boolean` | Default: `true` |
| `episodes_to_keep` | no | `integer` | Default: `10`, must be ≥ 1 |

**Response 201** — the created feed object (same shape as GET, without `slug` and `episode_count`):
```json
{
  "title": "My Podcast",
  "url": "https://example.com/feed.rss",
  "enabled": true,
  "episodes_to_keep": 10
}
```

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 409 | A feed with this title already exists | `{"error": "feed title already exists"}` |
| 422 | Schema validation failure | Pydantic error JSON |

---

#### `PATCH /api/v1/feeds/{slug}`

Updates a feed's properties. Partial updates — only the fields in the request body are changed.

**Title cannot be changed** via this endpoint. Any `title` field in the request body is silently dropped. This is by design: renaming a feed would break its slug and DB linkage.

**Path parameters:**

| Param | Description |
|---|---|
| `slug` | Feed slug |

**Request body** — any subset of writable fields:
```json
{
  "enabled": false,
  "episodes_to_keep": 5
}
```

**Response 200** — the updated feed object (same shape as POST 201 response):
```json
{
  "title": "My Podcast",
  "url": "https://example.com/feed.rss",
  "enabled": false,
  "episodes_to_keep": 5
}
```

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 404 | Slug not found | `{"error": "feed not found"}` |
| 422 | Schema validation failure | Pydantic error JSON |

---

#### `DELETE /api/v1/feeds/{slug}`

Removes a feed from the config. Does **not** delete its episodes from the database or its output files from disk.

**Path parameters:**

| Param | Description |
|---|---|
| `slug` | Feed slug |

**Response 204** — no body.

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 404 | Slug not found | `{"error": "feed not found"}` |
| 422 | Resulting config would be invalid (e.g. last feed removed) | Pydantic error JSON |

---

### 2.4 Settings

Settings read and write the `config.yaml` sections **except** `feeds` (feeds have their own endpoints). Writes are atomic.

#### `GET /api/v1/settings`

Returns the full application configuration plus credential status (never the actual key values).

**Response 200:**
```json
{
  "feeds": [...],
  "models": {
    "transcription": {"provider": "groq", "model": "whisper-large-v3", "context_window": null},
    "context_extraction": {"provider": "groq", "model": "llama-3.3-70b-versatile", "context_window": null},
    "ad_detection": {"provider": "groq", "model": "llama-3.3-70b-versatile", "context_window": null}
  },
  "paths": {
    "output_dir": "./output",
    "cache_dir": "./cache",
    "data_dir": "./data",
    "log_dir": "./logs"
  },
  "ad_detection": {
    "min_duration": 10000,
    "min_confidence": 0.7
  },
  "output": {
    "file_type": "mp3",
    "bitrate": "128k"
  },
  "log": {
    "level": "ERROR",
    "to_file": false,
    "file_level": "DEBUG",
    "rotate": false,
    "keep_last": 10,
    "per_episode": false
  },
  "base_url": "http://localhost:8080",
  "credentials": {
    "groq_api_key": "set",
    "openai_api_key": "not set",
    "openrouter_api_key": "not set"
  }
}
```

The `credentials` object maps each key field to `"set"` or `"not set"`. The actual key values are never returned.

**Schema details:**

| Section | Field | Type | Constraints |
|---|---|---|---|
| `models.*` | `provider` | `string` | `"groq"`, `"openai"`, or `"openrouter"` |
| `models.*` | `model` | `string` | Model name string |
| `models.*` | `context_window` | `integer\|null` | Optional, > 0 |
| `ad_detection` | `min_duration` | `integer` | > 0 (milliseconds) |
| `ad_detection` | `min_confidence` | `float` | 0.0 – 1.0 |
| `output` | `file_type` | `string` | `"mp3"`, `"m4a"`, `"ogg"`, `"opus"`, or `"flac"` |
| `output` | `bitrate` | `string` | Format: `"<number>k"` e.g. `"128k"` |
| `log` | `level` | `string` | `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"` |

---

#### `PATCH /api/v1/settings`

Updates application settings via deep merge. The `feeds` key is silently stripped — use the feeds endpoints to manage feeds.

**Request body** — any subset of the config structure (deep-merged):
```json
{
  "output": {
    "file_type": "m4a",
    "bitrate": "192k"
  },
  "log": {
    "level": "DEBUG"
  }
}
```

Deep merge rules: dicts are merged recursively; all other types (including lists) are replaced wholesale.

**Response 200** — the full updated config (same shape as GET, without `credentials`).

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 422 | Merged result fails schema validation | Pydantic error JSON |

---

### 2.5 Database Viewer (Read-Only)

These endpoints expose processed data from the SQLite database. All use a dedicated read-only connection (WAL mode) separate from the pipeline connection.

#### `GET /api/v1/db/episodes`

Returns paginated episode records with pipeline state.

**Query parameters:**

| Param | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `limit` | `integer` | `50` | 1–200 | Max rows to return |
| `offset` | `integer` | `0` | ≥ 0 | Row offset for pagination |
| `feed` | `string` | none | — | Filter by feed slug; unknown slug returns `[]` with 200 |

Results are ordered by `pubdate DESC` (episodes with null pubdate sort last).

**Response 200** — array of episode objects:
```json
[
  {
    "id": 1,
    "podcast": "My Podcast",
    "guid": "abc123",
    "title": "Episode Title",
    "pubdate": "2026-04-01T00:00:00+00:00",
    "skipped": 0,
    "url": "https://example.com/ep1.mp3",
    "description": "...",
    "explicit": null,
    "duration": 3600,
    "image_url": null,
    "episode_type": null,
    "itunes_author": null,
    "itunes_subtitle": null,
    "itunes_summary": null,
    "content_encoded": null,
    "link": null,
    "author": null,
    "itunes_title": null,
    "episode_number": null,
    "season_number": null,
    "itunes_block": null,
    "length": null,
    "source_url": null,
    "feed_slug": "my-podcast",
    "pipeline_state": "complete"
  }
]
```

`pipeline_state` is one of: `pending`, `downloaded`, `transcribed`, `processed`, `complete`, `skipped`. See §1.3 for resolution logic.

`feed_slug` is a computed field not stored in the DB (derived from `podcast` title).

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 400 | Non-integer `limit`/`offset`, or `limit` outside 1–200, or `offset < 0` | `{"error": "..."}` |

---

#### `GET /api/v1/db/transcriptions/{guid}`

Returns the full transcription and time-coded segments for an episode.

**Path parameters:**

| Param | Description |
|---|---|
| `guid` | Episode GUID |

**Response 200:**
```json
{
  "guid": "abc123",
  "text": "Full transcript text...",
  "segments": [
    {"start": 0, "end": 5000, "text": "Hello"},
    {"start": 5000, "end": 10000, "text": "world"}
  ]
}
```

Segments are ordered by `start_ms ASC`. Times are in **milliseconds**.

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 404 | No transcription row for this GUID | `{"error": "not found"}` |

---

#### `GET /api/v1/db/ads/{guid}`

Returns the ad detection result for an episode.

**Path parameters:**

| Param | Description |
|---|---|
| `guid` | Episode GUID |

**Response 200:**
```json
{
  "guid": "abc123",
  "detected": true,
  "segments": [
    {
      "start_ms": 1000,
      "end_ms": 30000,
      "confidence": 0.95,
      "sponsor": "ACME Corp",
      "ad_topic": "Streaming"
    }
  ]
}
```

`detected: true` means the ad detection pipeline ran for this episode. An empty `segments` array with `detected: true` means the pipeline ran but found no ads that met the confidence/duration threshold.

`sponsor` and `ad_topic` may be `null` if not identified.

Segments are ordered by `start_ms ASC`. Times are in **milliseconds**.

**Error responses:**

| Status | Condition | Body |
|---|---|---|
| 404 | No ad detection run row for this GUID | `{"error": "not found"}` |

---

#### `GET /api/v1/db/costs`

Returns LLM API cost tracking data.

**Query parameters:**

| Param | Type | Description |
|---|---|---|
| `feed` | `string` | Filter by feed slug. Unknown slug returns empty zero response with 200. |

**Response 200:**
```json
{
  "total": 0.35,
  "by_model": [
    {"provider": "groq", "model": "whisper-large-v3", "cost": 0.10},
    {"provider": "groq", "model": "llama-3.3-70b-versatile", "cost": 0.25}
  ],
  "by_episode": [
    {"guid": "abc123", "cost": 0.05},
    {"guid": "def456", "cost": 0.30}
  ]
}
```

`total` is the sum of all `cost_tracking` rows (including rows with null GUID).  
`by_episode` only includes rows with a non-null GUID; rows from before GUID tracking was added are excluded from this array but included in `total`.

---

### 2.6 Server-Sent Events

#### `GET /api/v1/events`

Opens a persistent SSE stream. The server pushes pipeline events to the client in real time.

**Response:** `text/event-stream` (HTTP 200, connection stays open)

Each event follows the SSE wire format:
```
event: <type>\n
data: <json-payload>\n
\n
```

**Event types and payload shapes:**

#### `run.started`
```json
{
  "feeds": ["My Podcast", "Another Show"],
  "total_episodes": 15
}
```

#### `run.completed`
```json
{
  "feeds": ["My Podcast", "Another Show"]
}
```

#### `episode.stage_changed`
```json
{
  "guid": "abc123",
  "stage": "transcribe",
  "status": "started",
  "feed_slug": "my-podcast"
}
```
`stage` values match the reprocess stage identifiers: `download`, `transcribe`, `topic`, `ad-detect`, `edit`.  
`status` indicates the stage transition (e.g. `"started"`, `"completed"`, `"skipped"`).

#### `episode.download_progress`
```json
{
  "guid": "abc123",
  "feed_slug": "my-podcast",
  "percent": 0.42
}
```
`percent` is in `[0.0, 1.0]`.

#### `episode.encode_progress`
```json
{
  "guid": "abc123",
  "feed_slug": "my-podcast",
  "percent": 0.75
}
```
`percent` is in `[0.0, 1.0]`.

#### `episode.completed`
```json
{
  "guid": "abc123",
  "feed_slug": "my-podcast",
  "outcome": "processed",
  "feed_done": 3,
  "feed_failed": 0,
  "feed_total": 10
}
```

#### `episode.failed`
```json
{
  "guid": "abc123",
  "feed_slug": "my-podcast",
  "error": "HTTP 503: upstream unavailable",
  "feed_done": 2,
  "feed_failed": 1,
  "feed_total": 10
}
```

The SSE connection holds open indefinitely. The server cleans up the subscriber queue in a `finally` block on disconnect, so safe to drop and reconnect at any time. There is no heartbeat or retry header; reconnection is the client's responsibility.

---

### 2.7 Logs

#### `GET /api/v1/logs`

Returns a directory listing of available log files.

**Response 200:**
```json
{
  "app_logs": [
    {
      "filename": "app.log",
      "size_bytes": 10240,
      "last_modified": "2026-05-26T09:00:00+00:00"
    }
  ],
  "episode_logs": {
    "my-podcast": [
      {
        "filename": "episodes/my-podcast/episode-title.2026-05-26.log",
        "size_bytes": 4096,
        "last_modified": "2026-05-26T09:05:00+00:00"
      }
    ]
  }
}
```

`app_logs` — files matching `logs/*.log`, sorted by mtime.  
`episode_logs` — dict keyed by feed directory name; each value is a list of `.log` files in `logs/episodes/<feed-dir>/`.

If the log directory does not exist, both lists are empty.

---

#### `GET /api/v1/logs/{path}`

Downloads a log file as plain text.

**Path parameters:**

| Param | Description |
|---|---|
| `path` | Relative path within the log directory (e.g. `app.log`, `episodes/my-podcast/ep.log`) |

**Query parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `offset` | `integer` | `0` | Byte offset to start reading from |
| `limit` | `integer` | none | Max bytes to return; omit for full file |

**Response 200** — plain text body, UTF-8. Custom headers:

| Header | Value |
|---|---|
| `X-Log-Size` | Total file size in bytes |
| `X-Log-Offset` | Actual byte offset returned |
| `X-Log-Limit` | Number of bytes returned |

**Error responses:**

| Status | Condition |
|---|---|
| 400 | Path traversal attempt (resolved path outside log dir) |
| 400 | Non-integer `offset` or `limit` |
| 404 | File does not exist |

---

#### `GET /api/v1/logs/{path}/tail`

Streams a log file as a live SSE tail (like `tail -f`).

**Path parameters:**

| Param | Description |
|---|---|
| `path` | Relative path within the log directory |

**Query parameters:**

| Param | Type | Default | Constraints | Description |
|---|---|---|---|---|
| `bytes` | `integer` | `8192` | — | Bytes of backfill to send immediately on connect |
| `interval` | `float` | `1.0` | Clamped to 0.5–10.0 | Poll interval in seconds |

**Response:** `text/event-stream`

Wire format: each poll that produces new data sends one SSE frame:
```
data: <new log text>\n
\n
```

The stream sends a backfill chunk on connect (last `bytes` bytes of the file), then polls every `interval` seconds for new content. File rotation is detected: if the file shrinks below the last position, reading restarts from byte 0.

**Error responses:**

| Status | Condition |
|---|---|
| 400 | Path traversal attempt |
| 404 | File does not exist (check at connection time) |

---

## 3. Common Error Response Shape

All error responses use `Content-Type: application/json` and a body of:
```json
{"error": "<human-readable message>"}
```

Exception: Pydantic 422 responses use Pydantic's native JSON error format (an object with a `detail` array), not the `{"error": "..."}` envelope.

---

## 4. Authentication

No authentication is implemented. The API is intended for local/private network use only.

---

## 5. Concurrency and Safety Guarantees

- **Config writes are atomic.** All PATCH/POST/DELETE endpoints that modify `config.yaml` write to a temp file on the same filesystem then call `os.replace()`. A crash mid-write cannot corrupt the config file.
- **Database reads use a dedicated read-only connection.** The `/api/v1/db/*` endpoints open a fresh `ReadOnlyDatabase` connection (WAL mode) for each request, separate from the pipeline's write connection.
- **Feed config is re-read from disk on every request.** GET `/api/v1/feeds` and GET `/api/v1/settings` read `config.yaml` fresh on each call, so changes made via PATCH are immediately visible.
- **Slug resolution uses in-memory config.** The `/api/v1/db/episodes` and `/api/v1/db/costs` slug-to-title resolution uses the config snapshot loaded at server startup. A feed added after startup will not be resolvable by slug in those endpoints until the server restarts.

---

## 6. SSE Disconnect Behaviour

Both `/api/v1/events` and `/api/v1/logs/{path}/tail` are SSE streams. On client disconnect:
- `/api/v1/events`: the subscriber queue is unregistered in a `finally` block. Safe to reconnect immediately.
- `/api/v1/logs/{path}/tail`: the file handle is closed in a `finally` block. A `ClientConnectionResetError` is caught and suppressed silently.

---

## 7. Quick Reference — All Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/health` | Server health and uptime |
| GET | `/api/v1/status` | Pipeline run state |
| POST | `/api/v1/run` | Start a full pipeline run |
| POST | `/api/v1/run/stop` | Stop the active run (graceful or force) |
| POST | `/api/v1/feeds/{slug}/run` | Start a run for one feed |
| POST | `/api/v1/episodes/{guid}/skip` | Mark episode as skipped |
| POST | `/api/v1/episodes/{guid}/reprocess` | Reset episode for reprocessing |
| GET | `/api/v1/feeds` | List all configured feeds |
| POST | `/api/v1/feeds` | Add a new feed |
| PATCH | `/api/v1/feeds/{slug}` | Update a feed |
| DELETE | `/api/v1/feeds/{slug}` | Remove a feed |
| GET | `/api/v1/settings` | Get application settings |
| PATCH | `/api/v1/settings` | Update application settings |
| GET | `/api/v1/db/episodes` | Paginated episode list with pipeline state |
| GET | `/api/v1/db/transcriptions/{guid}` | Episode transcription + segments |
| GET | `/api/v1/db/ads/{guid}` | Episode ad detection result |
| GET | `/api/v1/db/costs` | LLM cost tracking summary |
| GET | `/api/v1/events` | SSE stream of pipeline events |
| GET | `/api/v1/logs` | Log file directory listing |
| GET | `/api/v1/logs/{path}` | Download a log file |
| GET | `/api/v1/logs/{path}/tail` | Live SSE tail of a log file |
