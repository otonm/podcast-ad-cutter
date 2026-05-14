# Codebase Structure

**Analysis Date:** 2026-05-14

## Directory Layout

```
podcast-ad-cutter/
├── main.py                        # CLI entry point; logging setup; calls Pipeline.run()
├── config.yaml                    # Active config (gitignored); see config.example.yaml
├── config.example.yaml            # Template config with all fields documented
├── pyproject.toml                 # Project metadata, dependencies, tool config (ruff, pytest, coverage)
├── Dockerfile                     # Production container image
├── docker-compose.example.yml     # Example Compose file for deployment
├── entrypoint.sh                  # Docker entrypoint script
├── run.sh                         # Local run helper
├── AGENTS.md                      # Agent guidance
├── CLAUDE.md                      # Project instructions for Claude
├── TODO.md                        # In-progress notes
│
├── components/                    # One class per pipeline stage
│   ├── pipeline.py                # Pipeline orchestrator + _Stores dataclass
│   ├── feed_downloader.py         # Async concurrent RSS XML download
│   ├── feed_parser.py             # XML → ParsedFeed + Episode
│   ├── feed_publisher.py          # Write RSS 2.0 output + episode URL helpers
│   ├── episode_downloader.py      # Download raw audio to cache dir
│   ├── audio_prober.py            # ffprobe wrapper → AudioMetadata
│   ├── audio_preprocessor.py      # Re-encode to mono WAV; chunk oversized files
│   ├── episode_transcriptor.py    # STT via LiteLLM → Transcription + segments
│   ├── topic_extractor.py         # LLM → TopicExtraction
│   ├── ad_detector.py             # LLM → AdSegmentDetection list
│   ├── ad_parser.py               # AdSegment list → CutRange list (filter by duration/confidence)
│   ├── audio_editor.py            # ffmpeg atrim+concat to cut ads
│   ├── episode_copier.py          # Copy unmodified audio to output when no cuts
│   └── __init__.py
│
├── models/                        # Pure data transfer objects — no business logic
│   ├── feed.py                    # Episode, ParsedFeed, FeedParseInput, PublisherInput, AudioMetadata
│   ├── ad_detection.py            # AdSegment, AdSegmentDetection, AdDetectionResponseSchema, CutRange, AdDetectionCost
│   ├── transcription.py           # Transcription, TranscriptionSegment, TranscriptionCost
│   ├── topic.py                   # TopicExtraction, TopicExtractionSchema, TopicExtractionCost
│   ├── cost.py                    # CostRecord protocol (structural type)
│   └── __init__.py
│
├── database/                      # Async SQLite persistence
│   ├── connection.py              # Database async context manager; schema DDL; migrations
│   ├── episode_store.py           # episodes table DAO
│   ├── transcription_store.py     # transcriptions + transcription_segments tables DAO
│   ├── topic_store.py             # topic_extractions table DAO
│   ├── ad_store.py                # ad_segments + ad_detection_runs tables DAO
│   ├── audio_metadata_store.py    # episode_audio_metadata table DAO
│   ├── cost_tracking_store.py     # cost_tracking table DAO
│   └── __init__.py
│
├── config/                        # Config loading and validation
│   ├── config_loader.py           # load_config(), AppConfig, Credentials, Config, PROVIDER_KEY_MAP
│   └── __init__.py
│
├── utils/                         # Low-level cross-cutting helpers
│   ├── ffmpeg.py                  # Async ffmpeg subprocess wrapper with progress reporting
│   ├── llm.py                     # compute_completion_cost, extract_llm_reasoning (LiteLLM)
│   ├── exceptions.py              # PodcastAdCutterError hierarchy
│   ├── episode_log.py             # Per-episode FileHandler attach/detach + rotation
│   └── __init__.py
│
├── tests/                         # All tests (co-located with repo root, not inside packages)
│   ├── static/                    # Static test fixtures (audio files, XML samples)
│   ├── test_pipeline.py
│   ├── test_feed_downloader.py
│   ├── test_feed_parser.py
│   ├── test_feed_parser_integration.py
│   ├── test_feed_publisher.py
│   ├── test_episode_downloader.py
│   ├── test_audio_prober.py
│   ├── test_audio_preprocessor.py
│   ├── test_episode_transcriptor.py
│   ├── test_topic_extractor.py
│   ├── test_ad_detector.py
│   ├── test_ad_parser.py
│   ├── test_audio_editor.py
│   ├── test_episode_copier.py
│   ├── test_ad_store.py
│   ├── test_audio_metadata_store.py
│   ├── test_cost_tracking_store.py
│   ├── test_episode_store.py
│   ├── test_topic_store.py
│   ├── test_transcription_store.py
│   ├── test_database_connection.py
│   ├── test_config_loader.py
│   ├── test_ad_detection_models.py
│   ├── test_cost_models.py
│   ├── test_transcription_models.py
│   ├── test_ffmpeg.py
│   ├── test_llm.py
│   ├── test_episode_log.py
│   ├── test_exceptions.py
│   └── test_main.py
│
├── .planning/                     # GSD planning artefacts (not committed to output)
│   └── codebase/                  # Codebase map documents
│
└── .claude/                       # Claude agent skills and settings
    ├── settings.json
    └── skills/
        └── python-code-review/
```

## Directory Purposes

