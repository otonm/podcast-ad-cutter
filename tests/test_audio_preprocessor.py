"""Tests for AudioPreprocessor."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from components.audio_preprocessor import AudioPreprocessor
from utils.exceptions import FfmpegError

GUID = "ep-abc123"
INPUT_PATH = Path("/cache/ep-abc123.mp3")
DURATION = 120.0


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Return a cache directory path that does NOT yet exist."""
    return tmp_path / "cache"


@pytest.fixture
def preprocessor(cache_dir: Path) -> AudioPreprocessor:
    return AudioPreprocessor(cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_preprocess_all_empty_list(preprocessor: AudioPreprocessor) -> None:
    """preprocess_all returns [] without calling ffmpeg when given no pairs."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        result = await preprocessor.preprocess_all([])

    assert result == []
    mock_cls.return_value.run.assert_not_called()


async def test_preprocess_all_returns_output_path(
    preprocessor: AudioPreprocessor, cache_dir: Path
) -> None:
    """Returns (guid, cache_dir/{guid}.mono.m4a) on success."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        result = await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)])

    assert len(result) == 1
    guid, path = result[0]
    assert guid == GUID
    assert path == cache_dir / f"{GUID}.mono.m4a"


async def test_preprocess_all_creates_cache_dir(
    preprocessor: AudioPreprocessor, cache_dir: Path
) -> None:
    """cache_dir is created automatically."""
    assert not cache_dir.exists()  # noqa: ASYNC240
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)])

    assert cache_dir.is_dir()  # noqa: ASYNC240


async def test_preprocess_all_preserves_order(
    preprocessor: AudioPreprocessor, cache_dir: Path
) -> None:
    """Output list preserves the input order."""
    pairs = [(f"ep-{i}", Path(f"/cache/ep-{i}.mp3"), DURATION) for i in range(3)]
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        result = await preprocessor.preprocess_all(pairs)

    assert [guid for guid, _ in result] == ["ep-0", "ep-1", "ep-2"]


async def test_preprocess_all_correct_ffmpeg_args(
    preprocessor: AudioPreprocessor, cache_dir: Path
) -> None:
    """Ffmpeg.run() is called with the exact mono-AAC conversion arguments and duration."""
    expected_output = cache_dir / f"{GUID}.mono.m4a"
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)])

    call_args = mock_run.call_args
    assert call_args[0][0] == [
        "-i", str(INPUT_PATH),
        "-ac", "1",
        "-c:a", "aac",
        "-b:a", "32k",
        "-map_metadata", "-1",
        "-y",
        str(expected_output),
    ]
    assert call_args[1]["duration"] == DURATION


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


