# External Integrations

**Analysis Date:** 2026-03-28

## APIs & External Services

**LLM Providers (via litellm):**
- **Groq** - Speech-to-text (Whisper) and ad detection reasoning
  - SDK/Client: `litellm` (handles auth and routing)
  - Auth: `GROQ_API_KEY` env var
  - Usage: `components/episode_transcriptor.py`, `components/topic_extractor.py`

- **OpenAI** - Transcription and topic/context extraction
  - SDK/Client: `litellm` (handles auth and routing)
  - Auth: `OPENAI_API_KEY` env var
  - Usage: `components/episode_transcriptor.py`, `components/topic_extractor.py`

- **OpenRouter** - Fallback LLM routing for transcription and extraction
  - SDK/Client: `litellm` (handles auth and routing)
  - Auth: `OPENROUTER_API_KEY` env var
  - Usage: `components/episode_transcriptor.py`, `components/topic_extractor.py`

**Podcast Feed Sources:**
- RSS/Atom feeds via HTTP
  - Client: `aiohttp.ClientSession`
  - Implementation: `components/feed_downloader.py`
  - Protocol: HTTP GET requests to feed URLs configured in `config.yaml`

**Episode Audio Downloads:**
- HTTP enclosure downloads from podcast hosting
  - Client: `aiohttp.ClientSession`
  - Implementation: `components/episode_downloader.py`
  - Features: Retry logic with exponential backoff, streaming chunks, progress callbacks

## Data Storage

**Databases:**
- **SQLite** (local file-based)
  - Location: `{data_dir}/podcast_ad_cutter.db` (path configured in `config.yaml`)
  - Client: `aiosqlite` (async wrapper)
  - Implementation: `database/connection.py`
  - Tables:
    - `episodes` - Podcast episode metadata (title, guid, pubdate, duration, etc.)
    - `episode_audio_metadata` - Audio codec, channels, bitrate, duration
    - `transcriptions` - Full transcription text per episode
    - `transcription_segments` - Timestamped segments with start/end times
    - `cost_tracking` - LLM API costs per provider/model call
    - `topic_extractions` - Extracted topic, hosts, show name per episode
  - Connection: `database/connection.py:Database` - async context manager with foreign key constraints

**File Storage:**
- **Local filesystem** - No cloud storage
  - Cache directory: `{cache_dir}` - Downloaded audio files in original format
  - Output directory: `{output_dir}` - Processed audio files (codec configurable)
  - Data directory: `{data_dir}` - SQLite database
  - Log directory: `{log_dir}` - Application logs
  - All paths configured in `config.yaml` under `paths` section

**Caching:**
- None (beyond local disk caching of audio files)

## Authentication & Identity

**Auth Provider:**
- Custom - No user authentication system
- API authentication via environment variables:
  - `GROQ_API_KEY` - Groq API access
  - `OPENAI_API_KEY` - OpenAI API access
  - `OPENROUTER_API_KEY` - OpenRouter API access
- Implementation: `config/config_loader.py:Credentials` - loads from env via pydantic-settings

## Monitoring & Observability

**Error Tracking:**
- None - Project-specific exception classes for error handling

**Logs:**
- Stream logging to stdout (always)
- Optional file logging to `{log_dir}/{timestamp}.log` when enabled
- Log level configurable per config file or CLI flag (`--debug`)
- Libraries suppressed at WARNING level: `aiosqlite`, `LiteLLM`
- Timestamp format: ISO 8601 with local timezone
- Implementation: `main.py:configure_logging()`

## CI/CD & Deployment

**Hosting:**
- Not detected - Project is a CLI tool, not a hosted service

**CI Pipeline:**
- Not detected - No GitHub Actions or similar found

## Environment Configuration

**Required env vars:**
- `GROQ_API_KEY` - For Groq LLM provider (if used in config)
- `OPENAI_API_KEY` - For OpenAI LLM provider (if used in config)
- `OPENROUTER_API_KEY` - For OpenRouter LLM provider (if used in config)
- At minimum, one API key is required for the configured transcription provider

**Secrets location:**
- `.env` file in project root (not committed to git)
- Reference template: `.env.example`
- Loaded by `python-dotenv` in `config/config_loader.py`

## Webhooks & Callbacks

**Incoming:**
- None - No webhook endpoints

**Outgoing:**
- None - No outbound webhooks or callbacks

## Data Flow

**Feed Download → Parse → Episode Download:**
1. `FeedDownloader` - Fetches RSS/Atom XML via `aiohttp` from feed URLs
2. `FeedParser` - Parses XML to extract episode metadata
3. `EpisodeDownloader` - Streams audio files from enclosure URLs, saves to cache

**Audio Processing Pipeline:**
1. `AudioProber` - Probes audio metadata (duration, codec, channels) via `ffprobe`
2. `AudioPreprocessor` - Converts to mono WAV via `ffmpeg`
3. `EpisodeTranscriptor` - Sends to Groq/OpenAI/OpenRouter via `litellm` for transcription
4. `TopicExtractor` - Extracts topic/hosts from transcript via `litellm`
5. Cost tracking recorded for each LLM call in `cost_tracking` table

**Database Writes:**
- `EpisodeStore` - Saves episode metadata to `episodes` table
- `AudioMetadataStore` - Records codec/duration to `episode_audio_metadata` table
- `TranscriptionStore` - Saves transcription and segments to `transcriptions`/`transcription_segments`
- `CostTrackingStore` - Logs LLM API costs to `cost_tracking` table
- `TopicStore` - Saves extracted metadata to `topic_extractions` table

**Feed Publication:**
- `FeedPublisher` - Generates RSS/Atom XML with processed episodes
- Output written to `{output_dir}/feed.xml`

---

*Integration audit: 2026-03-28*
