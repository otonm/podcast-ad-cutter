# podcast-ad-cutter

Downloads the latest episodes of configured podcasts, transcribes them, identifies advertisements using an LLM, and exports clean audio files with ads removed.

```
RSS Feed → Download → Preprocess → Transcribe → Extract Topic → Detect Ads → Cut Audio → Export
```

---

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- ffmpeg
- API keys for at least one LLM provider (see [LLM Providers](#llm-providers))

---

## Setup

**1. Install dependencies**

```bash
uv sync
```

**2. Configure API keys**

```bash
cp .env.example .env
```

Edit `.env` and fill in the keys for the providers you intend to use:

```bash
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
OPENROUTER_API_KEY=sk-or-...
```

**3. Configure the application**

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`: add your feed URLs under `feeds`, set the transcription and interpretation providers and models, and adjust paths if needed.

---

## Configuration reference

### `feeds`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `name` | string | — | Display name, used for output directory and `--feed` filter |
| `url` | string | — | RSS feed URL |
| `enabled` | bool | `true` | Set to `false` to skip without removing the entry |

### `paths`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `output_dir` | path | — | Directory for output audio files; subdirectories are created per feed |
| `database` | path | — | SQLite database path; parent directories are created automatically |

### `transcription`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | — | `groq`, `openai`, or `openrouter` |
| `model` | string | — | Model name for the selected provider (e.g. `whisper-1`, `whisper-large-v3`) |
| `language` | string \| null | `"en"` | BCP-47 language code; `null` for auto-detect |

### `interpretation`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `provider` | string | — | `groq`, `openai`, or `openrouter` |
| `model` | string | — | Model name for the selected provider (e.g. `gpt-4o`, `llama-3.3-70b-versatile`) |
| `temperature` | float | `0` | Sampling temperature |
| `max_tokens` | int | `2048` | Maximum tokens in the LLM response |
| `topic_excerpt_words` | int | `2000` | Number of words from transcript start to send for topic extraction |

### `ad_detection`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `chunk_duration_sec` | int | `300` | Duration of each transcript chunk when chunking is required (seconds) |
| `chunk_overlap_sec` | int | `30` | Overlap between consecutive chunks to avoid missing ads at boundaries |
| `min_confidence` | float | `0.75` | Segments below this confidence are logged but not cut |
| `merge_gap_sec` | int | `5` | Adjacent ad segments within this gap are merged into one |

### `audio`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `output_format` | string | `"mp3"` | Output container: `mp3` or `m4a` |
| `cbr_bitrate` | string | `"192k"` | Constant bitrate for export; prevents VBR timestamp drift |

### `logging`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `level` | string | `"INFO"` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `log_file` | string \| null | `null` | Append logs to this file in addition to stdout |

### `retry`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_attempts` | int | `3` | Maximum LLM call attempts before giving up |
| `backoff_factor` | int | `2` | Exponential backoff multiplier between retries |

### `prompts`

| Key | Type | Description |
|-----|------|-------------|
| `ad_detection` | string | Behaviour instructions for ad detection. The JSON schema and format directives are appended automatically — do not include them here. |
| `topic_extraction` | string | Behaviour instructions for topic extraction. The JSON schema and format directives are appended automatically — do not include them here. |

---

## LLM providers

| Provider | Transcription models | Interpretation models | Env var |
|----------|---------------------|----------------------|---------|
| `openai` | `whisper-1` | `gpt-4o`, `gpt-4o-mini` | `OPENAI_API_KEY` |
| `groq` | `whisper-large-v3` | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768` | `GROQ_API_KEY` |
| `openrouter` | `openai/whisper-large-v3` | any model available on OpenRouter (e.g. `anthropic/claude-opus-4-6`) | `OPENROUTER_API_KEY` |

---

## CLI

```bash
uv run python main.py [options]
```

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to config file (default: `config.yaml`) |
| `--feed NAME` | Process only the feed with this name |
| `--output PATH` | Override `paths.output_dir` from config |
| `--min-confidence FLOAT` | Override `ad_detection.min_confidence` from config |
| `--use-cache` | Skip transcription if a cached transcript exists in the database |
| `--dry-run` | Run the full pipeline but skip the audio cutting step |
| `-v` / `--verbose` | Set log level to DEBUG |

---

## Pipeline behaviour

### Caching & checkpoints

The pipeline checks four levels before doing any work for an episode:

1. **Clean file exists on disk** — if `output/{feed}/{date} - {title}.{ext}` is present, the episode is skipped entirely. No download, no DB access.
2. **Ad segments cached** — if the `ad_segments` table already has rows for this episode, only download and cut are performed. Transcription, topic extraction, and ad detection are skipped.
3. **Transcript cached** — if the `transcripts` table has a row for this episode, transcription is skipped. Used when `--use-cache` is set.
4. **Topic context cached** — if the `topic_contexts` table has a row for this episode, topic extraction is skipped.

### Output layout

```
output/
└── {feed name}/
    └── DD.MM.YYYY - {episode title}.{ext}
```

The feed name and episode title are sanitised to remove filesystem-invalid characters and truncated to 120 characters.

### Cost tracking

Every LLM call is recorded in the `llm_calls` table with the model used and the cost in USD. To query total cost:

```sql
SELECT SUM(cost_usd) FROM llm_calls;
```

---

## Database

Path is set by `paths.database`. The file and its parent directories are created on first run.

| Table | Purpose |
|-------|---------|
| `episodes` | One row per episode; keyed on RSS `guid` |
| `transcripts` | Full transcript text and metadata per episode |
| `transcript_segments` | Word- or segment-level timestamps; FK to `transcripts` |
| `topic_contexts` | Extracted domain, topic, host names, and notes |
| `ad_segments` | Detected ad spans with confidence, reason, and `was_cut` flag |
| `llm_calls` | LLM call log with model, call type, and cost |

**Useful queries**

Check transcript for an episode:
```sql
SELECT full_text FROM transcripts WHERE episode_guid = '<guid>';
```

List ad segments with confidence:
```sql
SELECT start_ms, end_ms, confidence, sponsor_name, was_cut
FROM ad_segments
WHERE episode_guid = '<guid>'
ORDER BY start_ms;
```

Get total LLM cost:
```sql
SELECT call_type, model, SUM(cost_usd) AS total_usd
FROM llm_calls
GROUP BY call_type, model;
```

---

## Customising prompts

Both prompts are in `config.yaml` under `prompts:`. The JSON schema and output format directives are appended automatically by the config loader — do not include them in your custom text.

**Default ad detection prompt:**
```
Identify advertisements in this podcast transcript segment.
An ad is any span where the host or another person or persons promote a product, service, or sponsor.
Exclude brand mentions that are naturally part of the episode content.
```

**Default topic extraction prompt:**
```
Analyze the opening of this podcast transcript.
```

Tune `prompts.ad_detection` to match the style or language of a specific podcast. For example, for a podcast that frequently discusses its own sponsors in a non-promotional context, add additional signals or exclusion rules to reduce false positives.

---

## Development

```bash
# Run tests
uv run pytest

# Lint and format check
uv run ruff check . && uv run ruff format --check .

# Type check
uv run mypy .
```

Test fixtures are in `tests/fixtures/`. Tests use an in-memory SQLite database and `AsyncMock` for all LLM calls — no real network calls are made.
