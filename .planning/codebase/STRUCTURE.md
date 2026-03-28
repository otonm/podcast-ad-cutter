# Codebase Structure

**Analysis Date:** 2026-03-28

## Directory Layout

```
podcast-ad-cutter/
├── main.py                          # CLI entry point, argument parsing, logging setup
├── pyproject.toml                   # Metadata, dependencies, tool configuration
├── config.example.yaml              # Template for config.yaml (user creates this)
├── .env.example                     # Template for .env (user creates this)
├── config/                          # Configuration loading and validation
│   ├── config_loader.py             # Pydantic models and YAML/env loading
│   └── __init__.py
├── components/                      # Business logic — stateless single-concern operations
│   ├── pipeline.py                  # Orchestrator: coordinates all components and workflow
│   ├── feed_downloader.py           # Fetches RSS/Atom XML via aiohttp
│   ├── feed_parser.py               # Parses XML into Episode and ParsedFeed objects
│   ├── episode_downloader.py        # Downloads episode audio files to cache/
│   ├── audio_prober.py              # Extracts audio metadata via ffprobe
│   ├── audio_preprocessor.py        # Converts audio to mono 16-bit PCM via ffmpeg
│   ├── episode_transcriptor.py      # Sends audio to LLM for speech-to-text via litellm
│   ├── topic_extractor.py           # Extracts podcast topics/context via LLM
│   ├── feed_publisher.py            # Generates output RSS XML feed with updated URLs
│   ├── episode_copier.py            # Converts preprocessed audio to output format and places it
│   └── __init__.py
├── database/                        # Data persistence — SQLite tables and store classes
│   ├── connection.py                # Database context manager, schema definitions
│   ├── episode_store.py             # Persists/retrieves Episode records
│   ├── transcription_store.py       # Persists/retrieves Transcription and TranscriptionSegment
│   ├── audio_metadata_store.py      # Persists/retrieves AudioMetadata (ffprobe results)
│   ├── cost_tracking_store.py       # Persists/retrieves LLM API costs
│   ├── topic_store.py               # Persists/retrieves extracted topics
│   └── __init__.py
├── models/                          # Domain objects (dataclasses) — no config dependency
│   ├── feed.py                      # Episode, ParsedFeed, FeedParseInput, PublisherInput, AudioMetadata
│   ├── transcription.py             # Transcription, TranscriptionSegment, TranscriptionCost
│   ├── topic.py                     # Topic, TopicCost
│   └── __init__.py
├── utils/                           # Shared utilities
│   ├── exceptions.py                # Custom exception types (ConfigError, TranscriptionError, FfmpegError)
│   ├── ffmpeg.py                    # ffmpeg/ffprobe subprocess wrapper
│   └── __init__.py
├── tests/                           # Test suite (23 files, ~7000 lines, 100% coverage)
│   ├── test_pipeline.py             # Pipeline orchestration tests
│   ├── test_feed_downloader.py      # FeedDownloader tests
│   ├── test_feed_parser.py          # FeedParser tests, XML parsing edge cases
│   ├── test_feed_parser_integration.py  # Full XML + DB round-trip tests
│   ├── test_episode_downloader.py   # EpisodeDownloader tests
│   ├── test_audio_prober.py         # AudioProber tests
│   ├── test_audio_preprocessor.py   # AudioPreprocessor tests
│   ├── test_episode_transcriptor.py # EpisodeTranscriptor tests with mock litellm
│   ├── test_episode_copier.py       # EpisodeCopier tests
│   ├── test_feed_publisher.py       # FeedPublisher tests
│   ├── test_database_connection.py  # Database schema and context manager tests
│   ├── test_episode_store.py        # EpisodeStore CRUD tests
│   ├── test_transcription_store.py  # TranscriptionStore tests
│   ├── test_audio_metadata_store.py # AudioMetadataStore tests
│   ├── test_cost_tracking_store.py  # CostTrackingStore tests
│   ├── test_topic_store.py          # TopicStore tests
│   ├── test_config_loader.py        # Config loading, validation, error cases
│   ├── test_exceptions.py           # Exception types and error messages
│   ├── test_ffmpeg.py               # ffmpeg wrapper tests
│   ├── test_transcription_models.py # Transcription model serialization
│   ├── test_topic_extractor.py      # TopicExtractor tests with mock litellm
│   ├── test_main.py                 # main.py entry point and CLI parsing
│   ├── static/                      # Test fixtures (sample XML files, etc.)
│   └── __init__.py
├── cache/                           # Downloaded episode audio files (gitignore)
├── data/                            # SQLite database file (gitignore)
├── logs/                            # Timestamped log files when --log-to-file is set
├── output/                          # Generated RSS feeds and output audio files (gitignore)
└── .planning/                       # GSD planning documents (generated)
    └── codebase/                    # Analysis documents
```

