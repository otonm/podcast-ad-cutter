# EpisodeDownloader — Design Spec

**Date:** 2026-03-22
**Status:** Approved

---

## Context

The pipeline currently ends at `FeedPublisher.publish()`, which writes an RSS file. The next processing stage will remove ads from the audio files, but first the raw episode audio must be fetched from each episode's enclosure URL and written to a local scratch area (`cache/{guid}.{ext}`).

This module is a pure download step: streaming audio from the network to disk, with real-time progress reporting and automatic cleanup on failure. The cache directory is ephemeral — files are removed after processing or on interruption. A future pipeline stage will determine which episodes need processing based on whether a final processed file already exists in the output folder; the downloader itself makes no such judgement.

---

## Goals

- Stream episode audio from enclosure URLs to `cache/{guid}.{ext}`.
- Report real-time download progress via an async callback `(guid, percent)`.
- Retry failed downloads with exponential backoff; delete partial files on exhausted retries or interruption.
- Stay fully decoupled: no config imports, no database access, no knowledge of the pipeline.

---

## Non-Goals

- Determining which episodes need downloading (pipeline responsibility, implemented later).
- Audio processing or transcoding.
- Resumable downloads.

---

## Architecture

`EpisodeDownloader` is a new class in `components/episode_downloader.py`. It follows the same shape as `FeedDownloader`:

