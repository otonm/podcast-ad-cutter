"""Ffmpeg — async wrapper for invoking ffmpeg as a subprocess."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from utils.exceptions import FfmpegError

logger = logging.getLogger(__name__)

type ProgressCallback = Callable[[float], Awaitable[None]]


class Ffmpeg:
    """Runs ffmpeg as an async subprocess with progress reporting.

    Accepts arbitrary ffmpeg arguments (not including the ``ffmpeg`` binary
    itself).  ffmpeg is invoked with ``-progress pipe:1 -nostats`` and the
    ``out_time_ms`` progress lines are parsed to emit fractional progress
    values in ``[0.0, 1.0]``.  stderr is captured concurrently and included
    in :class:`~utils.exceptions.FfmpegError` when ffmpeg exits non-zero.

    """

    async def run(
        self,
        args: list[str],
        on_progress: ProgressCallback,
        duration: float,
    ) -> None:
        """Run ``ffmpeg`` with the given arguments.

        Args:
            args: ffmpeg arguments, not including the ``ffmpeg`` binary itself.
            on_progress: Async callback receiving a float in ``[0.0, 1.0]``.
                Intermediate values are emitted from ``out_time_ms`` output;
                ``1.0`` signals completion.
            duration: Total duration in seconds used to compute fractional
                progress from ``out_time_ms`` output.

        Raises:
            FfmpegError: If ffmpeg exits with a non-zero return code.  The
                error message includes the captured stderr output.

        """
        full_args = ["-progress", "pipe:1", "-nostats", *args]
        logger.debug(f"Running ffmpeg with args: {full_args}")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            *full_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_task = asyncio.create_task(self._drain_stderr(proc))

        while line := await proc.stdout.readline():  # type: ignore[union-attr]
            decoded = line.decode().strip()

            if decoded.startswith("out_time_ms="):
                raw = decoded.split("=", 1)[1]
                if raw.lstrip("-").isdigit():
                    out_time_us = int(raw)
                    pct = min(out_time_us / 1_000_000 / duration, 1.0)
                    await on_progress(pct)

        await proc.wait()
        stderr_output = await stderr_task

        if proc.returncode != 0:
            raise FfmpegError(
                f"ffmpeg exited with code {proc.returncode}", stderr=stderr_output
            )
        await on_progress(1.0)

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> str:
        chunks: list[bytes] = []
        while chunk := await proc.stderr.read(4096):  # type: ignore[union-attr]
            chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")
