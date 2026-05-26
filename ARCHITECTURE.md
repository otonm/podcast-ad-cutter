# Architecture

## Overview

Podcast Ad Cutter is a Python 3.12 async application. It has two operating modes:

- **Pipeline mode** — a one-shot run that processes configured feeds end-to-end and exits
- **Server mode** — a long-running aiohttp HTTP server that exposes a REST/SSE API and triggers pipeline runs on demand

Both modes share the same `Pipeline` class and `Config` objects. The server mode simply wraps the pipeline in an asyncio task and exposes control via HTTP.

---

## Tech Stack

| Concern | Library |
|---|---|
| HTTP server & client | `aiohttp` |
| Database | `aiosqlite` (async SQLite) |
| LLM calls (transcription, topic extraction, ad detection) | `litellm` |
| Audio processing | `ffmpeg` (subprocess via `asyncio.create_subprocess_exec`) |
| Configuration schema | `pydantic` + `pydantic-settings` |
| Config file format | YAML (`pyyaml`) |
| URL slugs | `python-slugify` |
| Environment variables | `python-dotenv` |
| Scheduling (Docker) | `supercronic` (cron runner in the container) |
| Privilege drop (Docker) | `gosu` |

---

## Directory Layout

```
main.py                     # CLI entry point — argument parsing, logging setup
config/
  config_loader.py          # Pydantic models for config.yaml; Credentials from env
components/
  pipeline.py               # Top-level orchestrator; owns all component instances
  feed_downloader.py        # Downloads RSS XML via aiohttp
  feed_parser.py            # Parses RSS XML into typed Episode objects
  feed_publisher.py         # Writes clean RSS 2.0 XML to the output directory
  episode_downloader.py     # Streams audio files to cache/; handles redirects
  audio_prober.py           # Runs ffprobe to read codec/duration/bitrate
  audio_preprocessor.py     # Re-encodes to mono AAC via ffmpeg (STT preparation)
  episode_transcriptor.py   # Sends audio to Whisper-compatible STT via litellm
  topic_extractor.py        # Extracts show context (topic, hosts) via LLM
  ad_detector.py            # Identifies ad segments via LLM; returns time ranges
  ad_parser.py              # Converts raw LLM ad output into cut ranges
  audio_editor.py           # Cuts ad segments and re-encodes output via ffmpeg
  episode_copier.py         # Copies unedited episodes to the output directory
database/
  connection.py             # Database (write) and ReadOnlyDatabase (read-only) context managers
  episode_store.py          # CRUD for the episodes table
  audio_metadata_store.py   # CRUD for episode_audio_metadata
  transcription_store.py    # CRUD for transcriptions + transcription_segments
  topic_store.py            # CRUD for topic_extractions
  ad_store.py               # CRUD for ad_detection_runs + ad_segments
  cost_tracking_store.py    # Insert + query for cost_tracking
models/
  feed.py                   # Episode, ParsedFeed, AudioMetadata, PublisherInput dataclasses
  ad_detection.py           # AdSegment, AdDetectionResponseSchema, CutRange
  transcription.py          # Transcription, TranscriptionSegment, TranscriptionCost
  topic.py                  # TopicExtraction
  cost.py                   # Cost model helpers
api/
  server.py                 # aiohttp app factory + AppRunner/TCPSite lifecycle
  event_bus.py              # In-process broadcast bus; PipelineEvent, PipelineEventType
  run_state.py              # RunState dataclass; FeedRunCounts; VALID_STAGES
  routes/
    health.py               # GET /api/v1/health
    control.py              # GET /api/v1/status, POST /api/v1/run, /run/stop, /feeds/{slug}/run, episodes
    feeds.py                # GET/POST/PATCH/DELETE /api/v1/feeds
    settings.py             # GET/PATCH /api/v1/settings
    db.py                   # GET /api/v1/db/episodes|transcriptions|ads|costs
    events.py               # GET /api/v1/events (SSE)
    logs.py                 # GET /api/v1/logs, /logs/{path}, /logs/{path}/tail
utils/
  ffmpeg.py                 # Thin async wrapper around ffmpeg/ffprobe subprocesses
  llm.py                    # litellm cost computation and response helpers
  episode_log.py            # Per-episode log handler management
  exceptions.py             # ConfigError, TranscriptionError, AdDetectionError
```

---

## Configuration System

`config/config_loader.py` defines a hierarchy of Pydantic models:

```
Config
├── app: AppConfig          # from config.yaml
│   ├── feeds: list[FeedConfig]
│   ├── models: ModelsConfig
│   │   ├── transcription: LLMConfig
│   │   ├── context_extraction: LLMConfig
│   │   └── ad_detection: LLMConfig
│   ├── paths: PathsConfig
│   ├── ad_detection: AdDetectionConfig
│   ├── output: OutputConfig
│   ├── log: LoggingConfig
│   └── base_url: str
└── credentials: Credentials  # from environment variables via pydantic-settings
```

