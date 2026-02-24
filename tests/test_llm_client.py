from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config_loader import InterpretationConfig, TranscriptionConfig


@pytest.fixture
def llm_config():
    return InterpretationConfig(
        provider="openai",
        model="gpt-4o",
        temperature=0,
        max_tokens=2048,
    )


@pytest.fixture
def transcription_config():
    return TranscriptionConfig(provider="openai", model="whisper-1", language="en")


async def test_complete_returns_text(llm_config):
    from pipeline.llm_client import complete

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hello world"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_response._hidden_params = {"response_cost": 0.001}

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.acompletion = AsyncMock(return_value=mock_response)
        mock_litellm.APIError = Exception
        content, cost = await complete(
            [{"role": "user", "content": "Hi"}],
            llm_config,
        )
    assert content == "Hello world"
    assert cost == 0.001


async def test_complete_raises_llm_error_on_api_error(llm_config):
    from pipeline.exceptions import LLMError
    from pipeline.llm_client import complete

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.APIError = type("APIError", (Exception,), {})
        mock_litellm.acompletion = AsyncMock(side_effect=mock_litellm.APIError("fail"))
        with pytest.raises(LLMError):
            await complete(
                [{"role": "user", "content": "Hi"}],
                llm_config,
            )


async def test_transcribe_returns_dict(transcription_config, tmp_path):
    from pipeline.llm_client import transcribe

    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"fake audio data")

    mock_result = MagicMock()
    mock_result.get = {"words": [{"word": "hello", "start": 0.0, "end": 0.5}]}.get
    mock_result._hidden_params = {"response_cost": 0.002}

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.atranscription = AsyncMock(return_value=mock_result)
        mock_litellm.APIError = Exception
        result, cost = await transcribe(audio_file, transcription_config)
    assert result is mock_result
    assert cost == 0.002
