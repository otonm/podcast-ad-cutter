# Audio Prober Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `AudioProber` component that runs `ffprobe` on downloaded episode files and stores codec, duration, channels, and bitrate in a new `episode_audio_metadata` table.

**Architecture:** `AudioProber` is a pure async processor — it receives `(guid, Path)` pairs, runs `ffprobe` once per file via `asyncio.create_subprocess_exec`, and returns `AudioMetadata` dataclasses. The pipeline owns filtering (skipping already-probed episodes) and calls the new `AudioMetadataStore` to persist results. A new `episode_audio_metadata` table with a FK to `episodes.guid` is enforced via `PRAGMA foreign_keys = ON` added to `Database.__aenter__`.

**Tech Stack:** Python 3.12, aiosqlite, asyncio subprocess, pytest, ruff, TDD throughout.

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Modify | `utils/exceptions.py` | Add `AudioProbeError` |
| Modify | `models/feed.py` | Add `AudioMetadata` dataclass |
| Modify | `database/connection.py` | PRAGMA FK enforcement; `episode_audio_metadata` schema |
| Create | `database/audio_metadata_store.py` | `AudioMetadataStore` — DB reads/writes for probe results |
| Create | `components/audio_prober.py` | `AudioProber` — runs ffprobe, parses JSON output |
| Modify | `components/pipeline.py` | Capture download results; wire prober stage |
| Modify | `tests/test_exceptions.py` | `AudioProbeError` hierarchy test |
| Modify | `tests/test_database_connection.py` | PRAGMA + new table tests |
| Create | `tests/test_audio_metadata_store.py` | Store unit tests (real SQLite, tmp_path) |
| Create | `tests/test_audio_prober.py` | Prober unit tests (mocked subprocess) |
| Modify | `tests/test_pipeline.py` | Pipeline prober integration tests |

---

## Task 1: `AudioProbeError` exception

**Files:**
- Modify: `utils/exceptions.py`
- Modify: `tests/test_exceptions.py`

- [ ] **Step 1.1 — Write the failing test**

Add to `tests/test_exceptions.py`:

```python
from utils.exceptions import AudioProbeError, ConfigError, PodcastAdCutterError


def test_audio_probe_error_is_podcast_error() -> None:
    assert issubclass(AudioProbeError, PodcastAdCutterError)
```

- [ ] **Step 1.2 — Run to confirm failure**

```
uv run pytest tests/test_exceptions.py::test_audio_probe_error_is_podcast_error -v
```
Expected: `ImportError: cannot import name 'AudioProbeError'`

- [ ] **Step 1.3 — Implement**

Add to `utils/exceptions.py` (after `ConfigError`):

```python
class AudioProbeError(PodcastAdCutterError):
    """Raised when ffprobe fails to extract audio metadata from a file."""
```

- [ ] **Step 1.4 — Run tests + coverage + ruff**

```
uv run pytest tests/test_exceptions.py -v
uv run pytest --cov=utils/exceptions.py tests/test_exceptions.py
uv run ruff check utils/exceptions.py
```
Expected: all pass, 100% coverage.

- [ ] **Step 1.5 — Commit**

```bash
git add utils/exceptions.py tests/test_exceptions.py
git commit -m "feat: add AudioProbeError exception"
```

---

## Task 2: `AudioMetadata` dataclass

**Files:**
- Modify: `models/feed.py`

No separate test file — `AudioMetadata` is covered by the store and prober tests in later tasks.

- [ ] **Step 2.1 — Add the dataclass**

Add to `models/feed.py` after the `Episode` dataclass:

```python
@dataclass
class AudioMetadata:
    """Audio metadata extracted from a downloaded episode file via ffprobe."""

    guid: str
    duration: float  # exact seconds (sub-second precision from ffprobe)
    codec: str       # e.g. "aac", "mp3"
    channels: int    # 1 = mono, 2 = stereo
    bitrate: int     # bits per second
```

- [ ] **Step 2.2 — Run full test suite to confirm nothing breaks**

```
uv run pytest -v
uv run ruff check models/feed.py
```
Expected: all pass.

- [ ] **Step 2.3 — Commit**

```bash
git add models/feed.py
git commit -m "feat: add AudioMetadata dataclass"
```

---

## Task 3: Database — PRAGMA + `episode_audio_metadata` table