`load_config()` reads and validates `config.yaml`, then checks that every provider referenced in `models.*` has a corresponding non-empty environment variable. It raises `ConfigError` (caught by `main.py`) if anything is missing or invalid.

The API routes that mutate config (feeds, settings) read and write `config.yaml` directly on every request. Writes are atomic: the new config is serialized to a temp file in the same directory, then swapped in with `os.replace()`. `AppConfig` is re-validated through Pydantic before any write is committed.

---

## Pipeline Flow

`Pipeline.run()` is the top-level async method. It runs all stages sequentially within a single `async with Database(...)` context (one write connection for the duration of the run).

### Phase 1 — Feed Selection and Download

1. Filter configured feeds by `enabled` flag (or `feed_name` override).
2. Download RSS XML for all selected feeds **concurrently** via `asyncio.gather`.
3. Parse each XML response into `ParsedFeed` (list of `Episode` objects) using `FeedParser`.

### Phase 2 — Per-Feed Pre-processing

For each feed:
1. Save all discovered episodes to the `episodes` table (upsert by GUID).
2. Fetch the episode list respecting `episodes_to_keep`.
3. Publish the clean RSS feed file to `output_dir/<feed-slug>/<feed-slug>.rss`.
4. Emit `run.started` event.

### Phase 3 — Per-Episode State Machine

Each episode is processed by a `while True` loop that checks what is missing and performs exactly one step per iteration, persisting results before looping. This means a crash or stop at any point is safe — the next run resumes from the last persisted state.

```
┌──────────────────────────────────────────────────────────┐
│                   Episode State Machine                   │
│                                                          │
│  output file exists? ──yes──► copy URL → done            │
│       │ no                                               │
│  audio in cache? ──no──► download → cache                │
│       │ yes                                              │
│  audio metadata in DB? ──no──► ffprobe → DB              │
│       │ yes                                              │
│  mono AAC in cache? ──no──► ffmpeg re-encode → cache     │
│       │ yes                                              │
│  transcription in DB? ──no──► STT (litellm) → DB         │
│       │ yes                                              │
│  topic extraction in DB? ──no──► LLM → DB                │
│       │ yes                                              │
│  ad detection in DB? ──no──► LLM → DB                    │
│       │ yes                                              │
│  ads found above threshold?                              │
│    yes ──► ffmpeg cut + encode → output_dir              │
│    no  ──► copy original to output_dir                   │
└──────────────────────────────────────────────────────────┘
```

After each episode completes (success or failure), `episode.completed` or `episode.failed` is emitted on the event bus and per-feed counters in `RunState` are updated.

### Stage Details

| Stage | Component | What it does |
|---|---|---|
| Download | `EpisodeDownloader` | Streams audio from the enclosure URL to `cache_dir/`. Follows up to 10 redirects. Uses `User-Agent: curl/7.88.1` for CDN compatibility. |
| Probe | `AudioProber` | Runs `ffprobe -v quiet -print_format json -show_streams` to read codec, channels, duration, bitrate. |
| Preprocess | `AudioPreprocessor` | Re-encodes to mono AAC at 32 kbps via ffmpeg. This is the format sent to the STT model. |
| Transcribe | `EpisodeTranscriptor` | Calls `litellm.atranscription` with `response_format=verbose_json`. Long files are chunked at ~98 min to stay under the 25 MB Groq limit. Results stored in `transcriptions` + `transcription_segments`. |
| Topic Extraction | `TopicExtractor` | Sends transcript + episode metadata to an LLM to extract show topic, hosts, and show name. Used as context for ad detection. |
| Ad Detection | `AdDetector` | Sends transcript segments + topic context to an LLM with a structured JSON schema response. Returns time-coded ad segment candidates with confidence scores. |
| Ad Parsing | `AdParser` | Converts raw LLM ad segment output into `CutRange` objects, filtering by `min_duration` and `min_confidence`. |
| Audio Editing | `AudioEditor` | Builds an ffmpeg `atrim`+`concat` filter graph to remove cut ranges and re-encode the result to the configured output format. |
| Copy | `EpisodeCopier` | When no ads are cut, copies the original (or preprocessed) audio to `output_dir/` and constructs the episode URL for the RSS feed. |

---

## Database Schema

SQLite file at `data_dir/data.db`. Schema is applied idempotently on every `Database` open — `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` with suppressed `OperationalError` for migrations.

