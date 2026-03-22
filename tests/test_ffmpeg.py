"""Tests for Ffmpeg — async ffmpeg subprocess runner."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from utils.exceptions import FfmpegError
from utils.ffmpeg import Ffmpeg

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_proc(
    stdout_lines: list[bytes] | None = None,
    returncode: int = 0,
    stderr_output: bytes = b"",
) -> object:
    """Build a minimal mock asyncio Process with readline()/read()/wait()."""
    lines = list(stdout_lines or [])

    class MockStderr:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._consumed = False

        async def read(self, n: int = -1) -> bytes:
            if self._consumed:
                return b""
            self._consumed = True
            return self._data[:n] if n >= 0 else self._data

    class MockProcess:
        """Minimal mock of asyncio.subprocess.Process."""

        def __init__(self) -> None:
            self.returncode = returncode
            self._lines = lines
            self._idx = 0
            self.stdout = self  # self is the async stream
            self.stderr = MockStderr(stderr_output)

        async def readline(self) -> bytes:
            if self._idx < len(self._lines):
                line = self._lines[self._idx]
                self._idx += 1
                return line
            return b""

        async def wait(self) -> int:
            return self.returncode

    return MockProcess()


def _make_async_create(proc: object) -> object:
    """Return an async factory that always returns *proc*."""
    async def fake_create(*args: object, **kwargs: object) -> object:
        return proc
    return fake_create


# ---------------------------------------------------------------------------
# run() — single branch (full progress with stderr capture)
# ---------------------------------------------------------------------------


async def test_run_success() -> None:
    """run() succeeds when ffmpeg exits 0."""
    proc = _make_proc(returncode=0)
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=10.0)

    assert calls[-1] == 1.0


async def test_run_nonzero_exit_raises() -> None:
    """run() raises FfmpegError when ffmpeg exits non-zero."""
    proc = _make_proc(returncode=1)
    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        with pytest.raises(FfmpegError, match="1"):
            await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=AsyncMock(), duration=10.0)


async def test_run_nonzero_exit_includes_stderr_in_error() -> None:
    """FfmpegError message must include the captured stderr text."""
    proc = _make_proc(returncode=1, stderr_output=b"No such file or directory\n")
    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        with pytest.raises(FfmpegError) as exc_info:
            await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=AsyncMock(), duration=10.0)

    assert "No such file or directory" in str(exc_info.value)
    assert exc_info.value.stderr == "No such file or directory\n"


async def test_run_success_stderr_not_in_error() -> None:
    """On success, no FfmpegError is raised even if stderr has content."""
    proc = _make_proc(returncode=0, stderr_output=b"Some warning\n")
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=10.0)  # must not raise

    assert calls[-1] == 1.0


async def test_run_uses_pipe_for_stdout_and_stderr() -> None:
    """run() must use PIPE for both stdout and stderr."""
    proc = _make_proc()
    captured: dict[str, object] = {}

    async def recording_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return proc

    async def cb(pct: float) -> None:
        pass

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=recording_create):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=10.0)

    assert captured["stdout"] == asyncio.subprocess.PIPE
    assert captured["stderr"] == asyncio.subprocess.PIPE


async def test_run_prepends_progress_flags() -> None:
    """run() must prepend -progress pipe:1 -nostats to the ffmpeg args."""
    proc = _make_proc()
    captured: list[object] = []

    async def recording_create(*args: object, **kwargs: object) -> object:
        captured.extend(args)
        return proc

    async def cb(pct: float) -> None:
        pass

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=recording_create):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=10.0)

    assert list(captured) == [
        "ffmpeg", "-progress", "pipe:1", "-nostats", "-i", "in.mp3", "out.m4a"
    ]


async def test_run_emits_intermediate_progress_values() -> None:
    """out_time_ms lines are parsed (microseconds) and converted to 0.0-1.0 progress."""
    # duration=2.0s; 500000µs → 0.25; 1000000µs → 0.5
    progress_lines = [
        b"frame=1\n",
        b"out_time_ms=500000\n",
        b"bitrate=N/A\n",
        b"out_time_ms=1000000\n",
    ]
    proc = _make_proc(stdout_lines=progress_lines)
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=2.0)

    assert 0.25 in calls
    assert 0.5 in calls
    assert calls[-1] == 1.0


async def test_run_clamps_progress_to_1() -> None:
    """out_time_ms value exceeding duration still emits at most 1.0 (not > 1.0)."""
    proc = _make_proc(stdout_lines=[b"out_time_ms=9999999999\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=1.0)

    assert all(v <= 1.0 for v in calls)


async def test_run_ignores_non_out_time_lines() -> None:
    """Lines that don't start with out_time_ms= must not trigger on_progress."""
    proc = _make_proc(stdout_lines=[b"frame=5\n", b"speed=1.2x\n", b"progress=continue\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=10.0)

    # Only the final 1.0 should be emitted (from our explicit call after wait)
    assert calls == [1.0]


async def test_run_ignores_out_time_ms_na() -> None:
    """out_time_ms=N/A must be silently skipped, not crash with ValueError."""
    proc = _make_proc(stdout_lines=[b"out_time_ms=N/A\n", b"out_time_ms=500000\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=2.0)

    assert 0.25 in calls
    assert 1.0 not in calls[:-1]  # N/A line must not have emitted anything unexpected


async def test_run_nonzero_exit_does_not_call_final_progress() -> None:
    """FfmpegError raised; on_progress(1.0) must NOT be called."""
    proc = _make_proc(stdout_lines=[b"out_time_ms=500000\n"], returncode=1)
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        with pytest.raises(FfmpegError):
            await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=2.0)

    assert 1.0 not in calls


async def test_run_emits_final_1_on_success() -> None:
    """After stdout is exhausted and exit code is 0, on_progress(1.0) is called."""
    proc = _make_proc(stdout_lines=[b"out_time_ms=100000\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=1.0)

    assert calls[-1] == 1.0
