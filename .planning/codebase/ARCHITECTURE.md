# Architecture

**Analysis Date:** 2026-03-28

## Pattern Overview

**Overall:** Pipeline + Component orchestration pattern

**Key Characteristics:**
- Async/await throughout using Python 3.12 native coroutines
- Single orchestrator (Pipeline) owns configuration and delegates to stateless components
- Components encapsulate one concern (download, parse, transcribe, etc.)
- Database abstraction layer isolates data access concerns
- Plain dataclass models (Episode, ParsedFeed, etc.) decouple components from config
- Conditional branching on episode state (A, B, C, D) to minimize redundant work

## Layers

**Configuration Layer:**
- Purpose: Load and validate application configuration, environment variables, secrets
- Location: `config/config_loader.py`
- Contains: Pydantic models for AppConfig, FeedConfig, PathsConfig, ModelsConfig, AdDetectionConfig, OutputConfig, LoggingConfig, Credentials
- Depends on: pydantic, pydantic-settings, python-dotenv, PyYAML
- Used by: Pipeline (sole owner)

**Model/Domain Layer:**
- Purpose: Plain data structures for passing data between components
- Location: `models/feed.py`, `models/transcription.py`, `models/topic.py`
- Contains: Episode, ParsedFeed, PublisherInput, Transcription, TranscriptionSegment, AudioMetadata
- Depends on: dataclasses, datetime
- Used by: All components and database stores

**Component Layer (Business Logic):**
- Purpose: Encapsulate single-concern operations (download, parse, transcribe, etc.)
- Location: `components/` directory
- Contains: FeedDownloader, FeedParser, EpisodeDownloader, AudioProber, AudioPreprocessor, EpisodeTranscriptor, TopicExtractor, FeedPublisher, EpisodeCopier
- Depends on: aiohttp, litellm, external utilities (ffmpeg via subprocess)
- Used by: Pipeline

**Database Layer:**
- Purpose: Persist and retrieve episodes, transcriptions, audio metadata, costs, topics
- Location: `database/` directory
- Contains: Database (connection manager), EpisodeStore, TranscriptionStore, AudioMetadataStore, CostTrackingStore, TopicStore
- Depends on: aiosqlite
- Used by: Pipeline

**Orchestration Layer:**
- Purpose: Coordinate the entire workflow, manage state, apply decision tree for episode processing
- Location: `components/pipeline.py`
- Contains: Pipeline class
- Depends on: All component classes, Database, Config
- Used by: main.py

**Utilities Layer:**
- Purpose: Shared error types, ffmpeg subprocess wrapping
- Location: `utils/`
- Contains: exceptions.py, ffmpeg.py
- Depends on: subprocess
- Used by: Components

## Data Flow

**Main Workflow:**

1. **Configuration Loading** (main.py → config_loader.py)
   - Load YAML config file
   - Load environment variables for API credentials
   - Validate and return Config object

2. **Pipeline Initialization** (main.py → Pipeline.__init__)
   - Instantiate all components with extracted config fields
   - Store config as sole configuration source

3. **Feed Download Phase** (Pipeline.run → FeedDownloader)
   - Collect (title, url) pairs from selected feeds
   - Fetch RSS/Atom XML for each feed in parallel using aiohttp.ClientSession
   - Return (title, xml_text) tuples

4. **Feed Parsing Phase** (Pipeline → FeedParser)
   - Parse each XML blob into ParsedFeed with Episode list
   - Extract channel-level and episode-level metadata
   - Handle iTunes and content:encoded namespace extensions

5. **Episode Storage Phase** (Pipeline → Database/EpisodeStore)
   - Persist parsed episodes to SQLite
   - Subsequent runs use stored episodes and only fetch N most recent (episodes_to_keep)

6. **Episode Processing Decision Tree** (Pipeline._process_episode)
   - **Branch A:** Both transcription and output audio exist → reconstruct URL only
   - **Branch B:** Transcription exists, no audio → download → probe → preprocess → copy
   - **Branch C:** Audio exists, no transcription → probe → preprocess → transcribe → extract topics
   - **Branch D:** Neither exists → download → probe → preprocess → transcribe → extract topics → copy

7. **Episode Processing Sub-steps:**
   - **Download:** EpisodeDownloader fetches raw audio to cache
   - **Probe:** AudioProber uses ffprobe to extract codec, channels, bitrate, duration
   - **Preprocess:** AudioPreprocessor converts to mono 16-bit PCM using ffmpeg
   - **Transcribe:** EpisodeTranscriptor sends mono file to LLM (groq/openai/openrouter) via litellm
   - **Extract Topics:** TopicExtractor sends transcript + metadata to LLM for context extraction
   - **Copy:** EpisodeCopier converts preprocessed audio to output format (AAC/MP3) and places in output structure
   - **Publish:** FeedPublisher generates RSS XML feed with updated URLs

