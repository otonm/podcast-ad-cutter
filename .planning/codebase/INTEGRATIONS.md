# External Integrations

**Analysis Date:** 2026-05-14

## APIs & External Services

**Speech-to-Text (transcription):**
- Groq Whisper (default: `whisper-large-v3`) — primary STT provider.
  - SDK/Client: `litellm.atranscription` (`components/episode_transcriptor.py`)
  - Auth env var: `GROQ_API_KEY`
  - Notes: Groq enforces a 25 MB per-request limit. Files exceeding this are split into chunks by `ffmpeg` and results merged. See `components/episode_transcriptor.py`, constants `_GROQ_MAX_BYTES` and `_CHUNK_DURATION_SECS`.

**Chat LLM (ad detection + topic extraction):**
- Groq (default: `llama-3.3-70b-versatile`), OpenAI, or OpenRouter — configured per pipeline stage.
  - SDK/Client: `litellm.acompletion` (`components/ad_detector.py`, `components/topic_extractor.py`)
  - Auth env vars: `GROQ_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`
  - Notes: Provider is selected per stage via `config.yaml` `models.*` keys. LiteLLM constructs model identifiers as `provider/model` for non-OpenAI providers.

**LiteLLM (provider abstraction layer):**
- `litellm>=1.83.0` — wraps Groq, OpenAI, and OpenRouter under a single API.
  - Used in: `components/episode_transcriptor.py`, `components/ad_detector.py`, `components/topic_extractor.py`, `utils/llm.py`
  - Features used: `atranscription`, `acompletion`, `token_counter`, `model_cost`, `get_model_info`, `supports_reasoning`, `completion_cost`

## Data Storage

**Databases:**
- SQLite (via `aiosqlite>=0.22.1`) — all persistence.
  - Connection: `database/connection.py` (`DatabaseConnection` class)
  - Path: configured via `paths.data_dir` in `config.yaml`
  - Tables: `episodes`, `transcriptions`, `transcription_segments`, `ads`, `topics`, `audio_metadata`, `cost_tracking`
  - Store modules: `database/episode_store.py`, `database/transcription_store.py`, `database/ad_store.py`, `database/topic_store.py`, `database/audio_metadata_store.py`, `database/cost_tracking_store.py`

**File Storage:**
- Local filesystem only.
  - `paths.output_dir` — processed audio files and generated RSS feeds.
  - `paths.cache_dir` — downloaded raw episode audio.
  - `paths.data_dir` — SQLite database file.
  - `paths.log_dir` — application and per-episode log files.

**Caching:**
- No in-memory or distributed cache. Episode audio is cached as files in `paths.cache_dir`. SQLite stores all processed metadata to avoid redundant API calls.

## Authentication & Identity

**Auth Provider:**
- No user authentication — this is a batch CLI application with no user accounts.

**API Key Management:**
- All LLM provider keys read from environment variables at startup.
- `config/config_loader.py` validates that keys are present for all providers referenced in `config.yaml` before allowing the pipeline to start.
- `PROVIDER_KEY_MAP` in `config/config_loader.py` maps provider names to their env var names.
- Keys are passed directly to `litellm` per-call via the `api_key=` parameter.

## Podcast Feed Sources

**Incoming RSS/Atom feeds:**
- HTTP GET to arbitrary podcast feed URLs listed in `config.yaml` `feeds[].url`.
- Client: `aiohttp.ClientSession` (`components/feed_downloader.py`)
- No authentication — assumes public feeds.
- Parsed with `xml.etree.ElementTree` (stdlib) in `components/feed_parser.py`.

**Podcast episode audio:**
- HTTP GET to episode `<enclosure>` URLs found in feed XML.
- Client: `aiohttp.ClientSession` (`components/episode_downloader.py`)
- Streamed to disk in chunks.
- No authentication — assumes public media URLs.

## Output — RSS Feed Publishing

**Outgoing RSS feed:**
- `components/feed_publisher.py` writes an RSS 2.0 XML file to `paths.output_dir/{feed-slug}.rss`.
- Feed `<enclosure>` URLs are constructed using `base_url` from `config.yaml` plus the relative audio file path.
- No push/webhook — the caller is responsible for serving the output directory over HTTP (e.g. nginx, Python's `http.server`).

## Audio Processing (external binary)

**ffmpeg / ffprobe:**
- Invoked as subprocesses via `utils/ffmpeg.py` and `components/audio_prober.py`.
- `ffmpeg` — converts downloaded audio to mono AAC for transcription (`components/audio_preprocessor.py`), cuts ad segments and re-encodes output (`components/audio_editor.py`), chunks oversized audio files (`components/episode_transcriptor.py`).
- `ffprobe` — reads audio duration and codec metadata (`components/audio_prober.py`).
- No API key required; must be installed in `PATH`.

## Monitoring & Observability

**Error Tracking:**
- None — no external error tracking service detected.

**Logs:**
- Python `logging` module throughout.
- Configurable via `config.yaml` `log.*`: level, optional file output, rotation, and per-episode log files.
- Per-episode logs written to `logs/episodes/<feed-slug>/<episode-slug>.<datetime>.log` when `log.per_episode: true` (`utils/episode_log.py`).
- LiteLLM and aiosqlite loggers explicitly suppressed to `WARNING` level at startup (`main.py`).

## CI/CD & Deployment

**Hosting:**
- Not detected — no deployment config, Dockerfile, or CI pipeline files found.

**CI Pipeline:**
- Not detected.

## Webhooks & Callbacks

**Incoming:**
- None.

**Outgoing:**
- None. The pipeline is purely pull-based: it fetches feeds and media on demand.

## Environment Configuration

**Required env vars (per provider used in config.yaml):**
- `GROQ_API_KEY` — required when any stage uses `provider: "groq"`
- `OPENAI_API_KEY` — required when any stage uses `provider: "openai"`
- `OPENROUTER_API_KEY` — required when any stage uses `provider: "openrouter"`

**Loading order:**
1. `.env` file loaded via `python-dotenv` (`load_dotenv()` in `config/config_loader.py`)
2. `pydantic-settings` reads env vars into `Credentials` model
3. `load_config()` validates that all keys required by the current `config.yaml` are present

---

*Integration audit: 2026-05-14*
