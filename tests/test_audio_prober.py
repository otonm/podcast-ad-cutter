"""Tests for AudioProber."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from components.audio_prober import AudioProber
from models.feed import AudioMetadata
from utils.exceptions import AudioProbeError


def _make_proc(stdout: bytes, returncode: int = 0) -> object:
    class MockProcess:
        """Mock process object that mimics asyncio.subprocess.Process."""

        def __init__(self) -> None:
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return (stdout, b"")

    return MockProcess()


def _ffprobe_json(
    *,
    codec: str = "aac",
    channels: int = 2,
    duration: str = "3661.234",
    stream_bitrate: str = "0",
    format_bitrate: str = "128000",
) -> bytes:
    stream = {
        "codec_type": "audio",
        "codec_name": codec,
        "channels": channels,
        "duration": duration,
        "bit_rate": stream_bitrate,
    }
    return json.dumps({"streams": [stream], "format": {"bit_rate": format_bitrate}}).encode()


def _make_async_create(proc: object) -> object:
    """Create an async function that returns a given process mock."""
    async def fake_create(*args: object, **kwargs: object) -> object:
        return proc
    return fake_create


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
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
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
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        result = await prober._probe_one(GUID, PATH)
    assert result.bitrate == 64000


# ---------------------------------------------------------------------------
# _probe_one — error cases
# ---------------------------------------------------------------------------


async def test_probe_one_raises_on_nonzero_exit(prober: AudioProber) -> None:
    mock_proc = _make_proc(b"", returncode=1)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        with pytest.raises(AudioProbeError, match="exited with code 1"):
            await prober._probe_one(GUID, PATH)


async def test_probe_one_raises_on_no_audio_stream(prober: AudioProber) -> None:
    stdout = json.dumps({"streams": [{"codec_type": "video"}], "format": {}}).encode()
    mock_proc = _make_proc(stdout)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        with pytest.raises(AudioProbeError, match="No audio stream"):
            await prober._probe_one(GUID, PATH)


async def test_probe_one_raises_on_timeout(prober: AudioProber) -> None:
    async def wait_for_timeout(coro: object, **kwargs: object) -> object:  # type: ignore[no-untyped-def]
        # Close the coroutine to avoid "was never awaited" warnings
        if hasattr(coro, "close"):
            coro.close()  # type: ignore[attr-defined]
        raise TimeoutError

    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(_make_proc(b""))):
        with patch("components.audio_prober.asyncio.wait_for", new=wait_for_timeout):
            with pytest.raises(AudioProbeError, match="timed out"):
                await prober._probe_one(GUID, PATH)


async def test_probe_one_raises_on_invalid_json(prober: AudioProber) -> None:
    mock_proc = _make_proc(b"not valid json")
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        with pytest.raises(AudioProbeError, match="invalid JSON"):
            await prober._probe_one(GUID, PATH)


async def test_probe_one_raises_on_missing_duration_field(prober: AudioProber) -> None:
    stdout = json.dumps({
        "streams": [{"codec_type": "audio", "codec_name": "aac", "channels": 2}],
        "format": {"bit_rate": "128000"},
    }).encode()
    mock_proc = _make_proc(stdout)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
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

    async def fake_create(*args: object, **kwargs: object) -> object:
        # Create a fresh mock each time with a new _communicate function
        return _make_proc(stdout)

    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=fake_create):
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

    async def fake_create(*args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        return ok_proc if call_count == 1 else fail_proc

    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=fake_create):
        results = await prober.probe_all(pairs)

    assert len(results) == 1
    assert results[0].guid == "ep-ok"
