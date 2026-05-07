"""Tests for EpisodeTranscriptor."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.episode_transcriptor import EpisodeTranscriptor, _noop
from models.transcription import Transcription, TranscriptionCost, TranscriptionSegment
from utils.exceptions import TranscriptionError

if TYPE_CHECKING:
    from pathlib import Path


def _make_response(
    text: str = "Hello world",
    segments: list[dict] | None = None,
    response_cost: float | None = 0.001,
) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.segments = segments if segments is not None else [
        {"start": 0.0, "end": 1.5, "text": "Hello"},
        {"start": 1.5, "end": 3.0, "text": "world"},
    ]
    resp._hidden_params = {"response_cost": response_cost}
    return resp


def _make_transcriptor(
    provider: str = "groq", model: str = "whisper-large-v3-turbo", api_key: str = "sk-test"
) -> EpisodeTranscriptor:
    return EpisodeTranscriptor(provider=provider, model=model, api_key=api_key)


@pytest.fixture
def transcriptor() -> EpisodeTranscriptor:
    return _make_transcriptor()


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    p = tmp_path / "ep1.mono.m4a"
    p.write_bytes(b"fake audio data")
    return p


async def test_transcribe_returns_result_tuple(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response()
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        guid, transcription, segments, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert guid == "ep-1"
    assert isinstance(transcription, Transcription)
    assert isinstance(cost, TranscriptionCost)
    assert all(isinstance(s, TranscriptionSegment) for s in segments)


async def test_transcribe_correct_transcription_values(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(text="This is the full text")
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, transcription, _, _ = await transcriptor.transcribe("ep-1", audio_file)

    assert transcription.guid == "ep-1"
    assert transcription.text == "This is the full text"


async def test_transcribe_converts_seconds_to_ms(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(segments=[{"start": 1.5, "end": 3.25, "text": "Hello"}])
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, segments, _ = await transcriptor.transcribe("ep-1", audio_file)

    assert len(segments) == 1
    assert segments[0].start_ms == 1500
    assert segments[0].end_ms == 3250


async def test_transcribe_segments_have_correct_guid(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response()
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, segments, _ = await transcriptor.transcribe("ep-42", audio_file)

    assert all(s.guid == "ep-42" for s in segments)


async def test_transcribe_splits_model_id_for_provider(audio_file: Path) -> None:
    t = _make_transcriptor(provider="groq", model="whisper-large-v3-turbo")
    mock_resp = _make_response()
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, _, cost = await t.transcribe("ep-1", audio_file)

    assert cost.provider == "groq"
    assert cost.model == "whisper-large-v3-turbo"


async def test_transcribe_openai_model_no_slash(audio_file: Path) -> None:
    t = _make_transcriptor(provider="openai", model="whisper-1")
    mock_resp = _make_response()
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, _, cost = await t.transcribe("ep-1", audio_file)

    assert cost.provider == "openai"
    assert cost.model == "whisper-1"


async def test_transcribe_cost_from_hidden_params(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0042)
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == 0.0042


async def test_transcribe_cost_defaults_to_zero_when_none(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=None)
    mock_resp.duration = None
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", {}):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == 0.0


async def test_compute_cost_uses_hidden_params_when_positive(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0042)
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == pytest.approx(0.0042)


async def test_compute_cost_falls_back_to_duration_when_zero(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0)
    mock_resp.duration = 60.0
    model_cost_map = {"groq/whisper-large-v3-turbo": {"input_cost_per_second": 0.0001}}
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", model_cost_map):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == pytest.approx(0.006)


async def test_compute_cost_falls_back_when_response_cost_none(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=None)
    mock_resp.duration = 60.0
    model_cost_map = {"groq/whisper-large-v3-turbo": {"input_cost_per_second": 0.0001}}
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", model_cost_map):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == pytest.approx(0.006)


async def test_compute_cost_returns_zero_when_model_not_in_cost_map(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0)
    mock_resp.duration = 60.0
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", {}):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == 0.0


async def test_compute_cost_returns_zero_when_no_duration(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0)
    mock_resp.duration = None
    model_cost_map = {"groq/whisper-large-v3-turbo": {"input_cost_per_second": 0.0001}}
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", model_cost_map):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == 0.0


async def test_compute_cost_returns_zero_when_no_cost_per_second(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0)
    mock_resp.duration = 60.0
    model_cost_map = {"groq/whisper-large-v3-turbo": {"input_cost_per_token": 0.0001}}
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", model_cost_map):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == 0.0


async def test_compute_cost_returns_zero_when_response_cost_not_numeric(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0)
    mock_resp._hidden_params = {"response_cost": "not-a-number"}
    mock_resp.duration = None
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", {}):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == 0.0


async def test_compute_cost_returns_zero_when_duration_not_numeric(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(response_cost=0.0)
    mock_resp.duration = "bad"
    model_cost_map = {"groq/whisper-large-v3-turbo": {"input_cost_per_second": "bad"}}
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ), patch("components.episode_transcriptor.litellm.model_cost", model_cost_map):
        _, _, _, cost = await transcriptor.transcribe("ep-1", audio_file)

    assert cost.cost == 0.0


async def test_transcribe_empty_segments_none(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(segments=None)
    mock_resp.segments = None
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, segments, _ = await transcriptor.transcribe("ep-1", audio_file)

    assert segments == []


async def test_transcribe_empty_segments_list(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_resp = _make_response(segments=[])
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=mock_resp),
    ):
        _, _, segments, _ = await transcriptor.transcribe("ep-1", audio_file)

    assert segments == []


async def test_transcribe_raises_on_litellm_exception(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(side_effect=RuntimeError("API down")),
    ):
        with pytest.raises(TranscriptionError):
            await transcriptor.transcribe("ep-1", audio_file)


async def test_transcribe_logs_error_on_failure(
    transcriptor: EpisodeTranscriptor, audio_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with caplog.at_level("ERROR"):
            with pytest.raises(TranscriptionError):
                await transcriptor.transcribe("ep-fail", audio_file)
    assert "ep-fail" in caplog.text


async def test_transcribe_logs_on_success(
    transcriptor: EpisodeTranscriptor, audio_file: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(return_value=_make_response()),
    ):
        with caplog.at_level("DEBUG"):
            await transcriptor.transcribe("ep-ok", audio_file)
    assert "ep-ok" in caplog.text


async def test_transcribe_passes_api_key_to_litellm(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_api = AsyncMock(return_value=_make_response())
    with patch("components.episode_transcriptor.litellm.atranscription", new=mock_api):
        await transcriptor.transcribe("ep-1", audio_file)
    _, kwargs = mock_api.call_args
    assert kwargs.get("api_key") == "sk-test"


async def test_transcribe_passes_verbose_json_format(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_api = AsyncMock(return_value=_make_response())
    with patch("components.episode_transcriptor.litellm.atranscription", new=mock_api):
        await transcriptor.transcribe("ep-1", audio_file)
    _, kwargs = mock_api.call_args
    assert kwargs.get("response_format") == "verbose_json"


async def test_transcribe_passes_model_id_to_litellm(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    mock_api = AsyncMock(return_value=_make_response())
    with patch("components.episode_transcriptor.litellm.atranscription", new=mock_api):
        await transcriptor.transcribe("ep-1", audio_file)
    _, kwargs = mock_api.call_args
    assert kwargs.get("model") == "groq/whisper-large-v3-turbo"


async def test_transcribe_openai_passes_bare_model_to_litellm(audio_file: Path) -> None:
    t = _make_transcriptor(provider="openai", model="whisper-1")
    mock_api = AsyncMock(return_value=_make_response())
    with patch("components.episode_transcriptor.litellm.atranscription", new=mock_api):
        await t.transcribe("ep-1", audio_file)
    _, kwargs = mock_api.call_args
    assert kwargs.get("model") == "whisper-1"


async def test_transcribe_opens_file_in_binary_mode(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    """The file handle passed to litellm must be opened in binary mode."""
    captured_file: list[object] = []

    async def _capture(**kwargs: object) -> MagicMock:
        captured_file.append(kwargs.get("file"))
        return _make_response()

    with patch("components.episode_transcriptor.litellm.atranscription", new=_capture):
        await transcriptor.transcribe("ep-1", audio_file)

    assert len(captured_file) == 1
    assert isinstance(captured_file[0], io.RawIOBase | io.BufferedIOBase)


async def test_noop_progress_callback_does_not_raise() -> None:
    await _noop(0.5)


async def test_transcribe_error_includes_file_size_mb(
    transcriptor: EpisodeTranscriptor, audio_file: Path
) -> None:
    with patch(
        "components.episode_transcriptor.litellm.atranscription",
        new=AsyncMock(side_effect=RuntimeError("API down")),
    ):
        with pytest.raises(TranscriptionError) as exc_info:
            await transcriptor.transcribe("ep-1", audio_file)
    assert "MB" in exc_info.value.message


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------

_SMALL_LIMIT = 50       # bytes — triggers chunking for any 100-byte test file
_SMALL_CHUNK_SECS = 60  # seconds per chunk
_SMALL_BITRATE = 1.0    # bytes/sec — makes 100-byte file "last" 100 seconds


async def _fake_ffmpeg_run(
    args: list[str], on_progress: object = None, duration: float = 0.0
) -> None:
    """Write a stub chunk file at the path given as the last ffmpeg arg."""
    from pathlib import Path as _Path
    _Path(args[-1]).write_bytes(b"chunk data")


@pytest.fixture
def large_audio(tmp_path: Path) -> Path:
    """100-byte audio file — with patched constants, represents a 100-second episode."""
    p = tmp_path / "large.mono.m4a"
    p.write_bytes(b"x" * 100)
    return p


# ---------------------------------------------------------------------------
# Chunking tests
# ---------------------------------------------------------------------------


async def test_transcribe_splits_into_multiple_chunks_when_file_exceeds_limit(
    transcriptor: EpisodeTranscriptor, large_audio: Path
) -> None:
    mock_atrans = AsyncMock(return_value=_make_response(segments=[]))
    with (
        patch("components.episode_transcriptor._GROQ_MAX_BYTES", _SMALL_LIMIT),
        patch("components.episode_transcriptor._CHUNK_DURATION_SECS", _SMALL_CHUNK_SECS),
        patch("components.episode_transcriptor._AUDIO_BITRATE_BYTES_PER_SEC", _SMALL_BITRATE),
        patch("components.episode_transcriptor.Ffmpeg") as mock_ffmpeg_cls,
        patch("components.episode_transcriptor.litellm.atranscription", new=mock_atrans),
    ):
        mock_ffmpeg_cls.return_value.run = AsyncMock(side_effect=_fake_ffmpeg_run)
        await transcriptor.transcribe("ep-1", large_audio)
    # 100 bytes / 1 byte/sec = 100 sec; ceil(100 / 60) = 2 chunks
    assert mock_atrans.call_count == 2


async def test_transcribe_chunked_concatenates_text_with_space(
    transcriptor: EpisodeTranscriptor, large_audio: Path
) -> None:
    responses = iter([
        _make_response(text="First part", segments=[]),
        _make_response(text="Second part", segments=[]),
    ])

    async def _side(**_: object) -> MagicMock:
        return next(responses)

    with (
        patch("components.episode_transcriptor._GROQ_MAX_BYTES", _SMALL_LIMIT),
        patch("components.episode_transcriptor._CHUNK_DURATION_SECS", _SMALL_CHUNK_SECS),
        patch("components.episode_transcriptor._AUDIO_BITRATE_BYTES_PER_SEC", _SMALL_BITRATE),
        patch("components.episode_transcriptor.Ffmpeg") as mock_ffmpeg_cls,
        patch("components.episode_transcriptor.litellm.atranscription", side_effect=_side),
    ):
        mock_ffmpeg_cls.return_value.run = AsyncMock(side_effect=_fake_ffmpeg_run)
        _, transcription, _, _ = await transcriptor.transcribe("ep-1", large_audio)

    assert transcription.text == "First part Second part"


async def test_transcribe_chunked_offsets_segment_timestamps_by_chunk_start(
    transcriptor: EpisodeTranscriptor, large_audio: Path
) -> None:
    responses = iter([
        _make_response(text="A", segments=[{"start": 1.0, "end": 2.0, "text": "Hello"}]),
        _make_response(text="B", segments=[{"start": 0.5, "end": 1.5, "text": "World"}]),
    ])

    async def _side(**_: object) -> MagicMock:
        return next(responses)

    with (
        patch("components.episode_transcriptor._GROQ_MAX_BYTES", _SMALL_LIMIT),
        patch("components.episode_transcriptor._CHUNK_DURATION_SECS", _SMALL_CHUNK_SECS),
        patch("components.episode_transcriptor._AUDIO_BITRATE_BYTES_PER_SEC", _SMALL_BITRATE),
        patch("components.episode_transcriptor.Ffmpeg") as mock_ffmpeg_cls,
        patch("components.episode_transcriptor.litellm.atranscription", side_effect=_side),
    ):
        mock_ffmpeg_cls.return_value.run = AsyncMock(side_effect=_fake_ffmpeg_run)
        _, _, segments, _ = await transcriptor.transcribe("ep-1", large_audio)

    assert len(segments) == 2
    assert segments[0].start_ms == 1000   # chunk 0 has no offset
    assert segments[0].end_ms == 2000
    assert segments[1].start_ms == 60500  # chunk 1: 500 + 60 * 1000 ms
    assert segments[1].end_ms == 61500


async def test_transcribe_chunked_sums_costs_across_chunks(
    transcriptor: EpisodeTranscriptor, large_audio: Path
) -> None:
    responses = iter([
        _make_response(text="A", segments=[], response_cost=0.01),
        _make_response(text="B", segments=[], response_cost=0.02),
    ])

    async def _side(**_: object) -> MagicMock:
        return next(responses)

    with (
        patch("components.episode_transcriptor._GROQ_MAX_BYTES", _SMALL_LIMIT),
        patch("components.episode_transcriptor._CHUNK_DURATION_SECS", _SMALL_CHUNK_SECS),
        patch("components.episode_transcriptor._AUDIO_BITRATE_BYTES_PER_SEC", _SMALL_BITRATE),
        patch("components.episode_transcriptor.Ffmpeg") as mock_ffmpeg_cls,
        patch("components.episode_transcriptor.litellm.atranscription", side_effect=_side),
    ):
        mock_ffmpeg_cls.return_value.run = AsyncMock(side_effect=_fake_ffmpeg_run)
        _, _, _, cost = await transcriptor.transcribe("ep-1", large_audio)

    assert cost.cost == pytest.approx(0.03)


async def test_transcribe_chunked_deletes_chunk_files_after_success(
    transcriptor: EpisodeTranscriptor, large_audio: Path
) -> None:
    with (
        patch("components.episode_transcriptor._GROQ_MAX_BYTES", _SMALL_LIMIT),
        patch("components.episode_transcriptor._CHUNK_DURATION_SECS", _SMALL_CHUNK_SECS),
        patch("components.episode_transcriptor._AUDIO_BITRATE_BYTES_PER_SEC", _SMALL_BITRATE),
        patch("components.episode_transcriptor.Ffmpeg") as mock_ffmpeg_cls,
        patch(
            "components.episode_transcriptor.litellm.atranscription",
            new=AsyncMock(return_value=_make_response(segments=[])),
        ),
    ):
        mock_ffmpeg_cls.return_value.run = AsyncMock(side_effect=_fake_ffmpeg_run)
        await transcriptor.transcribe("ep-1", large_audio)

    assert not (large_audio.parent / "ep-1.chunk0000.mono.m4a").exists()
    assert not (large_audio.parent / "ep-1.chunk0001.mono.m4a").exists()


async def test_transcribe_chunked_deletes_chunk_files_on_transcription_error(
    transcriptor: EpisodeTranscriptor, large_audio: Path
) -> None:
    call_count = 0

    async def _fail_on_second(**_: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("second chunk failed")
        return _make_response(segments=[])

    with (
        patch("components.episode_transcriptor._GROQ_MAX_BYTES", _SMALL_LIMIT),
        patch("components.episode_transcriptor._CHUNK_DURATION_SECS", _SMALL_CHUNK_SECS),
        patch("components.episode_transcriptor._AUDIO_BITRATE_BYTES_PER_SEC", _SMALL_BITRATE),
        patch("components.episode_transcriptor.Ffmpeg") as mock_ffmpeg_cls,
        patch("components.episode_transcriptor.litellm.atranscription", side_effect=_fail_on_second),
    ):
        mock_ffmpeg_cls.return_value.run = AsyncMock(side_effect=_fake_ffmpeg_run)
        with pytest.raises(TranscriptionError):
            await transcriptor.transcribe("ep-1", large_audio)

    assert not (large_audio.parent / "ep-1.chunk0000.mono.m4a").exists()
    assert not (large_audio.parent / "ep-1.chunk0001.mono.m4a").exists()
