# Structure

## Directory Layout

```
podcast-ad-cutter/
├── main.py                        # CLI entry point, logging setup, Pipeline instantiation
├── config.example.yaml            # Documented config template
├── .env.example                   # Credential env var template
├── pyproject.toml                 # Project metadata, deps, ruff/mypy/pytest config
│
├── components/                    # Pipeline stage components (one class per module)
│   ├── pipeline.py                # Orchestrator — the only file that imports config/
│   ├── feed_downloader.py         # Downloads RSS XML via aiohttp
│   ├── feed_parser.py             # Parses RSS XML to ParsedFeed/Episode models
│   ├── feed_publisher.py          # Writes and updates output RSS XML files
│   ├── episode_downloader.py      # Downloads episode audio to cache dir
│   ├── episode_transcriptor.py    # STT via Groq/litellm (chunked for >25 MB)
│   ├── episode_copier.py          # Copies original audio to output when no ads found
│   ├── topic_extractor.py         # LLM-based episode topic/context extraction
│   ├── ad_detector.py             # LLM-based ad segment detection with retry logic
│   ├── ad_parser.py               # Converts AdSegment → CutRange (filters by duration/confidence)
│   ├── audio_prober.py            # ffprobe wrapper — extracts audio metadata
│   ├── audio_preprocessor.py      # ffmpeg wrapper — converts to mono AAC
│   ├── audio_editor.py            # ffmpeg wrapper — cuts ads, produces clean output
│   └── __init__.py
│
├── config/
│   ├── config_loader.py           # YAML loading, Pydantic models, env var credentials
│   └── __init__.py
│
├── database/                      # aiosqlite stores — one class per entity table
│   ├── connection.py              # Database context manager, schema init + migrations
│   ├── episode_store.py           # Episodes CRUD + URL update
│   ├── transcription_store.py     # Transcription text + segments
│   ├── audio_metadata_store.py    # ffprobe metadata (duration, codec, etc.)
│   ├── topic_store.py             # Topic extraction results
│   ├── ad_store.py                # Ad segments + detection completion flag
│   ├── cost_tracking_store.py     # Per-call LLM cost records
│   └── __init__.py
│
├── models/                        # Pydantic domain models (no DB logic)
│   ├── feed.py                    # Episode, ParsedFeed, FeedParseInput, PublisherInput, AudioMetadata
│   ├── transcription.py           # Transcription, TranscriptionSegment, TranscriptionCost
│   ├── topic.py                   # TopicExtraction, TopicExtractionCost
│   ├── ad_detection.py            # AdSegmentDetection, AdSegment, CutRange, AdDetectionCost
│   ├── cost.py                    # Shared cost model base
│   └── __init__.py
│
├── utils/
│   ├── ffmpeg.py                  # Low-level ffmpeg/ffprobe subprocess helpers
│   ├── llm.py                     # LiteLLM cost computation, reasoning extraction helpers
│   ├── episode_log.py             # Per-episode log file rotation helpers
│   ├── exceptions.py              # Custom exception hierarchy
│   └── __init__.py
│
└── tests/                         # pytest test suite (mirrors source structure)
    ├── test_pipeline.py           # Largest file — full state machine integration tests
    ├── test_<component>.py        # One test file per source module
    ├── test_<model_group>.py      # Pydantic model validation tests
    └── __init__.py
```

## Key Entry Points

| File | Purpose |
|------|---------|
| `main.py` | `uv run python main.py` — parses CLI args, loads config, runs `Pipeline.run()` |
| `components/pipeline.py` | `Pipeline.run()` — the top-level async orchestration method |
| `database/connection.py` | `Database` async context manager — creates tables + runs migrations on first use |

## Configuration Loading

1. `main.py` calls `load_config(path)` with the YAML path (default: `config.yaml`)
2. `config_loader.load_config()`:
   - Loads `.env` via `python-dotenv`
   - Reads YAML via `pyyaml`
   - Validates structure with Pydantic `BaseModel` (raises `ConfigError` on failure)
   - Loads credentials from env vars via `pydantic-settings BaseSettings`
3. The resulting `Config` object is passed to `Pipeline.__init__` and never passed further down

## Where to Add New Code

| Addition | Location |
|----------|----------|
| New pipeline stage | `components/<verb><noun>.py` + constructor injection in `Pipeline.__init__` |
| New DB entity | `database/<entity>_store.py` + schema migration in `database/connection.py` |
| New domain model | `models/<domain>.py` |
| New config section | `config/config_loader.py` (add Pydantic model + wire into `AppConfig`) |
| New CLI flag | `main.py:parse_args()` + pass through to `Pipeline` or config override |
| New utility | `utils/<name>.py` |
| Tests | `tests/test_<module>.py` mirroring the source path |