**Files:**
- Modify: `database/connection.py`
- Modify: `tests/test_database_connection.py`

- [ ] **Step 3.1 — Write failing tests**

Add to `tests/test_database_connection.py`:

```python
import aiosqlite

from database.connection import Database


async def test_foreign_keys_pragma_is_enforced(db_path: Path) -> None:
    """PRAGMA foreign_keys = ON must be active — inserting into a child table
    without a matching parent row must raise IntegrityError."""
    async with Database(db_path) as db:
        # episodes table exists; episode_audio_metadata references it.
        # Inserting a metadata row with a non-existent guid must fail.
        with pytest.raises(aiosqlite.IntegrityError):
            await db.conn.execute(
                "INSERT INTO episode_audio_metadata (guid, duration, codec, channels, bitrate) "
                "VALUES ('ghost-guid', 60.0, 'aac', 2, 128000)"
            )


async def test_episode_audio_metadata_table_exists(db_path: Path) -> None:
    async with Database(db_path):
        pass
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='episode_audio_metadata'"
        )
        row = await cursor.fetchone()
    assert row is not None


async def _audio_metadata_column_names(db_path: Path) -> set[str]:
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(episode_audio_metadata)")
        rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def test_episode_audio_metadata_has_expected_columns(db_path: Path) -> None:
    async with Database(db_path):
        pass
    cols = await _audio_metadata_column_names(db_path)
    assert {"id", "guid", "duration", "codec", "channels", "bitrate"} <= cols
```

- [ ] **Step 3.2 — Run to confirm failure**

