# Audio Prober — Design Spec

**Date:** 2026-03-22
**Status:** Approved

---

## Overview

Add an `AudioProber` component that uses `ffprobe` to extract codec, exact duration,
channels, and bitrate from each downloaded episode file. The pipeline stores the results
in a new `episode_audio_metadata` table, keyed by episode GUID.

---

## Architecture

Five files are created or modified:

| File | Change |
|---|---|
| `models/feed.py` | Add `AudioMetadata` dataclass |
| `components/audio_prober.py` | New — `AudioProber` class |
| `database/connection.py` | Enable FK enforcement; add `episode_audio_metadata` schema + migration |
| `database/audio_metadata_store.py` | New — `AudioMetadataStore` class |
| `components/pipeline.py` | Capture `download_all` return value; wire prober stage |

### Data flow (inside `Pipeline.run`, after `download_all`)

```
download_all() → [(guid, Path), ...]       # return value now captured by pipeline
    │
    ▼
AudioMetadataStore.get_probed_guids() → already-done set
    │  (pipeline filters out already-probed pairs)
    ▼
AudioProber.probe_all(unprobed_pairs) → [AudioMetadata, ...]
    │
    ▼
AudioMetadataStore.save_all([AudioMetadata, ...])
```

The pipeline owns the filtering logic (consistent with how it owns feed selection).
`AudioProber` is a pure async processor: given paths, run ffprobe, return results.

---

## Data Model

### `AudioMetadata` dataclass (`models/feed.py`)

```python
@dataclass
class AudioMetadata:
    guid: str
    duration: float    # seconds, exact (sub-second precision from ffprobe)
    codec: str         # e.g. "aac", "mp3"
    channels: int      # 1 = mono, 2 = stereo
    bitrate: int       # bits per second
```

`duration` is stored as a float (seconds) rather than the raw `"HH:MM:SS"` string
already present on `Episode` — ffprobe provides sub-second precision which is
valuable for downstream processing (silence detection, segment alignment).

### Database table (`episode_audio_metadata`)

```sql
CREATE TABLE IF NOT EXISTS episode_audio_metadata (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guid     TEXT    NOT NULL UNIQUE REFERENCES episodes(guid),
    duration REAL    NOT NULL,
    codec    TEXT    NOT NULL,
    channels INTEGER NOT NULL,
    bitrate  INTEGER NOT NULL
)
```

- `guid` is `UNIQUE` — `INSERT OR IGNORE` is safe for idempotent saves.
- `REFERENCES episodes(guid)` is a hard FK constraint, enforced at runtime via
  `PRAGMA foreign_keys = ON` (see Database section below).
- Since the database is created fresh (no legacy data), FK enforcement is enabled
  globally on every connection from this point forward.
- The pipeline ordering guarantees that `save_episodes` always runs before
  `probe_all`, so the FK is always satisfied in normal operation.

---

## Database Changes (`database/connection.py`)

### `PRAGMA foreign_keys = ON`

Added to `Database.__aenter__` immediately after `aiosqlite.connect` returns and
before any schema or migration SQL is executed. Exact placement:

```python
self.conn = await aiosqlite.connect(self._db_path)
await self.conn.execute("PRAGMA foreign_keys = ON")   # <-- added here
await self.conn.execute(_SCHEMA)
await self.conn.commit()
# ... migration loop follows
```

### Schema addition

`_SCHEMA` gains a second `CREATE TABLE IF NOT EXISTS` statement for
`episode_audio_metadata` (appended after the existing `episodes` table definition).

### Migration

A new `_NEW_TABLES` list (analogous to `_NEW_COLUMNS`) holds table-level migrations.
The `__aenter__` migration loop attempts `CREATE TABLE IF NOT EXISTS` for each entry;
`OperationalError` is suppressed with `contextlib.suppress` so it is safe to run
against both fresh and legacy databases.

---

## Component APIs

### `AudioProber` (`components/audio_prober.py`)

```python
class AudioProber:
    def __init__(self, timeout: float = 30.0) -> None: ...

    async def probe_all(
        self, pairs: list[tuple[str, Path]]
    ) -> list[AudioMetadata]: ...

    async def _probe_one(self, guid: str, path: Path) -> AudioMetadata: ...
```

**`AudioProber.__init__`** takes an optional `timeout` (seconds, default 30.0) passed
to `asyncio.wait_for` in `_probe_one`. No other constructor arguments. In `Pipeline.__init__`,
constructed as `self._audio_prober = AudioProber()`.

**`_probe_one` implementation:**

Runs:
```bash
ffprobe -v quiet -print_format json -show_streams -show_format <path>
```
via `asyncio.create_subprocess_exec` (not shell — no injection risk), wrapped in
`asyncio.wait_for(..., timeout=self._timeout)`.