**`components/`:**
- Purpose: One Python class per pipeline stage. Each file is self-contained and has a single clear public method.
- Contains: `Pipeline` (orchestrator) + 11 single-purpose component classes
- Key files: `pipeline.py` (state machine), `ad_detector.py` (LLM), `audio_editor.py` (ffmpeg cuts)

**`models/`:**
- Purpose: Shared data transfer objects. Zero business logic, zero internal imports.
- Contains: `dataclass` types for domain objects; `pydantic.BaseModel` types for LLM response schemas
- Key files: `feed.py` (Episode, ParsedFeed), `ad_detection.py` (AdSegment, CutRange)

**`database/`:**
- Purpose: All SQLite persistence. Schema DDL lives in `connection.py`; each store is a thin DAO.
- Contains: `Database` context manager + 6 `*Store` classes
- Key files: `connection.py` (schema + migrations), `episode_store.py` (primary episode table)

**`config/`:**
- Purpose: YAML config loading and Pydantic validation. Single file.
- Key files: `config_loader.py` (all config models, `load_config`)

**`utils/`:**
- Purpose: Low-level cross-cutting helpers shared across components.
- Contains: ffmpeg wrapper, LiteLLM helpers, exception hierarchy, per-episode log utilities
- Key files: `ffmpeg.py`, `exceptions.py`

**`tests/`:**
- Purpose: All test files, one per module. Static fixtures in `tests/static/`.
- Contains: 30+ test files; one `test_<module>.py` per source file

## Key File Locations

**Entry Points:**
- `main.py`: CLI entry; calls `load_config` then `Pipeline.run()`

**Configuration:**
- `config/config_loader.py`: All Pydantic config models and `load_config()`
- `config.example.yaml`: Canonical reference for all YAML config keys
- `pyproject.toml`: Dependencies, ruff rules, pytest settings, coverage config

**Core Logic:**
- `components/pipeline.py`: Entire orchestration flow and per-episode state machine
- `components/ad_detector.py`: LLM-based ad detection with context-window handling
- `components/audio_editor.py`: ffmpeg ad-cutting logic

**Database Schema:**
- `database/connection.py`: All table DDL and inline migrations (ALTER TABLE)

**Testing:**
- `tests/`: All test files; `tests/static/` for fixtures

## Naming Conventions

**Files:**
- Components: `<noun>_<verb>.py` or `<noun>_<noun>.py` (e.g. `feed_downloader.py`, `audio_editor.py`)
- Models: `<domain>.py` (e.g. `feed.py`, `ad_detection.py`)
- Database stores: `<entity>_store.py` (e.g. `episode_store.py`, `ad_store.py`)
- Tests: `test_<module_name>.py` matching the source file exactly

**Classes:**
- Components: PascalCase noun phrases (e.g. `FeedDownloader`, `AudioEditor`, `EpisodeTranscriptor`)
- Stores: PascalCase `*Store` suffix (e.g. `EpisodeStore`, `AdStore`)
- Models: PascalCase noun (e.g. `Episode`, `AdSegment`, `TopicExtraction`)
- Config models: PascalCase `*Config` suffix (e.g. `FeedConfig`, `LLMConfig`, `AppConfig`)

**Directories:**
- Lowercase, singular nouns (`components`, `models`, `database`, `config`, `utils`, `tests`)

## Where to Add New Code

**New pipeline stage (e.g. a new ML processing step):**
- Implementation: `components/<noun>_<verb>.py` — one class with a clear public async method
- Constructor: accept only plain scalars or model instances (no `Config`)
- Wire-up: add to `Pipeline.__init__` and `Pipeline._process_episode_until_final` as a new guard
- Models: add result dataclass to appropriate `models/<domain>.py`
- DB persistence: add table DDL to `database/connection.py` + new `database/<entity>_store.py`
- Tests: `tests/test_<component>.py` + `tests/test_<store>.py`

**New component (non-stage utility class):**
- Implementation: `components/<name>.py`
- Tests: `tests/test_<name>.py`

**New shared utility:**
- Location: `utils/<name>.py`
- Tests: `tests/test_<name>.py`

**New config field:**
- Add to the appropriate Pydantic model in `config/config_loader.py`
- Update `config.example.yaml` with the new key and a comment
- Extract in `Pipeline.__init__` and pass as constructor arg to the relevant component

**New database table:**
- Add DDL constant + `CREATE TABLE` call in `database/connection.py`
- Add `*Store` DAO class in `database/<entity>_store.py`
- Tests: `tests/test_<entity>_store.py`

## Special Directories

**`.planning/codebase/`:**
- Purpose: GSD codebase map documents (ARCHITECTURE.md, STRUCTURE.md, etc.)
- Generated: Yes (by GSD mapping agent)
- Committed: Yes

**`.claude/`:**
- Purpose: Claude agent skills and project settings
- Generated: No
- Committed: Yes

**`data/`** (runtime, not in repo):
- Purpose: SQLite database file (`data.db`)
- Generated: Yes, at runtime
- Committed: No

**`cache/`** (runtime, not in repo):
- Purpose: Temporary downloaded audio files; cleaned up after each episode
- Generated: Yes, at runtime
- Committed: No

**`output/`** (runtime, not in repo):
- Purpose: Processed audio files and `feed.xml` per feed slug
- Generated: Yes, at runtime
- Committed: No

**`logs/`** (runtime, not in repo):
- Purpose: Timestamped run logs; optional per-episode logs in `logs/episodes/<feed-slug>/`
- Generated: Yes, at runtime
- Committed: No

---

*Structure analysis: 2026-05-14*
