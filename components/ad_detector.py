"""AdDetector — identifies advertisement segments in podcast transcripts via an LLM."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import litellm

from models.ad_detection import AdDetectionCost, AdSegmentDetection
from utils.exceptions import AdDetectionError

if TYPE_CHECKING:
    from models.topic import TopicExtraction
    from models.transcription import TranscriptionSegment

logger = logging.getLogger(__name__)

_COMPLETION_RESERVE_TOKENS = 512

_SYSTEM_PROMPT_TEMPLATE = (
    "You are an advertisement detection assistant for podcast audio.\n"
    "Your task is to identify which transcript segments are advertisements, "
    "sponsorship reads, or promotional content — not organic episode content.\n\n"
    "Episode context:\n"
    "- Show: {show}\n"
    "- Hosts: {hosts}\n"
    "- Topic: {topic}\n\n"
    "You will receive transcript segments in the format: [index][start_ms][end_ms] text\n\n"
    "Reply with a JSON array of objects, each with exactly these keys:\n"
    '- "index": integer segment index\n'
    '- "confidence": float 0.0\u20131.0\n'
    '- "sponsor": advertiser name (empty string if unknown)\n'
    '- "ad_topic": brief description of the ad (empty string if unknown)\n\n'
    "If no segments are ads, reply with []. Return only the JSON array, no other text."
)

_RETRY_PROMPT = (
    "Your previous response was not valid JSON or was not a JSON array with required keys. "
    "Reply with only a JSON array. Each element must have exactly these four keys: "
    '"index" (integer), "confidence" (float 0.0\u20131.0), "sponsor" (string), "ad_topic" (string). '
    "If there are no ads, reply with []. No other text."
)

type AdDetectionResult = tuple[str, list[AdSegmentDetection], AdDetectionCost]


class AdDetector:
    """Calls a chat LLM via litellm to identify advertisement segments in a transcript.

    Uses topic context (show, hosts, topic) to distinguish ads from content.
    Retries up to ``max_retries`` times on malformed JSON.  API-level failures
    are not retried.

    Args:
        provider: Provider name, e.g. ``"openai"`` or ``"groq"``.
        model: Model name, e.g. ``"gpt-4o-mini"``.
        api_key: API key for the provider.
        max_retries: Maximum number of LLM call attempts (default 3).

    """

    def __init__(self, provider: str, model: str, api_key: str, max_retries: int = 3) -> None:
        self._provider = provider
        self._model = model
        self._model_id = model if provider == "openai" else f"{provider}/{model}"
        self._api_key = api_key
        self._max_retries = max_retries
        self._max_input_tokens = self._resolve_context_window()

    def _resolve_context_window(self) -> int:
        """Return the model's max input token count, falling back to 8192."""
        try:
            info = litellm.get_model_info(self._model_id)
            limit = info.get("max_input_tokens") or info.get("max_tokens")
            if limit:
                logger.debug(f"Model {self._model_id} context window is {limit} tokens")
                return int(limit)
        except Exception as exc:  # noqa: BLE001 — litellm raises various errors for unknown models
            logger.warning(
                f"Could not retrieve model info for {self._model_id} ({exc}), "
                f"defaulting context window to 8192 tokens"
            )
        return 8192

    def _build_system_prompt(self, topic_extraction: TopicExtraction | None) -> str:
        show = topic_extraction.show if topic_extraction else "unknown"
        hosts = topic_extraction.hosts if topic_extraction else "unknown"
        topic = topic_extraction.topic if topic_extraction else "unknown"
        return _SYSTEM_PROMPT_TEMPLATE.format(show=show, hosts=hosts, topic=topic)

    def _format_segments(self, segments: list[TranscriptionSegment]) -> str:
        return "\n".join(
            f"[{i}][{s.start_ms}][{s.end_ms}] {s.text}"
            for i, s in enumerate(segments)
        )

    def _build_messages(
        self,
        system_prompt: str,
        formatted_segments: str,
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Segments:\n\n{formatted_segments}"},
        ]

    def _truncate_segments(self, formatted_segments: str, system_prompt: str) -> str:
        """Trim the formatted segments so the full prompt fits within the context window."""
        budget = self._max_input_tokens - _COMPLETION_RESERVE_TOKENS
        messages = self._build_messages(system_prompt, formatted_segments)
        token_count = litellm.token_counter(model=self._model_id, messages=messages)
        if token_count <= budget:
            return formatted_segments
        ratio = budget / token_count
        trimmed = formatted_segments[: int(len(formatted_segments) * ratio)]
        logger.warning(
            f"Segment list truncated from ~{token_count} to ~{budget} tokens "
            f"for model {self._model_id}"
        )
        return trimmed

    def _compute_cost(self, response: object) -> float:
        """Extract cost from response hidden params, falling back to litellm.completion_cost."""
        hidden = getattr(response, "_hidden_params", {}) or {}
        raw = hidden.get("response_cost")
        if raw is not None:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                pass
            else:
                if val > 0.0:
                    logger.debug(f"Ad detection cost is {val}")
                    return val
        try:
            cost = litellm.completion_cost(completion_response=response)
            logger.debug(f"Ad detection cost (completion_cost fallback) is {cost}")
            return float(cost)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"Could not compute cost for {self._model_id} ({exc}), cost will be $0.00"
            )
            return 0.0

    def _parse_response(self, content: str, guid: str) -> list[AdSegmentDetection]:
        data = json.loads(content)
        if not isinstance(data, list):
            msg = f"Expected JSON array, got {type(data).__name__} for '{guid}'"
            raise TypeError(msg)
        return [
            AdSegmentDetection(
                index=item["index"],
                confidence=item["confidence"],
                sponsor=item["sponsor"],
                ad_topic=item["ad_topic"],
            )
            for item in data
        ]

    async def detect(
        self,
        guid: str,
        segments: list[TranscriptionSegment],
        topic_extraction: TopicExtraction | None,
    ) -> AdDetectionResult:
        """Detect advertisement segments in one episode's transcript.

        Args:
            guid: Episode GUID — used in error messages and result objects.
            segments: All transcription segments for this episode.
            topic_extraction: Episode context (show, hosts, topic) to help
                the LLM distinguish ads from content.  Pass ``None`` if not
                yet available.

        Returns:
            ``(guid, list[AdSegmentDetection], AdDetectionCost)``.

        Raises:
            AdDetectionError: On any litellm/API failure or malformed JSON
                after all retries are exhausted.

        """
        system_prompt = self._build_system_prompt(topic_extraction)
        formatted = self._format_segments(segments)
        formatted = self._truncate_segments(formatted, system_prompt)
        messages = self._build_messages(system_prompt, formatted)

        try:
            response = await litellm.acompletion(
                model=self._model_id,
                messages=messages,
                response_format={"type": "json_object"},
                api_key=self._api_key,
            )
        except Exception as exc:
            msg = f"litellm.acompletion failed for '{guid}': {exc}"
            logger.error(f"Skipping ad detection for '{guid}': {msg}")
            raise AdDetectionError(msg) from exc

        total_cost = self._compute_cost(response)
        content = response.choices[0].message.content

        for attempt in range(self._max_retries):
            try:
                detections = self._parse_response(content, guid)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                if attempt == self._max_retries - 1:
                    msg = f"Failed to parse LLM response for '{guid}' after {self._max_retries} attempts: {exc!r}"
                    logger.error(msg)
                    raise AdDetectionError(msg) from exc

                logger.warning(
                    f"Ad detection response parse failed for '{guid}' (attempt {attempt + 1}): {exc!r}"
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": _RETRY_PROMPT},
                ]
                try:
                    response = await litellm.acompletion(
                        model=self._model_id,
                        messages=messages,
                        response_format={"type": "json_object"},
                        api_key=self._api_key,
                    )
                except Exception as retry_exc:
                    msg = f"litellm.acompletion failed for '{guid}' on retry: {retry_exc}"
                    raise AdDetectionError(msg) from retry_exc
                total_cost += self._compute_cost(response)
                content = response.choices[0].message.content
            else:
                cost_record = AdDetectionCost(
                    provider=self._provider,
                    model=self._model,
                    cost=total_cost,
                )
                logger.debug(f"Detected {len(detections)} ad segment(s) for '{guid}'")
                return (guid, detections, cost_record)

        msg = f"Ad detection failed for '{guid}': exhausted retries"  # pragma: no cover
        raise AdDetectionError(msg)  # pragma: no cover