## Directory Purposes

**config/:**
- Purpose: Load and validate configuration from YAML files and environment
- Contains: Pydantic model classes (FeedConfig, PathsConfig, ModelsConfig, Credentials, AppConfig)
- Key files: `config_loader.py` (PROVIDER_KEY_MAP, load_config, Config class)

**components/:**
- Purpose: Core business logic — stateless single-concern operations
- Contains: 10 component classes, each handling one stage of the workflow
- Key files: `pipeline.py` (orchestrator), `feed_downloader.py`, `feed_parser.py`, `episode_downloader.py`, `audio_prober.py`, `audio_preprocessor.py`, `episode_transcriptor.py`, `topic_extractor.py`, `feed_publisher.py`, `episode_copier.py`

**database/:**
- Purpose: Persist and retrieve data from SQLite
- Contains: Connection manager, 5 store classes (one per table type)
- Key files: `connection.py` (Database context manager, schema definitions), `episode_store.py`, `transcription_store.py`, `audio_metadata_store.py`, `cost_tracking_store.py`, `topic_store.py`

**models/:**
- Purpose: Plain domain objects for passing data between components
- Contains: Dataclass definitions (Episode, ParsedFeed, Transcription, etc.)
- Key files: `feed.py` (Episode, ParsedFeed, PublisherInput, FeedParseInput), `transcription.py`, `topic.py`

**utils/:**
- Purpose: Shared utilities and custom exceptions
- Contains: Exception classes, ffmpeg subprocess wrapper
- Key files: `exceptions.py` (ConfigError, TranscriptionError, FfmpegError), `ffmpeg.py`

**tests/:**
- Purpose: Test suite with 100% coverage requirement
- Contains: 23 test files (~7000 lines), one per component or module, plus integration tests
- Key files: `test_pipeline.py` (main orchestration), `test_config_loader.py` (config validation), `test_main.py` (CLI), `test_feed_parser_integration.py` (XML + DB round-trip)

## Key File Locations

**Entry Points:**
- `main.py`: Top-level CLI; invokes Pipeline.run()
- `components/pipeline.py` (Pipeline.run()): Main async workflow orchestrator

**Configuration:**
- `config/config_loader.py`: Config model definitions and load_config() function
- `config.example.yaml`: User template (checked in)
- `.env.example`: User template (checked in)

**Core Logic:**
- `components/pipeline.py`: Decision tree for episode processing (branches A-D)
- `components/feed_parser.py`: XML parsing (RSS, iTunes, content:encoded namespaces)
- `components/episode_transcriptor.py`: litellm API integration
- `components/topic_extractor.py`: Context extraction via LLM
- `components/audio_preprocessor.py`: ffmpeg audio conversion
- `components/feed_publisher.py`: RSS XML generation

**Database:**
- `database/connection.py`: Schema definitions (_EPISODES_SCHEMA, _TRANSCRIPTIONS_SCHEMA, etc.)
- `database/episode_store.py`: Episode CRUD (save_episodes, get_episodes_for_feed, update_episode_url)
- `database/transcription_store.py`: Transcription and segment storage

## Naming Conventions

**Files:** `snake_case.py` — `feed_downloader.py`, `episode_transcriptor.py`

**Classes:** `PascalCase`, one class per file (except `config_loader.py` with multiple Pydantic models) — `Pipeline`, `FeedDownloader`, `EpisodeTranscriptor`

**Functions:** `snake_case` — `load_config()`, `parse_all()`, `transcribe()`, `save_episodes()`

**Private attributes/methods:** `_leading_underscore` — `self._config`, `self._feed_downloader`

**Constants:** `UPPER_SNAKE_CASE` module-level; `_UPPER_SNAKE_CASE` for private — `PROVIDER_KEY_MAP`, `_EPISODES_SCHEMA`, `_ITUNES`

## Where to Add New Code

**New Component (e.g., AdDetector):**
- Implementation: `components/ad_detector.py`
- Class: `AdDetector` with async methods
- Usage: Add to `Pipeline.__init__()`, integrate into `_process_episode()` branching
- Tests: `tests/test_ad_detector.py`

**New Database Table:**
- Schema: Add `_YOUR_TABLE_SCHEMA` constant to `database/connection.py`
- Store class: Create `database/your_store.py`
- Tests: `tests/test_your_store.py`
- Model: Add dataclass to `models/feed.py` or new `models/your_model.py`

**New Configuration Field:**
- Model: Add field to appropriate Pydantic model in `config/config_loader.py`
- Template: Add to `config.example.yaml`
- Tests: Add to `tests/test_config_loader.py`

---

*Structure analysis: 2026-03-28*
