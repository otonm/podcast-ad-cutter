"""EpisodeTranscriptor — sends mono audio files to an STT model via litellm."""

from __future__ import annotations

import logging
import math
from pathlib import Path  # noqa: TC003

import litellm

from models.transcription import Transcription, TranscriptionCost, TranscriptionSegment
from utils.exceptions import TranscriptionError
from utils.ffmpeg import Ffmpeg

logger = logging.getLogger(__name__)

type TranscriptionResult = tuple[str, Transcription, list[TranscriptionSegment], TranscriptionCost]

_GROQ_MAX_BYTES: int = 25 * 1024 * 1024           # 25 MB Groq per-request limit
_AUDIO_BITRATE_BYTES_PER_SEC: float = 32_000 / 8  # 32 kbps AAC = 4 000 B/s
# Largest chunk that fits under the limit with 10 % headroom (~98 min).
# Minimises join points: fewer chunks = fewer transcript boundary errors.
_CHUNK_DURATION_SECS: int = int(_GROQ_MAX_BYTES / _AUDIO_BITRATE_BYTES_PER_SEC * 0.9)


async def _noop(_pct: float) -> None:
    pass


class EpisodeTranscriptor:
    """Sends mono audio files to an STT model via litellm and returns structured results.

    For each ``(guid, path)`` pair, calls ``litellm.atranscription`` with
    ``response_format="verbose_json"`` to obtain the full transcript and
    timestamped segments.  Cost is read from the response hidden params.

    Constructs the litellm model identifier from the provider and model name:
    OpenAI uses the bare model name; all other providers use ``"provider/model"``.

    When the audio file exceeds the provider's per-request size limit the file
    is split into chunks via ffmpeg, each chunk is transcribed independently,
    and the results are merged (text concatenated, segment timestamps offset by
    chunk start time, costs summed).

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

    async def _call_litellm(self, path: Path) -> object:
        with path.open("rb") as f:
            return await litellm.atranscription(
                model=self._model_id,
                file=f,
                response_format="verbose_json",
                api_key=self._api_key,
            )

    async def _transcribe_chunked(
        self, guid: str, path: Path, file_size_mb: float
    ) -> TranscriptionResult:
        estimated_duration = path.stat().st_size / _AUDIO_BITRATE_BYTES_PER_SEC  # noqa: ASYNC240
        num_chunks = math.ceil(estimated_duration / _CHUNK_DURATION_SECS)
        logger.info(
            f"'{guid}' ({file_size_mb:.1f} MB) exceeds limit — "
            f"splitting into {num_chunks} chunk(s)"
        )

        chunk_paths = [
            (path.parent / f"{guid}.chunk{i:04d}.mono.m4a", i * _CHUNK_DURATION_SECS)
            for i in range(num_chunks)
        ]

        try:
            for chunk_path, start in chunk_paths:
                await Ffmpeg().run(
                    [
                        "-ss", str(start),
                        "-i", str(path),
                        "-t", str(_CHUNK_DURATION_SECS),
                        "-c", "copy",
                        "-y",
                        str(chunk_path),
                    ],
                    on_progress=_noop,
                    duration=float(_CHUNK_DURATION_SECS),
                )

            all_text: list[str] = []
            all_segments: list[TranscriptionSegment] = []
            total_cost = 0.0

            for chunk_path, start_sec in chunk_paths:
                resp = await self._call_litellm(chunk_path)
                offset_ms = int(start_sec * 1000)
                all_text.append(resp.text)
                all_segments.extend(
                    TranscriptionSegment(
                        guid=guid,
                        start_ms=int(seg["start"] * 1000) + offset_ms,
                        end_ms=int(seg["end"] * 1000) + offset_ms,
                        text=seg["text"],
                    )
                    for seg in (resp.segments or [])
                )
                total_cost += self._compute_cost(resp)

        finally:
            for chunk_path, _ in chunk_paths:
                chunk_path.unlink(missing_ok=True)

        logger.info(f"Transcribed '{guid}' (chunked): {len(all_segments)} segment(s)")
        return (
            guid,
            Transcription(guid=guid, text=" ".join(all_text)),
            all_segments,
            TranscriptionCost(provider=self._provider, model=self._model, cost=total_cost),
        )

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
        file_size = path.stat().st_size  # noqa: ASYNC240
        file_size_mb = file_size / (1024 * 1024)
        logger.debug(f"Transcribing '{guid}' ({file_size_mb:.1f} MB) with model {self._model_id}")

        try:
            if file_size > _GROQ_MAX_BYTES:
                return await self._transcribe_chunked(guid, path, file_size_mb)
            response = await self._call_litellm(path)
        except Exception as exc:
            msg = f"litellm.atranscription failed for '{guid}' ({file_size_mb:.1f} MB): {exc}"
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
