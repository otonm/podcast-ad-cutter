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
| `components/pipeline.py` | Wire prober stage after `download_all` |

### Data flow (inside `Pipeline.run`, after `download_all`)

```
download_all() → [(guid, path), ...]
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
  `PRAGMA foreign_keys = ON` (added to `Database.__aenter__`).
- Since the database is created fresh (no legacy data), FK enforcement is enabled
  globally on every connection from this point forward.
- The pipeline ordering guarantees that `save_episodes` always runs before
  `probe_all`, so the FK is always satisfied in normal operation.

---

## Component APIs

### `AudioProber` (`components/audio_prober.py`)

```python
class AudioProber:
    async def probe_all(
        self, pairs: list[tuple[str, Path]]
    ) -> list[AudioMetadata]: ...

    async def _probe_one(self, guid: str, path: Path) -> AudioMetadata: ...
```

**`_probe_one` implementation:**

Runs:
```bash
ffprobe -v quiet -print_format json -show_streams <path>
```
via `asyncio.create_subprocess_exec` (not shell — no injection risk). Parses JSON
stdout, reads the first audio stream: `codec_name`, `duration`, `channels`,
`bit_rate`. Raises `AudioProbeError` if ffprobe exits non-zero or no audio stream
is present.

**`probe_all` implementation:**

Iterates serially (same contract as `EpisodeDownloader.download_all`). Failed probes
are caught, logged, and skipped — the episode is omitted from the returned list.

### `AudioMetadataStore` (`database/audio_metadata_store.py`)

```python
class AudioMetadataStore:
    def __init__(self, conn: aiosqlite.Connection) -> None: ...

    async def get_probed_guids(self) -> set[str]: ...
    async def save_all(self, records: list[AudioMetadata]) -> None: ...
```

- `get_probed_guids` — returns all GUIDs already present in `episode_audio_metadata`.
  Used by the pipeline to filter pairs before calling `probe_all`.
- `save_all` — `INSERT OR IGNORE` for each record; commits after all inserts.
  Follows the same pattern as `EpisodeStore.save_episodes`.

### `AudioProbeError` (`utils/exceptions.py`)

New exception class added. Raised by `_probe_one` and caught by `probe_all`.

---

## Pipeline Integration (`components/pipeline.py`)

Inside `Pipeline.run`, after `download_all` returns `[(guid, path), ...]` and within
the open `Database` context:

```python
probed_guids = await audio_metadata_store.get_probed_guids()
unprobed = [(g, p) for g, p in downloaded if g not in probed_guids]
probe_results = await self._audio_prober.probe_all(unprobed)
await audio_metadata_store.save_all(probe_results)
```

`self._audio_prober = AudioProber()` is constructed in `Pipeline.__init__` alongside
the other components.

---

## Error Handling

| Failure | Behaviour |
|---|---|
| ffprobe exits non-zero | Log error, skip episode |
| No audio stream in JSON output | Log warning, skip episode |
| `asyncio.TimeoutError` | Log error, skip episode |
| FK violation on insert | Raises `aiosqlite.IntegrityError` — indicates a pipeline ordering bug |

---

## Testing Strategy (TDD — tests written before implementation)

| Test file | Coverage |
|---|---|
| `tests/test_audio_prober.py` | `_probe_one` with mocked subprocess (fake stdout JSON); non-zero exit; missing stream; serial iteration; failed episodes skipped |
| `tests/test_audio_metadata_store.py` | `get_probed_guids` empty/populated; `save_all` INSERT OR IGNORE; FK violation raises `IntegrityError` |
| `tests/test_pipeline.py` | Pipeline calls `AudioProber` after download; pipeline filters already-probed GUIDs; results saved via store |
| `tests/test_database_connection.py` | `PRAGMA foreign_keys = ON` is emitted on connection open |

Subprocess is mocked by patching `asyncio.create_subprocess_exec` — no real ffprobe
binary or filesystem access required in unit tests.
