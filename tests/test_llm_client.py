from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.config_loader import InterpretationConfig, TranscriptionConfig


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


async def test_transcribe_fallback_cost_from_model_cost(tmp_path):
    from pipeline.llm_client import transcribe

    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"fake audio data")

    mock_result = MagicMock()
    mock_result.get = lambda key, default=None: {
        "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
        "duration": 120.0,
    }.get(key, default)
    mock_result._hidden_params = {"response_cost": None}

    config = TranscriptionConfig(provider="groq", model="whisper-large-v3", language="en")
    expected_cost = 120.0 * 3.083e-05

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.atranscription = AsyncMock(return_value=mock_result)
        mock_litellm.APIError = Exception
        mock_litellm.model_cost = {
            "groq/whisper-large-v3": {"input_cost_per_second": 3.083e-05}
        }
        _, cost = await transcribe(audio_file, config)

    assert cost == pytest.approx(expected_cost)


def test_validate_api_keys_raises_when_env_var_missing(monkeypatch):
    from pathlib import Path

    from config.config_loader import load_config
    from pipeline.exceptions import ConfigError
    from pipeline.llm_client import validate_api_keys

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        validate_api_keys(cfg)


def test_validate_api_keys_raises_when_key_invalid(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch

    from config.config_loader import load_config
    from pipeline.exceptions import ConfigError
    from pipeline.llm_client import validate_api_keys

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.setenv("OPENAI_API_KEY", "bad-key")

    with (
        patch("pipeline.llm_client.litellm.check_valid_key", return_value=False),
        pytest.raises(ConfigError, match="Invalid API key"),
    ):
        validate_api_keys(cfg)


def test_validate_api_keys_passes_with_valid_key(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch

    from config.config_loader import load_config
    from pipeline.llm_client import validate_api_keys

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-valid")

    with patch("pipeline.llm_client.litellm.check_valid_key", return_value=True):
        validate_api_keys(cfg)  # must not raise


def test_validate_api_keys_deduplicates_providers(monkeypatch):
    """When transcription and interpretation use the same provider, only one probe fires."""
    from pathlib import Path
    from unittest.mock import patch

    from config.config_loader import load_config
    from pipeline.llm_client import validate_api_keys

    # test_config.yaml uses openai for both transcription and interpretation
    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-valid")

    with patch("pipeline.llm_client.litellm.check_valid_key", return_value=True) as mock_check:
        validate_api_keys(cfg)

    assert mock_check.call_count == 1  # deduped: openai only probed once
