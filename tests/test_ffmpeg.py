"""Tests for Ffmpeg — async ffmpeg subprocess runner."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from utils.exceptions import FfmpegError
from utils.ffmpeg import Ffmpeg

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _make_proc(stdout_lines: list[bytes] | None = None, returncode: int = 0) -> object:
    """Build a minimal mock asyncio Process with readline()/wait()."""
    lines = list(stdout_lines or [])

    class MockProcess:
        """Minimal mock of asyncio.subprocess.Process."""

        def __init__(self) -> None:
            self.returncode = returncode
            self._lines = lines
            self._idx = 0
            self.stdout = self  # self is the async stream

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
# Branch A — no progress callback
# ---------------------------------------------------------------------------


async def test_run_no_progress_success() -> None:
    """run() with no callback succeeds when ffmpeg exits 0."""
    proc = _make_proc(returncode=0)
    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"])  # must not raise


async def test_run_no_progress_nonzero_exit_raises() -> None:
    """run() raises FfmpegError when ffmpeg exits non-zero."""
    proc = _make_proc(returncode=1)
    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        with pytest.raises(FfmpegError, match="1"):
            await Ffmpeg().run(["-i", "in.mp3", "out.m4a"])


async def test_run_no_progress_uses_devnull() -> None:
    """run() without callback passes DEVNULL for stdout and stderr."""
    proc = _make_proc()
    captured: dict[str, object] = {}

    async def recording_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return proc

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=recording_create):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"])

    assert captured["stdout"] == asyncio.subprocess.DEVNULL
    assert captured["stderr"] == asyncio.subprocess.DEVNULL


async def test_run_no_progress_passes_args_verbatim() -> None:
    """run() prepends 'ffmpeg' and passes args unchanged."""
    proc = _make_proc()
    captured: list[object] = []

    async def recording_create(*args: object, **kwargs: object) -> object:
        captured.extend(args)
        return proc

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=recording_create):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"])

    assert list(captured) == ["ffmpeg", "-i", "in.mp3", "out.m4a"]


# ---------------------------------------------------------------------------
# Branch B — progress callback, duration=None
# ---------------------------------------------------------------------------


async def test_run_progress_no_duration_emits_start_and_end() -> None:
    """With on_progress but no duration, callback receives exactly 0.0 then 1.0."""
    proc = _make_proc(stdout_lines=[b"frame=1\n", b"bitrate=N/A\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb)

    assert calls == [0.0, 1.0]


async def test_run_progress_no_duration_uses_pipe_stdout() -> None:
    """Branch B must capture stdout via PIPE so it can be drained."""
    proc = _make_proc()
    captured: dict[str, object] = {}

    async def recording_create(*args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return proc

    async def cb(pct: float) -> None:
        pass

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=recording_create):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb)

    assert captured["stdout"] == asyncio.subprocess.PIPE


async def test_run_progress_no_duration_nonzero_exit_raises_without_final_cb() -> None:
    """Branch B: FfmpegError raised; on_progress(1.0) must NOT be called."""
    proc = _make_proc(returncode=1)
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        with pytest.raises(FfmpegError):
            await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb)

    assert 1.0 not in calls


# ---------------------------------------------------------------------------
# Branch C — progress callback + duration
# ---------------------------------------------------------------------------


async def test_run_progress_with_duration_prepends_progress_flags() -> None:
    """Branch C must prepend -progress pipe:1 -nostats to the ffmpeg args."""
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


async def test_run_progress_with_duration_emits_intermediate_values() -> None:
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


async def test_run_progress_with_duration_clamps_to_1() -> None:
    """out_time_ms value exceeding duration still emits at most 1.0 (not > 1.0)."""
    proc = _make_proc(stdout_lines=[b"out_time_ms=9999999999\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=1.0)

    assert all(v <= 1.0 for v in calls)


async def test_run_progress_with_duration_ignores_non_out_time_lines() -> None:
    """Lines that don't start with out_time_ms= must not trigger on_progress."""
    proc = _make_proc(stdout_lines=[b"frame=5\n", b"speed=1.2x\n", b"progress=continue\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=10.0)

    # Only the final 1.0 should be emitted (from our explicit call after wait)
    assert calls == [1.0]


async def test_run_progress_with_duration_nonzero_exit_raises_without_final_cb() -> None:
    """Branch C: FfmpegError raised; on_progress(1.0) must NOT be called."""
    proc = _make_proc(stdout_lines=[b"out_time_ms=500000\n"], returncode=1)
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        with pytest.raises(FfmpegError):
            await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=2.0)

    assert 1.0 not in calls


async def test_run_progress_with_duration_emits_final_1_on_success() -> None:
    """After stdout is exhausted and exit code is 0, on_progress(1.0) is called."""
    proc = _make_proc(stdout_lines=[b"out_time_ms=100000\n"])
    calls: list[float] = []

    async def cb(pct: float) -> None:
        calls.append(pct)

    with patch("utils.ffmpeg.asyncio.create_subprocess_exec", new=_make_async_create(proc)):
        await Ffmpeg().run(["-i", "in.mp3", "out.m4a"], on_progress=cb, duration=1.0)

    assert calls[-1] == 1.0