- One public `async def download_all(...)` method.
- A private `_download_one(...)` helper for per-episode logic.
- A single `aiohttp.ClientSession` opened for the full batch.
- Episodes are downloaded **serially** within that session (one at a time, matching `FeedDownloader`'s pattern).
- No config imports; all parameters passed at construction or call time.

### Pipeline placement

```
Pipeline.run()
  → FeedDownloader.download_all()          # fetch RSS XML
  → FeedParser.parse_all()                 # parse to Episode objects
  → EpisodeStore.save_episodes()           # persist
  → EpisodeStore.get_episodes_for_feed()
  → FeedPublisher.publish()                # write RSS file
  → EpisodeDownloader.download_all()       # NEW: stream audio to cache/
```

`download_all` is called **once per feed** inside the existing per-feed loop in `pipeline.run()`, passing that feed's episode list. No structural change to the pipeline loop is needed.

The pipeline passes all episodes for the feed as `(guid, url)` pairs. Filtering (e.g. skipping episodes whose output file already exists) is not the downloader's concern.

---

## Public API

```python
class EpisodeDownloader:
    def __init__(
        self,
        cache_dir: Path,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        chunk_size: int = 1024 * 1024,  # 1 MB
        timeout: float | None = 300.0,  # seconds; None = no timeout
    ) -> None:
        ...

    async def download_all(
        self,
        episodes: list[tuple[str, str]],               # (guid, url)
        on_progress: Callable[[str, float], Awaitable[None]] | None = None,
    ) -> list[tuple[str, Path]]:                       # (guid, cache_path) — successes only
        ...
```

- `cache_dir` is created with `mkdir(parents=True, exist_ok=True)` at the **top of `download_all`**, before the episode loop. The constructor performs no I/O.
- `chunk_size` controls streaming granularity and progress callback frequency.
- `timeout` is passed to `aiohttp.ClientTimeout(total=timeout)` when opening the session. Without this, `asyncio.TimeoutError` would never be raised by aiohttp. Default is 300 s (suitable for large audio files on slow connections).
- Returns only successfully downloaded episodes; failed ones are logged and omitted.
- Results are returned in the same order as the input list.

### Progress callback

```python
async def on_progress(guid: str, percent: float) -> None: ...
```

- Called with `0.0` at the **start** of each episode download (treat as "starting / indeterminate", not "0% complete").
- Called with intermediate values `0.0 < p < 1.0` after each chunk if the server provides a `Content-Length` header.
- Called with `1.0` on successful completion.
- If the server omits `Content-Length`, only `0.0` (start) and `1.0` (end) are emitted.

The `0.0` sentinel signals "download starting" and should be treated as an indeterminate state by callers displaying a progress bar, not as a meaningful percentage.

---

## File Naming

Output path: `cache_dir / f"{guid}.{ext}"`

Extension is derived from the `Content-Type` **MIME type** of the HTTP response. Use `response.content_type` (aiohttp's parsed attribute) rather than the raw `Content-Type` header string — this strips parameters such as `codecs=...` before comparison.

| `response.content_type`        | Extension |
|-------------------------------|-----------|
| `audio/mpeg`                  | `mp3`     |
| `audio/mp4` or `audio/x-m4a` | `m4a`     |
| `audio/ogg`                   | `ogg`     |
| `audio/opus`                  | `opus`    |
| `audio/flac`                  | `flac`    |
| `audio/wav`                   | `wav`     |
| anything else                 | `mp3` (fallback, logged at WARNING) |

This avoids reliance on tracker redirect URLs where the extension may appear in an intermediate path rather than the final content URL.

---

## Error Handling & Retry

Each episode is attempted up to `max_retries + 1` times with exponential backoff. Sleep duration before attempt `N+1` is `retry_delay * (2 ** N)`:

```
attempt 0 → fail → sleep(retry_delay * 1)   # 2^0 = 1
attempt 1 → fail → sleep(retry_delay * 2)   # 2^1 = 2
attempt 2 → fail → sleep(retry_delay * 4)   # 2^2 = 4
attempt 3 → fail → log ERROR, return None
```

With the default `retry_delay=1.0` and `max_retries=3`, total wait before giving up is 7 seconds.

- `aiohttp.ClientError` and `asyncio.TimeoutError` trigger retries. (Timeout only fires if `timeout` is set — see API section.)
- HTTP non-200 responses are treated as failures and trigger retries.
- `asyncio.CancelledError` is **not** caught — it propagates. However a `try/finally` block guarantees the partial cache file is deleted before propagation.
- On exhausted retries the partial cache file is deleted and the episode is omitted from results.

---

## Pipeline Integration

In `pipeline.py`, `EpisodeDownloader` is constructed **once per run** (outside the per-feed loop), since `cache_dir` is feed-independent. Inside the loop, after `FeedPublisher.publish()`:

```python
# constructed once, before the feed loop
downloader = EpisodeDownloader(cache_dir=config.app.paths.cache_dir)

# inside the per-feed loop, after FeedPublisher.publish():
results = await downloader.download_all(
    [(ep.guid, ep.url) for ep in episodes],
    on_progress=self._on_download_progress,
)
```

`episodes` is the per-feed list already in hand from `store.get_episodes_for_feed(...)`. No extra DB query is needed.

---

## Testing Strategy

File: `tests/test_episode_downloader.py`
Framework: `pytest-asyncio` (`asyncio_mode = "auto"`, already configured), `aioresponses`, `tmp_path`.

| Scenario | Assertion |
|---|---|
| Successful download | File written at `cache/{guid}.{ext}`, path returned |
| Content-Type extension mapping | mp3/m4a/ogg/opus/flac/wav all produce correct extension |
| `audio/mp4; codecs=...` (parameterised MIME) | Correctly resolved to `m4a` via `response.content_type` |
| Unknown Content-Type | Falls back to `mp3`, warning logged |
| Progress callback with Content-Length | Called at 0.0 (start), intermediate values, 1.0 |
| Progress callback without Content-Length | Called only at 0.0 (start) and 1.0 |
| HTTP non-200 | Retries N times, then episode omitted, no file left in cache |
| `aiohttp.ClientError` | Same retry-then-skip behaviour |
| `asyncio.TimeoutError` | Same retry-then-skip behaviour |
| Retries exhausted | Partial file deleted, episode omitted |
| Multiple episodes, both succeed | Both paths returned in input order |
| Multiple episodes, one fails | Failed one skipped; successful ones returned |
| `asyncio.CancelledError` mid-download | Partial file deleted, error propagates (not swallowed) |
| `cache_dir` created if absent | Directory created at top of `download_all` |

---

## Dependencies

No new runtime dependencies. Uses:
- `aiohttp` (already in `pyproject.toml`)
- `asyncio` (stdlib)
- `pathlib` (stdlib)
- `logging` (stdlib)
- `collections.abc.Callable`, `collections.abc.Awaitable` (stdlib)
