# Podcast Ad Cutter

Automatically removes advertisements from podcast episodes and re-publishes clean RSS feeds you can add to any podcast app.

It downloads your configured feeds, transcribes each episode with a speech-to-text model, extracts the topic of an episode to help with ad identification, identifies ad segments, cuts them out with ffmpeg, and serves the resulting clean audio via a self-hosted RSS feed. A built-in HTTP API allows for building of UIs or other applications.

---

## Requirements

- **[uv](https://docs.astral.sh/uv/)** (for local runs) — or Docker
- **ffmpeg** installed and on `PATH`
- An API key for at least one supported LLM provider: [Groq](https://console.groq.com), [OpenAI](https://platform.openai.com), or [OpenRouter](https://openrouter.ai)

---

## Quick Start (local)

```bash
# 1. Copy and edit the config
cp config.example.yaml config.yaml

# 2. Add your API key(s) to a .env file
echo "GROQ_API_KEY=your-key-here" > .env

# 3. Run the pipeline once
uv run python main.py

# 4. (Optional) Start the HTTP API server
uv run python main.py --serve
```

---

## Configuration

All settings live in `config.yaml`. Copy `config.example.yaml` as a starting point.

### Feeds

```yaml
feeds:
  - title: "My Podcast"          # display name — also used as the output folder name
    url: "https://example.com/feed.rss"
    enabled: true
    episodes_to_keep: 10         # how many episodes to retain in the output feed
```

### Models

Three LLM tasks are configurable independently:

```yaml
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
    context_window: 8192          # optional — truncate prompt to this token limit
```

Supported providers: `groq`, `openai`, `openrouter`.

### Ad Detection

```yaml
ad_detection:
  min_duration: 10000    # ignore ads shorter than this (milliseconds)
  min_confidence: 0.7    # ignore segments the model rates below this score (0–1)
```

### Output Audio

```yaml
output:
  file_type: "mp3"       # mp3 | m4a | ogg | opus | flac
  bitrate: "128k"
```

### Paths

```yaml
paths:
  output_dir: "./output"   # clean audio files and RSS feeds go here
  cache_dir:  "./cache"    # temporary downloaded audio (safe to delete between runs)
  data_dir:   "./data"     # SQLite database
  log_dir:    "./logs"     # log files (when enabled)
```

### API Keys

Stored as environment variables (never in `config.yaml`). Create a `.env` file:

```bash
GROQ_API_KEY=...
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...
```

Only the key for the provider(s) you actually use needs to be set.

---

## CLI Flags

```
--config PATH           Config file path (default: config.yaml)
--feed NAME             Process only the named feed (exact title match)
--output PATH           Override output directory
--min-confidence FLOAT  Override ad_detection.min_confidence
--force-ai-detection    Force LLM ad detection even when a cached result exists
--debug                 Enable debug-level console logging
--log-to-file           Write logs to a timestamped file in logs/
--serve                 Start the HTTP API server instead of running the pipeline
--host HOST             API server bind host (default: 0.0.0.0)
--port PORT             API server bind port (default: 8080)
```

---

## HTTP API Server

Start with `--serve` to expose a REST API and SSE event stream:

```bash
uv run python main.py --serve --port 8080
```

The API lets you start/stop pipeline runs, manage feeds, inspect episode state, and stream live logs — all without restarting the process. See [API.md](API.md) for the full reference.

---

## Docker

Copy `docker-compose.example.yml` to `docker-compose.yml` and `config.example.yaml` to `config/config.yaml`, fill in your API keys, and configure `base_url` to your public hostname. Then:

```bash
docker compose pull && docker compose up -d
```

The container supports two modes:

- **Cron mode** (default) — runs the pipeline once at container start, then on a schedule. Set `CRON_SCHEDULE` to control the interval.
- **Server mode** — runs the HTTP API server continuously. Set `APP_SERVE=true`. It still runs the pipeline once at start and then on `CRON_SCHEDULE`, by calling `POST /api/v1/run` (a tick during an active run gets 409 and is skipped).

**Environment variables in the container:**

| Variable | Description |
|---|---|
| `CRON_SCHEDULE` | Cron expression for pipeline runs (default: `0 * * * *`). Applies in both modes. |
| `GROQ_API_KEY` | Groq API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `APP_SERVE` | Set to `true` to run the HTTP API server instead of cron |
| `APP_HOST` | API server bind host (default: `0.0.0.0`); must include loopback for scheduled runs in server mode |
| `APP_PORT` | API server bind port (default: `8080`) |
| `APP_FEED` | Process only this feed title |
| `APP_MIN_CONFIDENCE` | Override min confidence threshold |
| `APP_LOG_TO_FILE` | Set to `true` to write log files |
| `APP_DEBUG` | Set to `true` for debug logging |

**Volume mounts:**

| Mount | Purpose |
|---|---|
| `./config:/config` | Directory holding `config.yaml`; writable so settings saved via the API persist |
| `./output:/output` | Processed audio and RSS feeds (host folder, e.g. for serving the feeds) |
| `data:/data` | SQLite database |
| `cache:/cache` | Temporary audio during processing |
| `logs:/logs` | Log files |

---

## Output

Processed files are written to `output_dir/<feed-slug>/`:

- **`<feed-slug>.rss`** — clean RSS feed, ready to subscribe to in any podcast app
- **`<DD.MM.YYYY>-<episode-slug>.mp3`** — ad-free episode audio

Point your podcast app at the `.rss` file's URL (e.g. `http://your-server:8080/output/my-podcast/my-podcast.rss` if you're also serving the output directory).

The `base_url` config value is used to construct episode enclosure URLs in the feed — it must be reachable by your podcast client.

---

## Subscribing to the Clean Feed

Once the pipeline has run, the `.rss` file in each feed's output folder is a valid podcast feed. Add its URL to any podcast app that supports custom RSS (Overcast, Pocket Casts, AntennaPod, etc.).

---

## Web GUI and Deployment with Tailscale

The `gui` service in `docker-compose.example.yml` is a small Caddy container. It serves a browser UI for monitoring runs, disabling/deleting episodes, viewing transcripts and detected ads, editing settings and feeds, and tailing logs. It proxies `/api/*` to the backend, which must run in server mode (`APP_SERVE=true`). There is no authentication: keep the GUI port private.

With Tailscale installed on the host, publish the feeds publicly and keep the GUI on your tailnet:

```bash
# Public (Funnel, port 443): RSS feeds and episode audio straight from the output folder
sudo tailscale funnel --bg "$(pwd)/output"

# Tailnet only (Serve, port 8443): the GUI container published on 127.0.0.1:8090
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090

tailscale funnel status && tailscale serve status
```

- Set `base_url: https://<host>.<tailnet>.ts.net` in `config/config.yaml`. Feeds are then at `https://<host>.<tailnet>.ts.net/<feed-slug>.rss`; episode URLs inside the feeds are rewritten to the new `base_url` on the next run.
- The GUI is at `https://<host>.<tailnet>.ts.net:8443/`. From a device **outside** your tailnet, check that this URL does not load, and that `https://<host>.<tailnet>.ts.net/api/v1/health` returns 404 (Funnel serves files only).
- Funnel only listens on 443, 8443 or 10000, and one port cannot be both Serve and Funnel. If your Tailscale version refuses Funnel and Serve on one node, give the GUI its own hostname instead.
- Funneling a folder shows a public directory listing of `output/` (feed and episode names). The files are public through the RSS anyway.
- Changing `base_url` changes the feed URL: podcast apps subscribed to the old URL must resubscribe.

---

## Logging

- Console logging is always on. Level is set by `log.level` in config or `--debug`.
- File logging: set `log.to_file: true` in config or pass `--log-to-file`. Files are written to `log_dir/` with ISO-8601 timestamps.
- Per-episode logs: set `log.per_episode: true` to write a separate debug log for each episode to `log_dir/episodes/<feed-slug>/`. Useful for diagnosing why an episode's ads were or weren't cut.
- Log rotation: set `log.rotate: true` and `log.keep_last: N` to automatically prune old log files.