Parses JSON stdout:
- `codec` — `streams[0]["codec_name"]` (first audio stream)
- `channels` — `streams[0]["channels"]`
- `duration` — `float(streams[0]["duration"])` (seconds as string in ffprobe output)
- `bitrate` — `int(format["bit_rate"])` as primary source; falls back to
  `int(streams[0]["bit_rate"])` if `format["bit_rate"]` is absent or `"0"`.
  This handles VBR MP3s where stream-level `bit_rate` is often unreliable.

Raises `AudioProbeError` (see below) if:
- ffprobe exits non-zero
- stdout contains no audio stream
- `asyncio.TimeoutError` is raised (re-raised as `AudioProbeError`)

**`probe_all` implementation:**

Iterates serially (same contract as `EpisodeDownloader.download_all`). Returns
`list[AudioMetadata]` for successful probes only. Failed probes (`AudioProbeError`)
are caught, logged, and skipped — the episode is omitted from the returned list.
`probe_all([])` returns `[]` immediately without spawning any subprocess.

### `AudioMetadataStore` (`database/audio_metadata_store.py`)

```python
class AudioMetadataStore:
    def __init__(self, conn: aiosqlite.Connection) -> None: ...

    async def get_probed_guids(self) -> set[str]: ...
    async def save_all(self, records: list[AudioMetadata]) -> None: ...
```

- **Instantiated locally** inside `Pipeline.run` within the `async with Database(...)`
  block — `AudioMetadataStore(db.conn)`. It is **not** a `Pipeline.__init__` attribute
  because it requires a live connection that only exists within that context.
- `get_probed_guids` — `SELECT guid FROM episode_audio_metadata`; returns a `set[str]`.
  Used by the pipeline to filter pairs before calling `probe_all`.
- `save_all` — `executemany` with `INSERT OR IGNORE` for each record; single `commit`
  after all inserts. No partial-commit recovery (consistent with `EpisodeStore.save_episodes`);
  unexpected exceptions propagate to the caller.

### `AudioProbeError` (`utils/exceptions.py`)

```python
class AudioProbeError(PodcastAdCutterError):
    """Raised when ffprobe fails to extract audio metadata from a file."""
```

Inherits from `PodcastAdCutterError` (consistent with `ConfigError`).

---

## Pipeline Integration (`components/pipeline.py`)

### `Pipeline.__init__`

```python
self._audio_prober = AudioProber()
```

Added alongside the other component attributes. `AudioMetadataStore` is **not** added
here (see above).

### `Pipeline.run` — new stage after `download_all`

`EpisodeDownloader.download_all` already returns `list[tuple[str, Path]]`; the pipeline
now captures this return value (currently discarded):

```python
downloaded = await self._episode_downloader.download_all(
    episode_pairs, on_progress=self._on_download_progress
)
```

Inside the open `Database` context, after `EpisodeStore` operations:

```python
audio_metadata_store = AudioMetadataStore(db.conn)
probed_guids = await audio_metadata_store.get_probed_guids()
unprobed = [(g, p) for g, p in downloaded if g not in probed_guids]
probe_results = await self._audio_prober.probe_all(unprobed)
await audio_metadata_store.save_all(probe_results)
```

---

## Error Handling

| Failure | Behaviour |
|---|---|
| ffprobe exits non-zero | `_probe_one` raises `AudioProbeError`; `probe_all` logs and skips |
| No audio stream in JSON | `_probe_one` raises `AudioProbeError`; `probe_all` logs and skips |
| `asyncio.TimeoutError` | `_probe_one` wraps in `AudioProbeError`; `probe_all` logs and skips |
| FK violation on insert | Raises `aiosqlite.IntegrityError` — indicates a pipeline ordering bug |

---

## Testing Strategy (TDD — tests written before implementation)

| Test file | Coverage |
|---|---|
| `tests/test_audio_prober.py` | `_probe_one` with mocked subprocess (fake stdout JSON); non-zero exit code; missing audio stream; timeout; `probe_all` serial iteration; failed episodes skipped; `probe_all([])` returns empty list |
| `tests/test_audio_metadata_store.py` | `get_probed_guids` empty DB; `get_probed_guids` populated; `save_all` INSERT OR IGNORE; FK violation raises `IntegrityError` |
| `tests/test_pipeline.py` | Pipeline captures `download_all` return value; pipeline filters already-probed GUIDs; `probe_all` called with correct pairs; results saved via store |
| `tests/test_database_connection.py` | `PRAGMA foreign_keys = ON` emitted after connect; FK constraint active (insert into child without parent raises `IntegrityError`) |

Subprocess is mocked by patching `asyncio.create_subprocess_exec` — no real ffprobe
binary or filesystem access required in unit tests.
