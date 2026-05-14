<!-- refreshed: 2026-05-14 -->
# Architecture

**Analysis Date:** 2026-05-14

## System Overview

```text
┌─────────────────────────────────────────────────────────────────┐
│                         main.py (CLI entry point)               │
│  argparse → Config → Pipeline.run()                             │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│             components/pipeline.py  (Pipeline)                  │
│  Top-level orchestrator; sole owner of Config                   │
├──────────────┬──────────────┬────────────────┬──────────────────┤
│ Feed stage   │ Episode state│  ML stage      │  Post-process    │
│ FeedDownload │ machine      │  EpisodeTrans- │  AdParser        │
│ FeedParser   │ (while loop) │  criptor       │  AudioEditor     │
│ FeedPublish  │              │  TopicExtract- │  EpisodeCopier   │
│              │              │  or            │                  │
│              │              │  AdDetector    │                  │
└──────┬───────┴──────┬───────┴────────────────┴──────────────────┘
       │              │
       ▼              ▼
┌─────────────┐  ┌──────────────────────────────────────────────┐
│ models/     │  │ database/                                    │
│ feed.py     │  │  connection.py (Database — aiosqlite)        │
│ ad_detect.py│  │  episode_store.py, transcription_store.py    │
│ transcripto.│  │  topic_store.py, ad_store.py                 │
│ topic.py    │  │  audio_metadata_store.py, cost_tracking.py   │
│ cost.py     │  └──────────────────────────────────────────────┘
└─────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│  utils/                                                         │
│  ffmpeg.py  exceptions.py  llm.py  episode_log.py              │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│  External                                                        │
│  Groq / OpenAI / OpenRouter (LiteLLM)   ffmpeg / ffprobe        │
│  SQLite (data/data.db)                  RSS feeds (HTTP)        │
└─────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| Pipeline | Orchestrates all stages; sole Config owner; passes plain data to every component | `components/pipeline.py` |
| FeedDownloader | Async concurrent HTTP download of RSS XML for all feeds | `components/feed_downloader.py` |
| FeedParser | Parses raw XML into `ParsedFeed` + `Episode` dataclasses | `components/feed_parser.py` |
| FeedPublisher | Writes RSS 2.0 XML output; derives slugs and episode URLs | `components/feed_publisher.py` |
| EpisodeDownloader | Downloads raw audio to cache dir; exposes progress callback | `components/episode_downloader.py` |
| AudioProber | Wraps ffprobe to extract duration, codec, channels, bitrate | `components/audio_prober.py` |
| AudioPreprocessor | Re-encodes to mono WAV for STT; handles oversized files by chunking | `components/audio_preprocessor.py` |
| EpisodeTranscriptor | Sends audio to STT API (LiteLLM); returns Transcription + segments + cost | `components/episode_transcriptor.py` |
| TopicExtractor | LLM call to extract episode topic, hosts, show name | `components/topic_extractor.py` |
| AdDetector | LLM call to classify ad segments in transcript; handles context-window overflow | `components/ad_detector.py` |
| AdParser | Converts raw `AdSegment` list to `CutRange` list applying min-duration and min-confidence filters | `components/ad_parser.py` |
| AudioEditor | Runs ffmpeg atrim+concat to cut detected ads; re-encodes output | `components/audio_editor.py` |
| EpisodeCopier | Copies unmodified audio to output when no cuts qualify | `components/episode_copier.py` |
| Database | Async context manager owning aiosqlite connection + idempotent schema creation | `database/connection.py` |
| *Store classes | Thin DAO wrappers (EpisodeStore, TranscriptionStore, TopicStore, AdStore, AudioMetadataStore, CostTrackingStore) | `database/*.py` |
| Config / Credentials | Pydantic-validated YAML config + env-var credentials (pydantic-settings) | `config/config_loader.py` |
| Ffmpeg | Async subprocess wrapper with progress reporting via `out_time_ms` | `utils/ffmpeg.py` |
| LLM helpers | `compute_completion_cost`, `extract_llm_reasoning` using LiteLLM | `utils/llm.py` |
| Exception hierarchy | `PodcastAdCutterError` base + domain-specific subclasses | `utils/exceptions.py` |
| episode_log | Per-episode file handler attached/detached from root logger | `utils/episode_log.py` |

## Pattern Overview

**Overall:** Pipeline-orchestrated, async, stage-gated state machine with persistent SQLite checkpoints.

**Key Characteristics:**
- `Pipeline` is the only class that touches `Config`; all components receive plain scalars or model instances.
- Each episode advances through a 5-guard `while True` state machine; state is persisted to SQLite after every step, so interrupted runs resume without re-work.
- All I/O (HTTP, subprocess, DB, filesystem) is async throughout.
- Components are stateless classes with constructor-injected dependencies — no module-level singletons beyond loggers.
- Models layer (`models/`) contains only plain `dataclass` and `pydantic.BaseModel` types — zero business logic and zero config imports.

## Layers

**CLI Layer:**
- Purpose: Parse CLI args, load config, configure logging, invoke `Pipeline.run()`
- Location: `main.py`
- Contains: `parse_args`, `configure_logging`, `_rotate_logs`, `main`
- Depends on: `config/`, `components/pipeline.py`, `utils/exceptions.py`
- Used by: OS / Docker entrypoint

**Orchestration Layer:**
- Purpose: Coordinate all stages; own DB lifetime; per-episode state machine
- Location: `components/pipeline.py`
- Contains: `Pipeline`, `_Stores` dataclass
- Depends on: all `components/`, `database/`, `models/`, `config/`, `utils/episode_log.py`
- Used by: `main.py`

**Component Layer:**
- Purpose: Single-responsibility units for each pipeline stage
- Location: `components/*.py` (11 files excluding `pipeline.py`)
- Contains: one class per file with a clear public method (`download_all`, `parse_all`, `publish`, `transcribe`, `detect`, `edit`, etc.)
- Depends on: `models/`, `utils/`, external APIs (LiteLLM, ffmpeg)
- Used by: `Pipeline` only

**Data Model Layer:**
- Purpose: Pure data transfer objects — no business logic, no config imports
- Location: `models/feed.py`, `models/ad_detection.py`, `models/transcription.py`, `models/topic.py`, `models/cost.py`
- Contains: `dataclass` and `pydantic.BaseModel` types
- Depends on: nothing internal
- Used by: all layers

**Database Layer:**
- Purpose: Schema management and async DAO access to SQLite
- Location: `database/connection.py`, `database/ad_store.py`, `database/audio_metadata_store.py`, `database/cost_tracking_store.py`, `database/episode_store.py`, `database/topic_store.py`, `database/transcription_store.py`
- Contains: `Database` context manager + thin `*Store` DAOs
- Depends on: `aiosqlite`, `models/`
- Used by: `Pipeline` only

**Utility Layer:**
- Purpose: Low-level cross-cutting concerns
- Location: `utils/ffmpeg.py`, `utils/llm.py`, `utils/exceptions.py`, `utils/episode_log.py`
- Contains: `Ffmpeg` subprocess wrapper, LiteLLM cost/reasoning helpers, exception hierarchy, per-episode log helpers
- Depends on: `litellm`, stdlib
- Used by: `components/`

**Config Layer:**
- Purpose: YAML loading + env-var credential resolution
- Location: `config/config_loader.py`
- Contains: Pydantic `AppConfig`, `Credentials`, `Config`, `load_config`, `PROVIDER_KEY_MAP`
- Depends on: `pydantic`, `pydantic-settings`, `python-dotenv`, `PyYAML`
- Used by: `main.py` only (Pipeline receives a fully-constructed `Config`)

## Data Flow

### Primary Per-Episode Processing Path

1. **Feed download** — `FeedDownloader.download_all()` fetches RSS XML concurrently (`components/feed_downloader.py`)
2. **Feed parse** — `FeedParser.parse_all()` converts XML → `ParsedFeed` + `Episode` list (`components/feed_parser.py`)
3. **DB persist** — `EpisodeStore.save_episodes()` inserts/updates episode rows; `EpisodeStore.get_episodes_for_feed()` returns the working list (`database/episode_store.py`)
4. **RSS publish** — `FeedPublisher.publish()` writes `output/<slug>/feed.xml` (`components/feed_publisher.py`)
5. **Episode state machine** — `Pipeline._process_episode_until_final()` iterates guards:
   - **Guard 1:** Output file on disk → update URL in DB → `return`
   - **Guard 2:** Ad detection cached in DB → `AdParser.parse()` → `AudioEditor.edit()` (or `EpisodeCopier.copy()`) → `return`
   - **Guard 3:** Topic extracted in DB → `AdDetector.detect()` → save `AdSegment`s → `continue`
   - **Guard 4:** Transcript in DB → `TopicExtractor.extract()` → save `TopicExtraction` → `continue`
   - **Guard 5:** Audio on disk → `AudioProber.probe()` + `AudioPreprocessor.preprocess()` + `EpisodeTranscriptor.transcribe()` → save segments → `continue`
   - **Bottom:** No audio → `EpisodeDownloader.download()` → `continue`
6. **Output trim** — `Pipeline._trim_output_dir()` deletes surplus files beyond `episodes_to_keep`

### LLM Call Pattern (TopicExtractor / AdDetector)

1. Build prompt from transcription segments + episode metadata
2. Call `litellm.acompletion()` with JSON schema response format
3. Parse response → typed Pydantic schema → dataclass
4. Record cost via `utils/llm.compute_completion_cost()`
5. Return `(guid, result, CostRecord)` tuple

**State Management:**
- Per-feed `_Stores` dataclass (defined in `components/pipeline.py`) groups all six DAO instances plus three in-memory GUID sets (`transcribed_guids`, `extracted_guids`, `ad_detected_guids`) loaded once at feed start and mutated as episodes complete each stage — avoids repeated DB round-trips within a feed run.
- Single shared SQLite file at `data/data.db`; connection opened exactly once per `Pipeline.run()` call via `async with Database(self._db_path) as db`.

## Key Abstractions

**Episode GUID:**
- Purpose: Primary key threaded through every layer (DB rows, cache filenames, log filenames, store GUID sets)
- Pattern: String from RSS `<guid>` element; used as FK across all DB tables

**`_Stores` dataclass:**
- Purpose: Groups all six DAO instances plus three GUID sets for one feed run
- Location: `components/pipeline.py` (module-level private dataclass, `slots=True`)
- Pattern: Constructed once per feed, mutated in-place as episodes advance

**`CostRecord` Protocol:**
- Purpose: Structural type for cost tracking — any dataclass with `provider`, `model`, `cost` satisfies it
- Location: `models/cost.py`

**Pydantic LLM response schemas:**
- Purpose: Enforce structured output from LLM JSON mode
- Examples: `AdDetectionResponseSchema` (`models/ad_detection.py`), `TopicExtractionSchema` (`models/topic.py`)

## Entry Points

**CLI:**
- Location: `main.py`
- Triggers: `uv run python main.py [--config ...] [--feed ...] [--output ...] ...`
- Responsibilities: Parse args, load and validate `Config`, configure logging, run `Pipeline.run()`

**Docker:**
- Location: `entrypoint.sh`, `Dockerfile`
- Triggers: Container start
- Responsibilities: Invoke `main.py` inside container

## Architectural Constraints

- **Threading:** Single-threaded asyncio event loop. `asyncio.create_subprocess_exec` used for ffmpeg. No `ThreadPoolExecutor` or worker threads.
- **Global state:** Loggers are module-level (standard Python logging). `ET.register_namespace()` called at import time in `components/feed_publisher.py`. No other mutable module-level state.
- **Config isolation:** No component below `Pipeline` imports from `config/`. Enforced by design and documented in `Pipeline.__init__` docstring.
- **Models isolation:** `models/` has no imports from `components/`, `database/`, or `config/`. Models are pure data.
- **DB lifetime:** `Database` context manager is opened exactly once per `Pipeline.run()` call; all store operations share the same connection.
- **Circular imports:** None detected. Dependency direction is strictly: `main` → `components` → `utils`/`models`; `database` → `models`.

## Anti-Patterns

### Bypassing the state machine with early returns

**What happens:** Adding `await component.method()` calls in `Pipeline.run()` outside `_process_episode_until_final`.
**Why it's wrong:** Episodes resume mid-run only because every step ends with a DB write followed by `continue`. Skipping persistence means re-work or data loss on restart.
**Do this instead:** Add new stages as additional guards inside `_process_episode_until_final` in `components/pipeline.py`, always persisting to a store before `continue`.

### Importing Config inside a component

**What happens:** A component imports `from config.config_loader import Config` to read settings.
**Why it's wrong:** Violates the single-owner rule; creates implicit coupling; makes components harder to test.
**Do this instead:** Add the needed value as a constructor parameter and let `Pipeline.__init__` extract it from `Config`.

## Error Handling

**Strategy:** Domain-specific exceptions raised by components; caught in `Pipeline._process_episode_until_final` via bare `except Exception` (logs and skips the episode). `ConfigError` is caught in `main()` and terminates with `sys.exit(1)`.

**Patterns:**
- `PodcastAdCutterError` is the base (`utils/exceptions.py`); subclasses: `ConfigError`, `AudioProbeError`, `FfmpegError`, `TranscriptionError`, `TopicExtractionError`, `AdDetectionError`
- Internal sentinel exceptions (e.g. `_JsonValidateFailedError`, `_ContextWindowExceededError` in `components/ad_detector.py`) are caught within the component — they never propagate to `Pipeline`

## Cross-Cutting Concerns

**Logging:** Python stdlib `logging`; `logger = logging.getLogger(__name__)` in every module. F-strings only (no `%` operator). `aiosqlite` and `LiteLLM` loggers silenced to WARNING at startup. Optional per-episode FileHandler attached/detached via `utils/episode_log.py`.

**Validation:** All config validated by Pydantic at startup in `config/config_loader.py`. LLM JSON responses validated by Pydantic schemas in `models/`.

**Authentication:** API keys loaded from env vars via `pydantic-settings` (`Credentials` class in `config/config_loader.py`); resolved once in `load_config()` and stored in `Config.credentials`.

---

*Architecture analysis: 2026-05-14*
