"""EpisodeTranscriptor — sends mono audio files to an STT model via litellm."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003

import litellm

from models.transcription import Transcription, TranscriptionCost, TranscriptionSegment
from utils.exceptions import TranscriptionError

logger = logging.getLogger(__name__)

type TranscriptionResult = tuple[str, Transcription, list[TranscriptionSegment], TranscriptionCost]


class EpisodeTranscriptor:
    """Sends mono audio files to an STT model via litellm and returns structured results.

    For each ``(guid, path)`` pair, calls ``litellm.atranscription`` with
    ``response_format="verbose_json"`` to obtain the full transcript and
    timestamped segments.  Cost is read from the response hidden params.

    Constructs the litellm model identifier from the provider and model name:
    OpenAI uses the bare model name; all other providers use ``"provider/model"``.

    Args:
        provider: Provider name, e.g. ``"groq"`` or ``"openai"``.
        model: Model name, e.g. ``"whisper-large-v3-turbo"``.
        api_key: API key for the provider.

    """

    def __init__(self, provider: str, model: str, api_key: str) -> None:
        self._provider = provider
        self._model = model
        self._model_id = model if provider == "openai" else f"{provider}/{model}"
        self._api_key = api_key

    def _compute_cost(self, response: object) -> float:
        """Extract cost from response, falling back to duration-based calculation.

        LiteLLM maps groq/whisper to the openai provider for cost lookup, which
        fails because openai/whisper-large-v3 is not in its model_cost map. The
        success handler then finds the groq model but reads input_cost_per_token
        (0) instead of input_cost_per_second, storing 0.0. We treat 0.0 as a
        failed lookup and compute cost manually.
        """
        hidden = getattr(response, "_hidden_params", {}) or {}
        raw = hidden.get("response_cost")
        if raw is not None:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                pass
            else:
                if val > 0.0:
                    logger.debug(f"Transcription cost is {val}")
                    return val

        canonical = f"{self._provider}/{self._model}"
        model_info = litellm.model_cost.get(canonical)
        if model_info is None:
            logger.warning(f"Model {canonical} not in litellm.model_cost, cost will be $0.00")
            return 0.0

        rate = model_info.get("input_cost_per_second")
        if rate is None:
            logger.warning(f"No input_cost_per_second for {canonical}, cost will be $0.00")
            return 0.0

        duration = getattr(response, "duration", None)
        if duration is None:
            logger.warning(f"No duration in response for {canonical}, cost will be $0.00")
            return 0.0

        try:
            cost = float(duration) * float(rate)
        except (TypeError, ValueError):
            logger.warning(f"Could not compute cost for {canonical}, cost will be $0.00")
            return 0.0
        else:
            logger.debug(f"Transcription cost for model {canonical} is {cost}")
            return cost

    async def transcribe(self, guid: str, path: Path) -> TranscriptionResult:
        """Transcribe one mono audio file.

        Args:
            guid: Episode GUID — used in error messages and result objects.
            path: Path to the mono audio file.

        Returns:
            ``(guid, Transcription, list[TranscriptionSegment], TranscriptionCost)``.

        Raises:
            TranscriptionError: On any litellm or API failure.

        """
        logger.debug(f"Transcribing '{guid}' with model {self._model_id}")
        try:
            with path.open("rb") as f:
                response = await litellm.atranscription(
                    model=self._model_id,
                    file=f,
                    response_format="verbose_json",
                    api_key=self._api_key,
                )
        except Exception as exc:
            msg = f"litellm.atranscription failed for '{guid}': {exc}"
            logger.error(f"Skipping transcription for '{guid}': {msg}")
            raise TranscriptionError(msg) from exc

        cost_value = self._compute_cost(response)

        transcription = Transcription(guid=guid, text=response.text)

        segments = [
            TranscriptionSegment(
                guid=guid,
                start_ms=int(seg["start"] * 1000),
                end_ms=int(seg["end"] * 1000),
                text=seg["text"],
            )
            for seg in (response.segments or [])
        ]

        cost_record = TranscriptionCost(
            provider=self._provider,
            model=self._model,
            cost=cost_value,
        )

        logger.info(f"Transcribed '{guid}': {len(segments)} segment(s)")
        return (guid, transcription, segments, cost_record)
