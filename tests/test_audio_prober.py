"""Tests for AudioProber."""

from __future__ import annotations

import json
import logging
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


async def test_probe_returns_metadata(prober: AudioProber) -> None:
    stdout = _ffprobe_json()
    mock_proc = _make_proc(stdout)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        result = await prober.probe(GUID, PATH)

    assert isinstance(result, AudioMetadata)
    assert result.guid == GUID
    assert result.codec == "aac"
    assert result.channels == 2
    assert abs(result.duration - 3661.234) < 0.001
    assert result.bitrate == 128000


async def test_probe_bitrate_fallback_to_stream(prober: AudioProber) -> None:
    """When format bit_rate is '0', falls back to stream bit_rate."""
    stdout = _ffprobe_json(format_bitrate="0", stream_bitrate="64000")
    mock_proc = _make_proc(stdout)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        result = await prober.probe(GUID, PATH)
    assert result.bitrate == 64000


async def test_probe_raises_on_nonzero_exit(prober: AudioProber) -> None:
    mock_proc = _make_proc(b"", returncode=1)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        with pytest.raises(AudioProbeError, match="exited with code 1"):
            await prober.probe(GUID, PATH)


async def test_probe_raises_on_no_audio_stream(prober: AudioProber) -> None:
    stdout = json.dumps({"streams": [{"codec_type": "video"}], "format": {}}).encode()
    mock_proc = _make_proc(stdout)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        with pytest.raises(AudioProbeError, match="No audio stream"):
            await prober.probe(GUID, PATH)


async def test_probe_raises_on_timeout(prober: AudioProber) -> None:
    async def wait_for_timeout(coro: object, **kwargs: object) -> object:  # type: ignore[no-untyped-def]
        if hasattr(coro, "close"):
            coro.close()  # type: ignore[attr-defined]
        raise TimeoutError

    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(_make_proc(b""))):
        with patch("components.audio_prober.asyncio.wait_for", new=wait_for_timeout):
            with pytest.raises(AudioProbeError, match="timed out"):
                await prober.probe(GUID, PATH)


async def test_probe_raises_on_invalid_json(prober: AudioProber) -> None:
    mock_proc = _make_proc(b"not valid json")
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        with pytest.raises(AudioProbeError, match="invalid JSON"):
            await prober.probe(GUID, PATH)


async def test_probe_raises_on_missing_duration_field(prober: AudioProber) -> None:
    stdout = json.dumps({
        "streams": [{"codec_type": "audio", "codec_name": "aac", "channels": 2}],
        "format": {"bit_rate": "128000"},
    }).encode()
    mock_proc = _make_proc(stdout)
    with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
        with pytest.raises(AudioProbeError):
            await prober.probe(GUID, PATH)


async def test_probe_logs_debug_on_success(prober: AudioProber, caplog: pytest.LogCaptureFixture) -> None:
    """Successful probe emits a debug log with codec/duration/channels/bitrate."""
    stdout = _ffprobe_json(codec="aac", channels=2, duration="3661.234", format_bitrate="128000")
    mock_proc = _make_proc(stdout)
    with caplog.at_level(logging.DEBUG, logger="components.audio_prober"):
        with patch("components.audio_prober.asyncio.create_subprocess_exec", new=_make_async_create(mock_proc)):
            await prober.probe(GUID, PATH)

    assert any("ep-abc" in r.message and "aac" in r.message for r in caplog.records)