async def test_preprocess_all_no_progress_callback(preprocessor: AudioPreprocessor) -> None:
    """on_progress=None works without error; Ffmpeg.run is still called."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        result = await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)], on_progress=None)

    assert len(result) == 1


async def test_preprocess_all_progress_start_and_end(
    preprocessor: AudioPreprocessor,
) -> None:
    """With a callback, on_progress(guid, 0.0) and on_progress(guid, 1.0) are called."""
    calls: list[tuple[str, float]] = []

    async def cb(guid: str, pct: float) -> None:
        calls.append((guid, pct))

    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        # Simulate Ffmpeg.run invoking the wrapper with 0.0 and 1.0
        async def fake_run(
            args: list[str],
            on_progress: object = None,
            duration: object = None,
        ) -> None:
            if on_progress:
                await on_progress(0.0)  # type: ignore[operator]
                await on_progress(1.0)  # type: ignore[operator]

        mock_cls.return_value.run = fake_run
        await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)], on_progress=cb)

    assert (GUID, 0.0) in calls
    assert (GUID, 1.0) in calls


async def test_preprocess_all_progress_intermediate_forwarded(
    preprocessor: AudioPreprocessor,
) -> None:
    """Intermediate progress values from Ffmpeg are forwarded with the guid."""
    calls: list[tuple[str, float]] = []

    async def cb(guid: str, pct: float) -> None:
        calls.append((guid, pct))

    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        captured_wrapper: list[object] = []

        async def fake_run(
            args: list[str],
            on_progress: object = None,
            duration: object = None,
        ) -> None:
            if on_progress:
                captured_wrapper.append(on_progress)

        mock_cls.return_value.run = fake_run
        await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)], on_progress=cb)

    # Call the captured wrapper with an intermediate value
    assert len(captured_wrapper) == 1
    wrapper = captured_wrapper[0]
    await wrapper(0.5)  # type: ignore[operator]
    assert (GUID, 0.5) in calls


async def test_preprocess_all_always_passes_callable_to_ffmpeg(
    preprocessor: AudioPreprocessor,
) -> None:
    """Ffmpeg.run() always receives a callable on_progress, even when preprocess_all gets None."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)], on_progress=None)

    call_kwargs = mock_run.call_args[1]
    assert callable(call_kwargs.get("on_progress"))


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_preprocess_all_ffmpeg_error_is_skipped(
    preprocessor: AudioPreprocessor,
) -> None:
    """FfmpegError causes the episode to be omitted; no exception propagates."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(side_effect=FfmpegError("boom"))
        result = await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)])

    assert result == []


async def test_preprocess_all_partial_success(
    preprocessor: AudioPreprocessor, cache_dir: Path
) -> None:
    """Only successful episodes are returned when one fails."""
    call_count = 0

    async def fake_run(
        args: list[str],
        on_progress: object = None,
        duration: object = None,
    ) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            msg = "second fails"
            raise FfmpegError(msg)

    pairs = [
        ("ep-ok", Path("/cache/ep-ok.mp3"), DURATION),
        ("ep-fail", Path("/cache/ep-fail.mp3"), DURATION),
    ]
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = fake_run
        result = await preprocessor.preprocess_all(pairs)

    assert len(result) == 1
    assert result[0][0] == "ep-ok"


async def test_preprocess_all_ffmpeg_error_does_not_call_final_progress(
    preprocessor: AudioPreprocessor,
) -> None:
    """When FfmpegError is raised, on_progress is not called for that episode."""
    cb = AsyncMock()

    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(side_effect=FfmpegError("boom"))
        await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)], on_progress=cb)

    cb.assert_not_awaited()


async def test_preprocess_all_logs_error_on_ffmpeg_failure(
    preprocessor: AudioPreprocessor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ERROR log contains the guid and base message; ffmpeg stderr is NOT in the ERROR log."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(
            side_effect=FfmpegError("ffmpeg exited with code 1", stderr="Conversion failed!")
        )
        with caplog.at_level(logging.ERROR, logger="components.audio_preprocessor"):
            await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)])

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(GUID in r.message for r in error_records)
    assert all("Conversion failed!" not in r.message for r in error_records)


async def test_preprocess_all_logs_stderr_at_debug_on_ffmpeg_failure(
    preprocessor: AudioPreprocessor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When FfmpegError carries stderr, the stderr text is logged at DEBUG."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(
            side_effect=FfmpegError("ffmpeg exited with code 1", stderr="Conversion failed!")
        )
        with caplog.at_level(logging.DEBUG, logger="components.audio_preprocessor"):
            await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)])

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("Conversion failed!" in r.message for r in debug_records)


# ---------------------------------------------------------------------------
# Logging on success
# ---------------------------------------------------------------------------


async def test_preprocess_all_logs_debug_on_success(
    preprocessor: AudioPreprocessor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A debug log is emitted for each successfully preprocessed episode."""
    with patch("components.audio_preprocessor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        with caplog.at_level(logging.DEBUG, logger="components.audio_preprocessor"):
            await preprocessor.preprocess_all([(GUID, INPUT_PATH, DURATION)])

    assert any(GUID in r.message for r in caplog.records)
