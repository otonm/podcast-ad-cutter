"""Tests for EpisodeTranscriptor."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from components.episode_transcriptor import EpisodeTranscriptor
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


async def test_transcribe_logs_debug_on_success(
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