```
uv run pytest tests/test_database_connection.py::test_foreign_keys_pragma_is_enforced tests/test_database_connection.py::test_episode_audio_metadata_table_exists -v
```
Expected: both `FAIL` (table doesn't exist, no pragma).

- [ ] **Step 3.3 — Implement**

In `database/connection.py`:

1. Rename the existing `_SCHEMA` constant to `_EPISODES_SCHEMA` (same SQL, different name).

2. Add a new constant immediately after it:

```python
_AUDIO_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS episode_audio_metadata (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guid     TEXT    NOT NULL UNIQUE REFERENCES episodes(guid),
    duration REAL    NOT NULL,
    codec    TEXT    NOT NULL,
    channels INTEGER NOT NULL,
    bitrate  INTEGER NOT NULL
)
"""
```

3. Update `Database.__aenter__` — replace:

```python
self.conn = await aiosqlite.connect(self._db_path)
await self.conn.execute(_SCHEMA)
await self.conn.commit()
```

with:

```python
self.conn = await aiosqlite.connect(self._db_path)
await self.conn.execute("PRAGMA foreign_keys = ON")
await self.conn.execute(_EPISODES_SCHEMA)
await self.conn.execute(_AUDIO_METADATA_SCHEMA)
await self.conn.commit()
```

- [ ] **Step 3.4 — Run tests + coverage + ruff**

```
uv run pytest tests/test_database_connection.py -v
uv run pytest --cov=database/connection.py tests/test_database_connection.py
uv run ruff check database/connection.py
```
Expected: all pass, 100% coverage.

- [ ] **Step 3.5 — Run full suite to confirm no regressions**

```
uv run pytest -v
```
Expected: all pass (the legacy migration test creates a raw DB without `PRAGMA foreign_keys = ON`, which is fine since it inserts no FK-constrained rows).

- [ ] **Step 3.6 — Commit**

```bash
git add database/connection.py tests/test_database_connection.py
git commit -m "feat: add FK enforcement and episode_audio_metadata schema"
```

---

## Task 4: `AudioMetadataStore`

**Files:**
- Create: `database/audio_metadata_store.py`
- Create: `tests/test_audio_metadata_store.py`

- [ ] **Step 4.1 — Write failing tests**

Create `tests/test_audio_metadata_store.py`:

```python
"""Tests for AudioMetadataStore."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from database.audio_metadata_store import AudioMetadataStore
from database.connection import Database
from database.episode_store import EpisodeStore
from models.feed import AudioMetadata, Episode


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _ep(guid: str) -> Episode:
    return Episode(guid=guid, url=f"https://example.com/{guid}.mp3", title=guid)


def _meta(guid: str) -> AudioMetadata:
    return AudioMetadata(guid=guid, duration=120.5, codec="aac", channels=2, bitrate=128000)


async def test_get_probed_guids_empty(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AudioMetadataStore(db.conn)
        result = await store.get_probed_guids()
    assert result == set()


async def test_get_probed_guids_after_save(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1"), _ep("ep-2")])
        meta_store = AudioMetadataStore(db.conn)
        await meta_store.save_all([_meta("ep-1")])
        result = await meta_store.get_probed_guids()
    assert result == {"ep-1"}


async def test_save_all_empty_is_noop(db_path: Path) -> None:
    async with Database(db_path) as db:
        store = AudioMetadataStore(db.conn)
        await store.save_all([])  # must not raise
        result = await store.get_probed_guids()
    assert result == set()


async def test_save_all_insert_or_ignore(db_path: Path) -> None:
    """Saving the same record twice must not raise and must store exactly one row."""
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        meta_store = AudioMetadataStore(db.conn)
        await meta_store.save_all([_meta("ep-1")])
        await meta_store.save_all([_meta("ep-1")])  # second save — must be silently ignored

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM episode_audio_metadata")
        (count,) = await cursor.fetchone()  # type: ignore[misc]
    assert count == 1


async def test_save_all_stores_correct_values(db_path: Path) -> None:
    async with Database(db_path) as db:
        ep_store = EpisodeStore(db.conn)
        await ep_store.save_episodes("pod", [_ep("ep-1")])
        meta_store = AudioMetadataStore(db.conn)
        await meta_store.save_all([
            AudioMetadata(guid="ep-1", duration=3661.5, codec="mp3", channels=1, bitrate=64000)
        ])

    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT guid, duration, codec, channels, bitrate FROM episode_audio_metadata"
        )
        row = await cursor.fetchone()

    assert row == ("ep-1", 3661.5, "mp3", 1, 64000)


async def test_save_all_raises_on_fk_violation(db_path: Path) -> None:
    """Inserting metadata for a GUID not in episodes must raise IntegrityError."""
    async with Database(db_path) as db:
        store = AudioMetadataStore(db.conn)
        with pytest.raises(aiosqlite.IntegrityError):
            await store.save_all([_meta("ghost-guid")])
```

- [ ] **Step 4.2 — Run to confirm failure**

```
uv run pytest tests/test_audio_metadata_store.py -v
```
Expected: `ImportError: No module named 'database.audio_metadata_store'`

- [ ] **Step 4.3 — Implement**

Create `database/audio_metadata_store.py`:

```python
"""AudioMetadata persistence against an open aiosqlite connection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from models.feed import AudioMetadata

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


class AudioMetadataStore:
    """Handles audio metadata persistence against an open aiosqlite connection.

    Expects the schema to already exist (created by Database).  Receives
    the connection rather than owning it — only Database manages the
    connection lifecycle.

    Args:
        conn: An open aiosqlite connection with episode_audio_metadata present.

    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_probed_guids(self) -> set[str]:
        """Return all GUIDs that already have a row in episode_audio_metadata.

        Used by the pipeline to filter out already-probed episodes before
        calling AudioProber.probe_all.

        Returns:
            Set of GUIDs with existing metadata rows.

        """
        async with self._conn.execute(
            "SELECT guid FROM episode_audio_metadata"
        ) as cursor:
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def save_all(self, records: list[AudioMetadata]) -> None:
        """Persist probe results, silently skipping any duplicate GUIDs.

        Args:
            records: Metadata records to persist.  Empty list is a no-op.

        """
        if not records:
            return
        rows = [(r.guid, r.duration, r.codec, r.channels, r.bitrate) for r in records]
        await self._conn.executemany(
            "INSERT OR IGNORE INTO episode_audio_metadata "
            "(guid, duration, codec, channels, bitrate) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        await self._conn.commit()
        logger.info(f"Saved {len(records)} audio metadata record(s)")
```

- [ ] **Step 4.4 — Run tests + coverage + ruff**

```
uv run pytest tests/test_audio_metadata_store.py -v
uv run pytest --cov=database/audio_metadata_store.py tests/test_audio_metadata_store.py
uv run ruff check database/audio_metadata_store.py
```
Expected: all pass, 100% coverage.

- [ ] **Step 4.5 — Commit**

```bash
git add database/audio_metadata_store.py tests/test_audio_metadata_store.py
git commit -m "feat: add AudioMetadataStore"
```

---

## Task 5: `AudioProber`

**Files:**
- Create: `components/audio_prober.py`
- Create: `tests/test_audio_prober.py`

### ffprobe JSON output format

The command run per episode:
```
ffprobe -v quiet -print_format json -show_streams -show_format <path>
```

Expected JSON structure:
```json
{
    "streams": [
        {
            "codec_type": "audio",
            "codec_name": "aac",
            "channels": 2,
            "duration": "3661.234000",
            "bit_rate": "0"
        }
    ],
    "format": {
        "bit_rate": "128000"
    }
}
```

Field extraction rules:
- `codec` ← `streams[0]["codec_name"]` (first stream with `codec_type == "audio"`)
- `channels` ← `streams[0]["channels"]`
- `duration` ← `float(streams[0]["duration"])`
- `bitrate` ← `int(format["bit_rate"])` if it exists and is not `"0"`, otherwise fall back to `int(streams[0].get("bit_rate", "0"))`

### Mock helper

Tests patch `asyncio.create_subprocess_exec` with an `AsyncMock`. Helper:

```python
from unittest.mock import AsyncMock, MagicMock

def _make_proc(stdout: bytes, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc
```

- [ ] **Step 5.1 — Write failing tests**

Create `tests/test_audio_prober.py`:

```python
"""Tests for AudioProber."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.audio_prober import AudioProber
from models.feed import AudioMetadata
from utils.exceptions import AudioProbeError


def _make_proc(stdout: bytes, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


def _ffprobe_json(
    *,
    codec: str = "aac",
    channels: int = 2,
    duration: str = "3661.234",
    stream_bitrate: str = "0",
    format_bitrate: str = "128000",
) -> bytes:
    return json.dumps({
        "streams": [{"codec_type": "audio", "codec_name": codec, "channels": channels, "duration": duration, "bit_rate": stream_bitrate}],
        "format": {"bit_rate": format_bitrate},
    }).encode()


GUID = "ep-abc"
PATH = Path("/cache/ep-abc.mp3")


@pytest.fixture
def prober() -> AudioProber:
    return AudioProber()


# ---------------------------------------------------------------------------
# _probe_one — happy path
# ---------------------------------------------------------------------------


async def test_probe_one_returns_metadata(prober: AudioProber) -> None:
    stdout = _ffprobe_json()
    mock_proc = _make_proc(stdout)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        result = await prober._probe_one(GUID, PATH)

    assert isinstance(result, AudioMetadata)
    assert result.guid == GUID
    assert result.codec == "aac"
    assert result.channels == 2
    assert abs(result.duration - 3661.234) < 0.001
    assert result.bitrate == 128000


async def test_probe_one_bitrate_fallback_to_stream(prober: AudioProber) -> None:
    """When format bit_rate is '0', falls back to stream bit_rate."""
    stdout = _ffprobe_json(format_bitrate="0", stream_bitrate="64000")
    mock_proc = _make_proc(stdout)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        result = await prober._probe_one(GUID, PATH)
    assert result.bitrate == 64000


# ---------------------------------------------------------------------------
# _probe_one — error cases
# ---------------------------------------------------------------------------


async def test_probe_one_raises_on_nonzero_exit(prober: AudioProber) -> None:
    mock_proc = _make_proc(b"", returncode=1)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(AudioProbeError, match="exited with code 1"):
            await prober._probe_one(GUID, PATH)


async def test_probe_one_raises_on_no_audio_stream(prober: AudioProber) -> None:
    stdout = json.dumps({"streams": [{"codec_type": "video"}], "format": {}}).encode()
    mock_proc = _make_proc(stdout)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(AudioProbeError, match="No audio stream"):
            await prober._probe_one(GUID, PATH)


async def test_probe_one_raises_on_timeout(prober: AudioProber) -> None:
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_make_proc(b""))):
        with patch("asyncio.wait_for", side_effect=TimeoutError):
            with pytest.raises(AudioProbeError, match="timed out"):
                await prober._probe_one(GUID, PATH)


async def test_probe_one_raises_on_missing_duration_field(prober: AudioProber) -> None:
    stdout = json.dumps({
        "streams": [{"codec_type": "audio", "codec_name": "aac", "channels": 2}],
        "format": {"bit_rate": "128000"},
    }).encode()
    mock_proc = _make_proc(stdout)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        with pytest.raises(AudioProbeError):
            await prober._probe_one(GUID, PATH)


# ---------------------------------------------------------------------------
# probe_all
# ---------------------------------------------------------------------------


async def test_probe_all_empty_list(prober: AudioProber) -> None:
    result = await prober.probe_all([])
    assert result == []


async def test_probe_all_returns_all_successes(prober: AudioProber) -> None:
    pairs = [(f"ep-{i}", Path(f"/cache/ep-{i}.mp3")) for i in range(3)]
    stdout = _ffprobe_json()
    mock_proc = _make_proc(stdout)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        results = await prober.probe_all(pairs)
    assert len(results) == 3
    assert [r.guid for r in results] == ["ep-0", "ep-1", "ep-2"]


async def test_probe_all_skips_failed_episodes(prober: AudioProber) -> None:
    """A failing probe is logged and skipped; successful ones are returned."""
    pairs = [("ep-ok", Path("/cache/ep-ok.mp3")), ("ep-fail", Path("/cache/ep-fail.mp3"))]
    ok_stdout = _ffprobe_json()
    fail_proc = _make_proc(b"", returncode=1)
    ok_proc = _make_proc(ok_stdout)

    call_count = 0

    async def fake_create(*args: object, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return ok_proc if call_count == 1 else fail_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        results = await prober.probe_all(pairs)

    assert len(results) == 1
    assert results[0].guid == "ep-ok"
```

- [ ] **Step 5.2 — Run to confirm failure**

```
uv run pytest tests/test_audio_prober.py -v
```
Expected: `ImportError: No module named 'components.audio_prober'`

- [ ] **Step 5.3 — Implement**

Create `components/audio_prober.py`:

```python
"""AudioProber — extracts codec/duration/channels/bitrate via ffprobe."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path  # noqa: TC003

from models.feed import AudioMetadata
from utils.exceptions import AudioProbeError

logger = logging.getLogger(__name__)


class AudioProber:
    """Probes downloaded episode files with ffprobe to extract audio metadata.

    Each call to :meth:`probe_all` runs ``ffprobe`` once per file via
    ``asyncio.create_subprocess_exec``.  Failed probes are logged and skipped;
    the episode is omitted from the returned list.

    This class has no dependency on the config module or the database.  The
    caller (Pipeline) is responsible for filtering already-probed episodes and
    persisting the results.

    Args:
        timeout: Seconds before an individual ffprobe call is cancelled.
            Default is 30.0.

    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def probe_all(
        self, pairs: list[tuple[str, Path]]
    ) -> list[AudioMetadata]:
        """Probe each (guid, path) pair and return successful results.

        Args:
            pairs: ``(guid, local_path)`` pairs to probe.  Order is preserved.

        Returns:
            :class:`~models.feed.AudioMetadata` for every episode probed
            successfully, in input order.  Failed episodes are omitted.

        """
        results: list[AudioMetadata] = []
        for guid, path in pairs:
            try:
                metadata = await self._probe_one(guid, path)
                results.append(metadata)
            except AudioProbeError as exc:
                logger.error(f"Skipping probe for '{guid}': {exc}")
        return results

    async def _probe_one(self, guid: str, path: Path) -> AudioMetadata:
        """Run ffprobe on one file and return parsed metadata.

        Args:
            guid: Episode GUID — used only in error messages and the result.
            path: Path to the local audio file.

        Raises:
            AudioProbeError: On non-zero ffprobe exit, timeout, missing audio
                stream, or unparseable JSON output.

        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_format",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            raise AudioProbeError(f"ffprobe timed out probing '{guid}'") from exc

        if proc.returncode != 0:
            raise AudioProbeError(
                f"ffprobe exited with code {proc.returncode} for '{guid}'"
            )

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise AudioProbeError(f"ffprobe produced invalid JSON for '{guid}'") from exc

        streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        if not streams:
            raise AudioProbeError(f"No audio stream found in ffprobe output for '{guid}'")

        stream = streams[0]
        fmt = data.get("format", {})
        format_bitrate = fmt.get("bit_rate", "0") or "0"
        bitrate = (
            int(format_bitrate) if format_bitrate != "0"
            else int(stream.get("bit_rate", "0"))
        )

        try:
            return AudioMetadata(
                guid=guid,
                duration=float(stream["duration"]),
                codec=str(stream["codec_name"]),
                channels=int(stream["channels"]),
                bitrate=bitrate,
            )
        except (KeyError, ValueError) as exc:
            raise AudioProbeError(
                f"Missing or invalid field in ffprobe output for '{guid}'"
            ) from exc
```

- [ ] **Step 5.4 — Run tests + coverage + ruff**

```
uv run pytest tests/test_audio_prober.py -v
uv run pytest --cov=components/audio_prober.py tests/test_audio_prober.py
uv run ruff check components/audio_prober.py
```
Expected: all pass, 100% coverage.

- [ ] **Step 5.5 — Commit**

```bash
git add components/audio_prober.py tests/test_audio_prober.py
git commit -m "feat: add AudioProber"
```

---

## Task 6: Pipeline integration

**Files:**
- Modify: `components/pipeline.py`
- Modify: `tests/test_pipeline.py`

### What changes in `pipeline.py`

1. **Imports** — add:
   ```python
   from components.audio_prober import AudioProber
   from database.audio_metadata_store import AudioMetadataStore
   ```
   Also add `AudioMetadata` to the models import if needed by type annotations.

2. **`Pipeline.__init__`** — add after `self._episode_downloader`:
   ```python
   self._audio_prober = AudioProber()
   ```

3. **`Pipeline.run`** — accumulate download results across feeds and run prober:

   Inside the `for feed in parsed_feeds` loop, replace:
   ```python
   await self._episode_downloader.download_all(
       episode_pairs,
       on_progress=self._on_download_progress,
   )
   ```
   with:
   ```python
   downloaded = await self._episode_downloader.download_all(
       episode_pairs,
       on_progress=self._on_download_progress,
   )
   all_downloaded.extend(downloaded)
   ```

   Before the loop, add `all_downloaded: list[tuple[str, Path]] = []`.

   After the loop (still inside the `async with Database` block), add:
   ```python
   if all_downloaded:
       audio_metadata_store = AudioMetadataStore(db.conn)
       probed_guids = await audio_metadata_store.get_probed_guids()
       unprobed = [(g, p) for g, p in all_downloaded if g not in probed_guids]
       probe_results = await self._audio_prober.probe_all(unprobed)
       await audio_metadata_store.save_all(probe_results)
   ```

   The `if all_downloaded:` guard ensures the DB store is never touched when nothing was downloaded — this preserves all existing tests without modification (they all return `[]` from `download_all`).

- [ ] **Step 6.1 — Write failing tests**

Add to `tests/test_pipeline.py`:

```python
from pathlib import Path
from components.audio_prober import AudioProber
from database.audio_metadata_store import AudioMetadataStore
from models.feed import AudioMetadata


async def test_pipeline_probes_downloaded_episodes() -> None:
    """After download, pipeline calls probe_all with the downloaded (guid, path) pairs."""
    feed_cfg = FeedConfig(title="My Pod", url="http://x.com/feed", enabled=True, episodes_to_keep=5)
    config = MagicMock()
    config.app.feeds = [feed_cfg]
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.base_url = "http://localhost"

    ep = Episode(guid="ep-001", url="https://example.com/ep.mp3", title="Ep 1",
                 pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    parsed = ParsedFeed(config_title="My Pod", feed_url="http://x.com/feed",
                        title="My Pod", episodes=[ep])
    cached_path = Path("/cache/ep-001.mp3")

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.FeedPublisher") as mock_pub_cls,
        patch("components.pipeline.EpisodeDownloader") as mock_ep_dl_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.AudioProber") as mock_prober_cls,
        patch("components.pipeline.AudioMetadataStore") as mock_meta_store_cls,
    ):
        mock_dl_cls.return_value.download_all = AsyncMock(return_value=[("My Pod", "<rss/>")])
        mock_fp_cls.return_value.parse_all.return_value = [parsed]
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store_cls.return_value.save_episodes = AsyncMock()
        mock_store_cls.return_value.get_episodes_for_feed = AsyncMock(return_value=[ep])
        mock_pub_cls.return_value.publish = AsyncMock(return_value=Path("/output/my-pod.rss"))
        mock_ep_dl_cls.return_value.download_all = AsyncMock(return_value=[("ep-001", cached_path)])
        mock_prober = AsyncMock()
        mock_prober.probe_all = AsyncMock(return_value=[
            AudioMetadata(guid="ep-001", duration=120.0, codec="aac", channels=2, bitrate=128000)
        ])
        mock_prober_cls.return_value = mock_prober
        mock_meta_store = AsyncMock()
        mock_meta_store.get_probed_guids = AsyncMock(return_value=set())
        mock_meta_store.save_all = AsyncMock()
        mock_meta_store_cls.return_value = mock_meta_store

        pipeline = Pipeline(config)
        await pipeline.run()

    mock_prober.probe_all.assert_awaited_once_with([("ep-001", cached_path)])
    mock_meta_store.save_all.assert_awaited_once()


async def test_pipeline_filters_already_probed_guids() -> None:
    """Episodes already in episode_audio_metadata are not re-probed."""
    feed_cfg = FeedConfig(title="My Pod", url="http://x.com/feed", enabled=True, episodes_to_keep=5)
    config = MagicMock()
    config.app.feeds = [feed_cfg]
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.base_url = "http://localhost"

    ep = Episode(guid="ep-001", url="https://example.com/ep.mp3", title="Ep 1",
                 pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    parsed = ParsedFeed(config_title="My Pod", feed_url="http://x.com/feed",
                        title="My Pod", episodes=[ep])
    cached_path = Path("/cache/ep-001.mp3")

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.FeedPublisher") as mock_pub_cls,
        patch("components.pipeline.EpisodeDownloader") as mock_ep_dl_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.AudioProber") as mock_prober_cls,
        patch("components.pipeline.AudioMetadataStore") as mock_meta_store_cls,
    ):
        mock_dl_cls.return_value.download_all = AsyncMock(return_value=[("My Pod", "<rss/>")])
        mock_fp_cls.return_value.parse_all.return_value = [parsed]
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store_cls.return_value.save_episodes = AsyncMock()
        mock_store_cls.return_value.get_episodes_for_feed = AsyncMock(return_value=[ep])
        mock_pub_cls.return_value.publish = AsyncMock(return_value=Path("/output/my-pod.rss"))
        mock_ep_dl_cls.return_value.download_all = AsyncMock(return_value=[("ep-001", cached_path)])
        mock_prober = AsyncMock()
        mock_prober.probe_all = AsyncMock(return_value=[])
        mock_prober_cls.return_value = mock_prober
        mock_meta_store = AsyncMock()
        # ep-001 is already probed — pipeline must pass empty list to probe_all
        mock_meta_store.get_probed_guids = AsyncMock(return_value={"ep-001"})
        mock_meta_store.save_all = AsyncMock()
        mock_meta_store_cls.return_value = mock_meta_store

        pipeline = Pipeline(config)
        await pipeline.run()

    mock_prober.probe_all.assert_awaited_once_with([])


async def test_pipeline_skips_prober_when_nothing_downloaded() -> None:
    """When download_all returns [], AudioProber and AudioMetadataStore are never called."""
    feed_cfg = FeedConfig(title="My Pod", url="http://x.com/feed", enabled=True, episodes_to_keep=5)
    config = MagicMock()
    config.app.feeds = [feed_cfg]
    config.app.paths.data_dir = MagicMock()
    config.app.paths.output_dir = MagicMock()
    config.app.paths.cache_dir = MagicMock()
    config.app.base_url = "http://localhost"

    ep = Episode(guid="ep-001", url="https://example.com/ep.mp3", title="Ep 1",
                 pub_date=datetime(2026, 3, 22, tzinfo=UTC))
    parsed = ParsedFeed(config_title="My Pod", feed_url="http://x.com/feed",
                        title="My Pod", episodes=[ep])

    with (
        patch("components.pipeline.FeedDownloader") as mock_dl_cls,
        patch("components.pipeline.FeedParser") as mock_fp_cls,
        patch("components.pipeline.FeedPublisher") as mock_pub_cls,
        patch("components.pipeline.EpisodeDownloader") as mock_ep_dl_cls,
        patch("components.pipeline.Database") as mock_db_cls,
        patch("components.pipeline.EpisodeStore") as mock_store_cls,
        patch("components.pipeline.AudioProber") as mock_prober_cls,
        patch("components.pipeline.AudioMetadataStore") as mock_meta_store_cls,
    ):
        mock_dl_cls.return_value.download_all = AsyncMock(return_value=[("My Pod", "<rss/>")])
        mock_fp_cls.return_value.parse_all.return_value = [parsed]
        mock_db = MagicMock()
        mock_db_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_store_cls.return_value.save_episodes = AsyncMock()
        mock_store_cls.return_value.get_episodes_for_feed = AsyncMock(return_value=[ep])
        mock_pub_cls.return_value.publish = AsyncMock(return_value=Path("/output/my-pod.rss"))
        # download_all returns nothing — prober should be entirely skipped
        mock_ep_dl_cls.return_value.download_all = AsyncMock(return_value=[])
        mock_prober_cls.return_value = AsyncMock()
        mock_meta_store_cls.return_value = AsyncMock()

        pipeline = Pipeline(config)
        await pipeline.run()

    mock_prober_cls.return_value.probe_all.assert_not_awaited()
    mock_meta_store_cls.return_value.get_probed_guids.assert_not_awaited()
```

- [ ] **Step 6.2 — Run to confirm failure**

```
uv run pytest tests/test_pipeline.py::test_pipeline_probes_downloaded_episodes tests/test_pipeline.py::test_pipeline_filters_already_probed_guids tests/test_pipeline.py::test_pipeline_skips_prober_when_nothing_downloaded -v
```
Expected: `FAIL` or `ImportError` (AudioProber not yet imported in pipeline).

- [ ] **Step 6.3 — Implement**

Edit `components/pipeline.py`:

**Imports** — add after `from components.episode_downloader import EpisodeDownloader`:
```python
from components.audio_prober import AudioProber
from database.audio_metadata_store import AudioMetadataStore
```

**`Pipeline.__init__`** — add after `self._episode_downloader = EpisodeDownloader(...)`:
```python
self._audio_prober = AudioProber()
```

**`Pipeline.run`** — full updated method body (only the relevant changed section shown):

Before `async with Database(self._db_path) as db:`, move/keep the existing code.
Inside the `async with Database(...) as db:` block, add `all_downloaded: list[tuple[str, Path]] = []` before the `for feed in parsed_feeds` loop.

Inside the loop, replace:
```python
await self._episode_downloader.download_all(
    episode_pairs,
    on_progress=self._on_download_progress,
)
```
with:
```python
downloaded = await self._episode_downloader.download_all(
    episode_pairs,
    on_progress=self._on_download_progress,
)
all_downloaded.extend(downloaded)
```

After the loop (still inside `async with Database`):
```python
if all_downloaded:
    audio_metadata_store = AudioMetadataStore(db.conn)
    probed_guids = await audio_metadata_store.get_probed_guids()
    unprobed = [(g, p) for g, p in all_downloaded if g not in probed_guids]
    probe_results = await self._audio_prober.probe_all(unprobed)
    await audio_metadata_store.save_all(probe_results)
```

Also add `Path` to the `TYPE_CHECKING` import block in `pipeline.py` if not already present (it is already there).

- [ ] **Step 6.4 — Run the new tests**

```
uv run pytest tests/test_pipeline.py::test_pipeline_probes_downloaded_episodes tests/test_pipeline.py::test_pipeline_filters_already_probed_guids tests/test_pipeline.py::test_pipeline_skips_prober_when_nothing_downloaded -v
```
Expected: all `PASS`.

- [ ] **Step 6.5 — Run full test suite**

```
uv run pytest -v
```
Expected: all pass (existing pipeline tests are unaffected because they all return `[]` from `download_all`, which triggers the `if all_downloaded:` guard).

- [ ] **Step 6.6 — Run coverage for pipeline**

```
uv run pytest --cov=components/pipeline.py tests/test_pipeline.py
```
Expected: 100%.

- [ ] **Step 6.7 — Run full project coverage**

```
uv run pytest --cov=.
```
Expected: 100% across all files.

- [ ] **Step 6.8 — Run ruff**

```
uv run ruff check components/pipeline.py
```
Expected: no errors.

- [ ] **Step 6.9 — Commit**

```bash
git add components/pipeline.py tests/test_pipeline.py
git commit -m "feat: wire AudioProber into pipeline"
```

---

## Final Verification

- [ ] **Run full suite one last time**

```
uv run pytest --cov=. -v
uv run ruff check
```
Expected: all tests pass, 100% coverage, zero ruff errors.
