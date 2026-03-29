"""Tests for AudioEditor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from components.audio_editor import AudioEditor
from models.ad_detection import AdSegment
from utils.exceptions import FfmpegError

PUB_DATE = datetime(2026, 3, 28, tzinfo=UTC)
TITLE = "My Episode"
FEED_SLUG = "my-podcast"
GUID = "ep-001"


def _make_editor(tmp_path: Path, file_type: str = "mp3", bitrate: str = "128k") -> AudioEditor:
    return AudioEditor(output_dir=tmp_path / "output", file_type=file_type, bitrate=bitrate)


def _seg(start_ms: int, end_ms: int, confidence: float = 0.95) -> AdSegment:
    return AdSegment(guid=GUID, start_ms=start_ms, end_ms=end_ms,
                     confidence=confidence, sponsor="Acme", ad_topic="widgets")


@pytest.fixture
def editor(tmp_path: Path) -> AudioEditor:
    return _make_editor(tmp_path)


@pytest.fixture
def input_path(tmp_path: Path) -> Path:
    p = tmp_path / "ep.mp3"
    p.write_bytes(b"audio")
    return p


# ---------------------------------------------------------------------------
# No qualifying ads → return None
# ---------------------------------------------------------------------------

async def test_no_segments_returns_none(editor: AudioEditor, input_path: Path) -> None:
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        result = await editor.edit(GUID, input_path, [], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7)
    assert result is None
    mock_cls.assert_not_called()


async def test_below_min_duration_returns_none(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(0, 5000)  # 5s < 10s min
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        result = await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7)
    assert result is None
    mock_cls.assert_not_called()


async def test_below_min_confidence_returns_none(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(0, 30000, confidence=0.5)  # 0.5 < 0.7 min
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        result = await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7)
    assert result is None
    mock_cls.assert_not_called()


async def test_all_ads_returns_none(editor: AudioEditor, input_path: Path) -> None:
    """Single ad covering full audio → no keep segments → return None."""
    seg = _seg(0, 3600000)  # full hour
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        result = await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                                   total_duration_s=3600.0)
    assert result is None
    mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Qualifying ads → returns Path, calls ffmpeg
# ---------------------------------------------------------------------------

async def test_qualifying_segment_returns_path(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        result = await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                                   total_duration_s=3600.0)
    assert result is not None
    assert result.suffix == ".mp3"


async def test_qualifying_segment_creates_output_dir(tmp_path: Path, input_path: Path) -> None:
    editor = _make_editor(tmp_path)
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        result = await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                                   total_duration_s=3600.0)
    assert result is not None
    assert result.parent.is_dir()  # noqa: ASYNC240


async def test_qualifying_segment_uses_filter_complex(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    assert "-filter_complex" in args


async def test_filter_complex_contains_atrim(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    fc_idx = args.index("-filter_complex")
    assert "atrim" in args[fc_idx + 1]


async def test_filter_complex_contains_asetpts(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    fc_idx = args.index("-filter_complex")
    assert "asetpts=PTS-STARTPTS" in args[fc_idx + 1]


async def test_atrim_times_in_seconds(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(60000, 90000)  # 60s to 90s
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    fc_idx = args.index("-filter_complex")
    fc_str = args[fc_idx + 1]
    assert "60.0" in fc_str
    assert "90.0" in fc_str


async def test_two_ads_produce_three_keep_segments(editor: AudioEditor, input_path: Path) -> None:
    segs = [_seg(60000, 90000), _seg(300000, 330000)]
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, segs, FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    fc_idx = args.index("-filter_complex")
    fc_str = args[fc_idx + 1]
    assert "concat=n=3" in fc_str


async def test_overlapping_segments_merged(editor: AudioEditor, input_path: Path) -> None:
    segs = [_seg(60000, 120000), _seg(90000, 150000)]  # overlapping
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, segs, FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    fc_idx = args.index("-filter_complex")
    fc_str = args[fc_idx + 1]
    # Merged into one ad → two keep segments
    assert "concat=n=2" in fc_str


async def test_idempotent_skip_returns_path(tmp_path: Path, input_path: Path) -> None:
    editor = _make_editor(tmp_path)
    seg = _seg(60000, 90000)
    # Pre-create output file
    from components.feed_publisher import FeedPublisher
    filename = FeedPublisher.episode_filename(PUB_DATE, TITLE, "mp3")
    dest = tmp_path / "output" / FEED_SLUG / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"already done")

    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        result = await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7)
    assert result == dest
    mock_cls.assert_not_called()


async def test_uses_mp3_codec(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    assert "libmp3lame" in args


async def test_uses_m4a_codec(tmp_path: Path, input_path: Path) -> None:
    editor = _make_editor(tmp_path, file_type="m4a")
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_run = AsyncMock(return_value=None)
        mock_cls.return_value.run = mock_run
        await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                          total_duration_s=3600.0)
    args = mock_run.call_args[0][0]
    assert "aac" in args


async def test_ffmpeg_error_propagates(editor: AudioEditor, input_path: Path) -> None:
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(
            side_effect=FfmpegError("ffmpeg failed", stderr="bad input")
        )
        with pytest.raises(FfmpegError):
            await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                              total_duration_s=3600.0)


async def test_filename_uses_feed_publisher_format(editor: AudioEditor, input_path: Path) -> None:
    from components.feed_publisher import FeedPublisher
    seg = _seg(60000, 90000)
    with patch("components.audio_editor.Ffmpeg") as mock_cls:
        mock_cls.return_value.run = AsyncMock(return_value=None)
        result = await editor.edit(GUID, input_path, [seg], FEED_SLUG, PUB_DATE, TITLE, 10000, 0.7,
                                   total_duration_s=3600.0)
    expected_name = FeedPublisher.episode_filename(PUB_DATE, TITLE, "mp3")
    assert result is not None
    assert result.name == expected_name


async def test_on_progress_logs_at_complete(editor: AudioEditor) -> None:
    cb = AudioEditor._on_progress("ep-001")
    # 1.0 triggers the debug log branch
    await cb(1.0)
    # Other values are no-ops — just verify they don't raise
    await cb(0.5)