**State Management:**
- Episode state tracked via sets: `transcribed_guids` (in database) and `extracted_guids` (in database)
- Decision tree checks existing_audio (filesystem) vs transcription_exists (database)
- No mid-flight state; each episode processing is independent and idempotent
- Database open for full episode batch to avoid connection churn

## Key Abstractions

**Episode (dataclass):**
- Purpose: Represents a single podcast episode with all RSS/iTunes metadata
- Examples: `models/feed.py` line 15-40
- Pattern: Plain dataclass with Optional fields for extended metadata, default pub_date to now()

**ParsedFeed (dataclass):**
- Purpose: Result of parsing one RSS feed; carries channel + episode metadata separately
- Examples: `models/feed.py` line 98-128
- Pattern: Parallel structure to Episode with channel-level fields (title, description, image_url, categories, owner_name, etc.)

**Pipeline (class):**
- Purpose: Single orchestrator managing config and delegating to components
- Examples: `components/pipeline.py`
- Pattern: Constructor initializes all component instances; run() method orchestrates workflow; _process_episode() applies decision tree; helper methods for feed selection, download, parse input building

**Store (ABC-like pattern):**
- Purpose: Encapsulate database access for a single entity type
- Examples: EpisodeStore, TranscriptionStore, AudioMetadataStore, CostTrackingStore, TopicStore in `database/`
- Pattern: Each store owns one table; methods like `save_episodes()`, `get_episodes_for_feed()`, `get_transcribed_guids()` expose the store's contract; receives aiosqlite.Connection from Pipeline

**Component (pattern):**
- Purpose: Single-concern operation (e.g., download, parse, transcribe)
- Examples: FeedDownloader, FeedParser, EpisodeDownloader, AudioProber, EpisodeTranscriptor
- Pattern: Stateless (all parameters passed to methods, not stored); accepts extracted config fields in __init__, no dependency on Config object; returns plain dataclass results

## Entry Points

**main.py:**
- Location: `/home/oton/projects/podcast-ad-cutter/main.py`
- Triggers: User runs `uv run python main.py [--config CONFIG] [--feed FEED] [--debug] [--log-to-file]`
- Responsibilities: Parse CLI args, load config with error handling, configure logging, instantiate Pipeline, invoke pipeline.run(), catch and report exceptions

**Pipeline.run():**
- Location: `components/pipeline.py` line 82
- Triggers: Invoked by main.py
- Responsibilities: Select feeds (all enabled or one by name), download, parse, store episodes, iterate episodes with decision tree, update URLs, publish feeds; return list of ParsedFeed

**Pipeline._process_episode():**
- Location: `components/pipeline.py` line 178
- Triggers: Invoked by Pipeline.run() for each episode
- Responsibilities: Check transcription and audio existence, apply decision tree branch (A-D), call appropriate sequence of component methods, update database and feed publisher

## Error Handling

**Strategy:** Exceptions bubble up; Pipeline catches episode-level exceptions to skip and continue; Config/setup errors abort early

**Patterns:**
- `ConfigError` (utils/exceptions.py): Config validation or loading failure → sys.exit(1) in main.py
- `TranscriptionError` (utils/exceptions.py): Transcription via litellm fails → logged, episode skipped, pipeline continues
- `Exception` in _process_episode → `logger.exception()` and continue to next episode
- HTTP errors in FeedDownloader._fetch_one() → logged at WARNING, feed skipped
- ffmpeg failures captured in FfmpegError with stderr text (utils/exceptions.py)

## Cross-Cutting Concerns

**Logging:** Structured via standard logging module with f-strings (see main.py lines 73-98 for config); libraries like aiosqlite and LiteLLM kept at WARNING to avoid noise; each component logs entry/debug/completion

**Validation:** Pydantic handles config validation; FeedParser validates XML and feeds (logs WARNING on failure, omits from results); component methods accept already-validated inputs (no runtime validation)

**Authentication:** Credentials loaded from environment variables (GROQ_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY) via pydantic-settings; passed as api_key parameter to EpisodeTranscriptor and TopicExtractor; never logged

**Async/Await:** All I/O operations are async: aiohttp (HTTP), aiosqlite (database), litellm.atranscription (STT), ffmpeg subprocess via subprocess.run (blocking but not I/O-bound); Pipeline.run() is async; all database operations are async

---

*Architecture analysis: 2026-03-28*
