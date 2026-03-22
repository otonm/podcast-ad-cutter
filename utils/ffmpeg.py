"""Ffmpeg — async wrapper for invoking ffmpeg as a subprocess."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from utils.exceptions import FfmpegError

logger = logging.getLogger(__name__)

type ProgressCallback = Callable[[float], Awaitable[None]]


class Ffmpeg:
    """Runs ffmpeg as an async subprocess with optional progress reporting.

    Accepts arbitrary ffmpeg arguments (not including the ``ffmpeg`` binary
    itself).  When a progress callback and a total duration are supplied,
    ffmpeg is invoked with ``-progress pipe:1 -nostats`` and the
    ``out_time_ms`` progress lines are parsed to emit fractional progress
    values in ``[0.0, 1.0]``.

    """

    async def run(
        self,
        args: list[str],
        on_progress: ProgressCallback | None = None,
        duration: float | None = None,
    ) -> None:
        """Run ``ffmpeg`` with the given arguments.

        Args:
            args: ffmpeg arguments, not including the ``ffmpeg`` binary itself.
            on_progress: Optional async callback receiving a float in
                ``[0.0, 1.0]``.  ``0.0`` signals start; ``1.0`` signals
                completion.  Intermediate values are only emitted when
                *duration* is also provided.
            duration: Total duration in seconds used to compute fractional
                progress from ``out_time_ms`` output.  Ignored when
                *on_progress* is ``None``.

        Raises:
            FfmpegError: If ffmpeg exits with a non-zero return code.

        """
        if on_progress is None:
            await self._run_silent(args)
        elif duration is None:
            await self._run_with_bookend_progress(args, on_progress)
        else:
            await self._run_with_full_progress(args, on_progress, duration)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _run_silent(self, args: list[str]) -> None:
        """Branch A: run ffmpeg with all output suppressed."""
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise FfmpegError(f"ffmpeg exited with code {proc.returncode}")

    async def _run_with_bookend_progress(
        self, args: list[str], on_progress: ProgressCallback
    ) -> None:
        """Branch B: emit 0.0 at start and 1.0 at end; no intermediate values."""
        await on_progress(0.0)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Drain stdout to prevent pipe buffer deadlock.
        while await proc.stdout.readline():  # type: ignore[union-attr]
            pass
        await proc.wait()
        if proc.returncode != 0:
            raise FfmpegError(f"ffmpeg exited with code {proc.returncode}")
        await on_progress(1.0)

    async def _run_with_full_progress(
        self, args: list[str], on_progress: ProgressCallback, duration: float
    ) -> None:
        """Branch C: parse out_time_ms lines to emit fractional progress."""
        full_args = ["-progress", "pipe:1", "-nostats", *args]
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *full_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        while line := await proc.stdout.readline():  # type: ignore[union-attr]
            decoded = line.decode().strip()
            if decoded.startswith("out_time_ms="):
                out_time_us = int(decoded.split("=", 1)[1])
                pct = min(out_time_us / 1_000_000 / duration, 1.0)
                await on_progress(pct)
        await proc.wait()
        if proc.returncode != 0:
            raise FfmpegError(f"ffmpeg exited with code {proc.returncode}")
        await on_progress(1.0)
