# Architecture

## High-Level Overview

The codebase is a **linear processing pipeline** with a resumable per-episode state machine. It is not event-driven or service-oriented — it is a batch CLI tool that runs to completion and exits.

```
main.py (CLI entry point)
  └── Pipeline (orchestrator, owns Config + DB lifetime)
        ├── Feed-level phase: download + parse + publish RSS
        └── Episode-level phase: while-loop state machine per episode
```

## Layered Architecture

| Layer | Package | Purpose |
|-------|---------|---------|
| Entry point | `main.py` | CLI args, logging setup, `Pipeline` instantiation |
| Config | `config/` | YAML + env var loading via Pydantic + pydantic-settings |
| Orchestrator | `components/pipeline.py` | Sole owner of `Config`; coordinates all stages |
| Components | `components/` | One class per pipeline stage; receive plain typed values |
| Models | `models/` | Pydantic data models for domain objects |
| Database | `database/` | aiosqlite stores — one class per entity |
| Utils | `utils/` | ffmpeg wrappers, LLM helpers, exceptions, episode logging |

**Config isolation rule:** Only `main.py` and `Pipeline` import from `config/`. All components below `Pipeline` receive plain values through their constructors — they have no knowledge of the config structure.

## Data Flow

```
RSS URL (config)
  → FeedDownloader (aiohttp, async)
  → FeedParser (stdlib xml.etree)
  → EpisodeStore.save_episodes (aiosqlite)
  → EpisodeStore.get_episodes_for_feed
  → FeedPublisher.publish (write RSS XML)

Per episode (while-loop state machine):
  ↓ Guard 1: output file on disk → update URL, done
  ↓ Guard 2: ad detection in DB → parse cuts → AudioEditor / EpisodeCopier, done
  ↓ Guard 3: topic in DB → AdDetector (LLM) → AdStore
  ↓ Guard 4: transcription in DB → TopicExtractor (LLM) → TopicStore
  ↓ Guard 5: audio on disk (cache or this run) → AudioPreprocessor (ffmpeg mono) → EpisodeTranscriptor (Groq STT) → TranscriptionStore
  ↓ Bottom: EpisodeDownloader (aiohttp) → disk
  loop
```

## Episode State Machine

`Pipeline._process_episode_until_final` is a `while True` loop that evaluates 5 guards in order, executes **exactly one step**, persists the result, then loops. This makes every stage resumable across crashed or interrupted runs.

| Guard | Condition | Action |
|-------|-----------|--------|
| 1 | Output file exists on disk | Update URL in DB → return |
| 2 | Ad detection result in DB | Parse cuts, export audio → return |
| 3 | Topic extracted | Run ad detection → continue |
| 4 | Transcript exists | Extract topic → continue |
| 5 | Audio on disk | Probe + preprocess + transcribe → continue |
| — | None | Download audio → continue |

Per-feed shared state (`_Stores` dataclass) holds GUID sets loaded once at feed start and mutated in-place as episodes complete each stage — avoids repeated DB round-trips within a feed run.

## LLM Integration

- **LiteLLM** as the unified LLM client for all AI calls (transcription via Groq, topic extraction and ad detection via OpenAI or OpenRouter)
- `AdDetector` and `TopicExtractor` implement their own retry loops with internal sentinel exceptions:
  - `_JsonValidateFailedError` — retry without JSON schema enforcement
  - `_ContextWindowExceededError` — retry with truncated transcript
- Reasoning content extracted from responses when available (for model observability)
- Cost tracking: every LLM call produces a cost model object saved to `CostTrackingStore`

## Audio Processing

- **ffmpeg/ffprobe** invoked as subprocesses via `utils/ffmpeg.py`
- `AudioProber` — extracts duration, codec, channels, bitrate via ffprobe JSON
- `AudioPreprocessor` — converts to mono AAC for Groq STT (reduces file size)
- `EpisodeTranscriptor` — chunks audio at 25 MB boundaries to avoid Groq 413 errors; uses `litellm` audio transcription
- `AudioEditor` — cuts ads using ffmpeg `atrim`+`concat` filters; produces clean output file

## Async Patterns

- `asyncio.gather` for concurrent feed downloads
- `async with Database(path) as db` for DB lifetime management
- `async with aiohttp.ClientSession()` for HTTP (inside downloader components)
- All component methods are `async def`; progress callbacks are `async def` too
- Sync filesystem operations (glob, stat, unlink) used inside async functions with `# noqa: ASYNC240` where unavoidable

## RSS Publishing

- `FeedPublisher` writes the output RSS feed to disk
- Feed metadata is re-published on every run with updated episode URLs pointing to the locally-hosted edited audio
- Episode URLs are patched in-memory after audio export and written back to the RSS file
- `owner_email` scrubbed to `None` and `podcast:guid` added on every publish (Podcast 2.0 compliance + privacy)