```
episodes
  id, podcast, guid (UNIQUE), title, pubdate, url, description,
  explicit, duration, image_url, episode_type, itunes_author,
  itunes_subtitle, itunes_summary, content_encoded, link, author,
  itunes_title, episode_number, season_number, itunes_block,
  length, source_url, skipped

episode_audio_metadata
  id, guid → episodes, duration, codec, channels, bitrate

transcriptions
  id, guid → episodes, transcription (full text)

transcription_segments
  id, guid → episodes, start_ms, end_ms, text
  INDEX: idx_transcription_segments_guid

cost_tracking
  id, provider, model, cost, guid → episodes (nullable)

topic_extractions
  id, guid → episodes, podcast, title, topic, hosts, show

ad_segments
  id, guid → episodes, start_ms, end_ms, confidence, sponsor, ad_topic, indices
  INDEX: idx_ad_segments_guid

ad_detection_runs
  id, guid → episodes (UNIQUE — one row per episode that completed ad detection)
```

The `ad_detection_runs` row acts as a completion flag: its presence means ad detection ran for that episode, even if `ad_segments` is empty (i.e. no ads were found above the threshold).

---

## API Layer

The API server is built with `aiohttp`. It is started via `AppRunner` + `TCPSite` (never `web.run_app()`) so the event loop remains shared with the pipeline task.

### App Factory

`api/server.py:create_app()` builds the `web.Application`, stores shared objects in `app[]`, and registers all route tables. It takes no global state — safe to call in tests with `TestClient`.

Shared objects stored on the app dict:
- `app["event_bus"]` — `EventBus` instance
- `app["run_state"]` — `RunState` instance
- `app["config_path"]` — `Path` to `config.yaml`

### Route Modules

Each route module exposes a factory function (`create_*_router(...)`) that captures its dependencies via closure and returns a `web.RouteTableDef`. This keeps handlers decoupled from global state and testable in isolation.

### Event Bus

`api/event_bus.py` implements a simple broadcast bus. Each SSE client that connects to `/api/v1/events` gets its own `asyncio.Queue`. `EventBus.emit()` puts the event on every subscriber's queue. `EventBus.unsubscribe()` is always called in a `finally` block on disconnect.

### Run State

`api/run_state.py:RunState` is the shared mutable state for the active pipeline run. It lives on the aiohttp app dict and is read/written by both the control route handlers and the pipeline task. It tracks:
- `state` — `"idle"` | `"running"` | `"stopping"`
- `started_at` — UTC timestamp of the current run
- `active_feed_slug` — slug of the feed being processed (or `None` for full runs)
- `current_episode_guid` — GUID of the episode being processed
- `task` — reference to the asyncio `Task` running the pipeline
- `feeds` — dict of `slug → FeedRunCounts` updated as episodes complete

### Database Concurrency

The pipeline holds one write connection (`Database`) open for the full duration of a run. The API read endpoints each open a fresh `ReadOnlyDatabase` (SQLite URI `?mode=ro`) per request. SQLite WAL mode allows concurrent reads alongside the writer without blocking.

---

## Docker Architecture

The container image (based on `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`) includes:
- Python 3.12 + all Python dependencies (installed via `uv sync --frozen`)
- `ffmpeg` (via apt)
- `supercronic` — a cron runner designed for containers; replaces system cron
- `gosu` — for privilege drop from root to the `app` user after volume ownership is fixed

**Startup flow — cron mode (default):**
1. `entrypoint.sh` runs as root, fixes ownership of bind-mounted volumes, writes a crontab file from `$CRON_SCHEDULE`, then execs `gosu app supercronic /tmp/crontab`
2. `supercronic` runs `run.sh` on the cron schedule as the `app` user
3. `run.sh` assembles CLI arguments from environment variables and execs `python main.py`

**Startup flow — server mode (`APP_SERVE=true`):**
1. `entrypoint.sh` detects `APP_SERVE`, skips supercronic, and directly execs `gosu app /bin/sh /app/run.sh`
2. `run.sh` passes `--serve` (plus optional `--host`/`--port` from `APP_HOST`/`APP_PORT`) to `python main.py`
3. The process runs indefinitely, serving the HTTP API on port 8080 (or `APP_PORT`)

The `EXPOSE 8080` directive in the Dockerfile documents the server port. In server mode, the `docker-compose.yml` `ports` section must be uncommented to make it reachable from the host.

---

## Testing

Tests live in `tests/` and use `pytest` with `pytest-asyncio` (`asyncio_mode = "auto"`).

API tests use `aiohttp.test_utils.TestClient` + `TestServer` with the real `create_app()` factory — no HTTP mocking. Pipeline component tests mock external I/O (HTTP calls via `aioresponses`, LLM calls via `unittest.mock`). Database tests use `aiosqlite` against real SQLite in `tmp_path`.

```bash
uv run pytest              # run all tests
uv run pytest --cov=.      # run with coverage (must be 100%)
uv run ruff                # lint
```
