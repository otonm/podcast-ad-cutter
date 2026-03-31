"""AdDetector — identifies advertisement segments in podcast transcripts via an LLM."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import litellm

from models.ad_detection import AdDetectionCost, AdDetectionResponseSchema, AdSegmentDetection
from utils.exceptions import AdDetectionError
from utils.llm import compute_completion_cost

if TYPE_CHECKING:
    from models.topic import TopicExtraction
    from models.transcription import TranscriptionSegment

logger = logging.getLogger(__name__)


class _JsonValidateFailedError(Exception):
    """Internal sentinel: Groq rejected the JSON schema — the call should be retried without schema."""


_COMPLETION_RESERVE_TOKENS = 512

_SYSTEM_PROMPT_TEMPLATE: str = """You are an expert audio editor and detector of advertisements in podcast transcripts.
Your job is to identify existing advertisement segments in podcast transcripts for later removal or replacement.

## Definition of an ad

An ad is any span where the host or another speaker promotes a product, service, sponsor, organization, or another show
in a clearly promotional way.

Examples of common ad patterns:

- Introductions: 'When we return on...', 'We'll be right back after this message.'
- Openers: 'This episode is brought to you by...', 'Our sponsor today is...', 'Before we get started, a word from...',
  'Support for this show comes from...'
- Body: describing features, benefits, or personal endorsement of a product, usually include promo codes, discount URLs,
  affiliate links
- Closers/call to action: 'Use code [X] for a discount', 'Link in the description', 'Back to the show', '...at [url]'
- Return-to-show language: 'And we are back...', '<podcast name> is back...'

However, the exact wording can vary widely, and ads may not always follow these patterns.

Typical characteristics:

- Duration: typically 15-120 seconds
- Location: pre-roll (before intro), mid-roll (during episode), end-roll (after outro)
- Structure: usually spans multiple consecutive transcript segments
- Content: usually distinct from the episode's topic, however the theme can be similar
  (economics, politics, entertainment)
- Other: usually include promo codes, discount URLs or codes, affiliate links or are
  cross-promotion of other network shows

Ignore organic brand mentions that are part of the episode topic.

## Detection workflow

1. Scan the **entire** transcript from the first segment to the last segment before answering.
2. Identify every candidate ad region, especially:
   - the first 10 segments,
   - segments after an opener or introduction,
   - post-roll content after the outro or credits.
3. For each candidate, expand backward and forward to capture the full continuous ad block.
4. Merge only segments from the same continuous occurrence.
5. If the same sponsor or similar ad copy appears again later, return it as a separate object.

Use the provided episode domain and topic to distinguish ads from content.

**Determine**: segments that construct the ad block (the indices),
your confidence (0.0 = not at all, 1.0 = completely confident),
the reason for the message (the content),
the sponsor itself.

## Ad repetition

Repeated ads are common in podcast transcripts.
If the same sponsor or substantially the same ad copy appears again later, ALWAYS treat it as a new ad block.
Return **every** non-contiguous occurrence separately, even if the wording is identical or nearly identical.
**NEVER deduplicate** repeated ads.

## Input format

You will receive transcript segments in the format: [index][start_ms][end_ms] text.
Return the index of the first and last segment that belong to each ad block.
The included timestamps will be then used in code to determine the lenght of the ad.

## Output format

Reply with a JSON object with a single key "ads" whose value is an array of objects, no markdown, no preamble.
Each object must have exactly these keys:
- "index": integer segment index
- "confidence": float 0.0-1.0
- "sponsor": advertiser name (empty string if unknown)
- "ad_topic": brief description of the ad (empty string if unknown)

If no segments are ads, reply with {{"ads": []}}.

## Final notes

Prefer to over-identify rather than under-identify ads, but try to avoid false positives as much as possible.
Use the confidence score to indicate your certainty.
"""

_RETRY_PROMPT: str = """
Your previous response was not valid JSON or was missing the required structure.

Reply with a JSON object with a single key "ads" whose value is an array.
Each element must have exactly these four keys:
- "index" (integer)
- "confidence" (float 0.0-1.0)
- "sponsor" (string)
- "ad_topic" (string)

If there are no ads, reply with {"ads": []}. No other text."""

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

    async def _call_llm(
        self,
        guid: str,
        messages: list[dict[str, str]],
        *,
        use_schema: bool,
        use_reasoning: bool,
    ) -> litellm.ModelResponse:
        """Call litellm.acompletion; translate retryable API errors into internal sentinels.

        Raises:
            _JsonValidateFailedError: When Groq rejects the JSON schema (retryable).
            AdDetectionError: On any other API-level failure (non-retryable).

        """
        response_format = AdDetectionResponseSchema if use_schema else None
        reasoning_effort = "high" if use_reasoning else None
        try:
            return await litellm.acompletion(
                model=self._model_id,
                messages=messages,
                response_format=response_format,
                api_key=self._api_key,
                reasoning_effort=reasoning_effort,
                drop_params=True,
            )
        except litellm.BadRequestError as exc:
            if "json_validate_failed" in str(exc):
                raise _JsonValidateFailedError from exc
            msg = f"litellm.acompletion failed for '{guid}': {exc}"
            raise AdDetectionError(msg) from exc
        except Exception as exc:
            msg = f"litellm.acompletion failed for '{guid}': {exc}"
            raise AdDetectionError(msg) from exc

    def _parse_response(self, content: str, guid: str) -> list[AdSegmentDetection]:
        data = json.loads(content)
        items = data["ads"]
        if not isinstance(items, list):
            msg = f"Expected JSON array under 'ads', got {type(items).__name__} for '{guid}'"
            raise TypeError(msg)
        return [
            AdSegmentDetection(
                index=item["index"],
                confidence=item["confidence"],
                sponsor=item["sponsor"],
                ad_topic=item["ad_topic"],
            )
            for item in items
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
        logger.debug(
            f"Running ad detection for '{guid}': {len(segments)} segment(s), "
            f"topic={'set' if topic_extraction else 'unset'}"
        )
        system_prompt = self._build_system_prompt(topic_extraction)
        formatted = self._format_segments(segments)
        formatted = self._truncate_segments(formatted, system_prompt)
        messages = self._build_messages(system_prompt, formatted)
        total_cost = 0.0
        use_schema = True
        use_reasoning = True

        for attempt in range(self._max_retries):
            logger.debug(f"Calling LLM (attempt {attempt + 1}): schema={use_schema}, reasoning={use_reasoning}")
            try:
                response = await self._call_llm(
                    guid, messages, use_schema=use_schema, use_reasoning=use_reasoning
                )
                logger.debug(f"LLM Response: {response}")
            except _JsonValidateFailedError as exc:
                if attempt == self._max_retries - 1:
                    msg = f"JSON schema validation failed for '{guid}' after {self._max_retries} attempts"
                    logger.error(f"Skipping ad detection for '{guid}': {msg}")
                    raise AdDetectionError(msg) from exc
                logger.warning(
                    f"JSON schema validation failed for '{guid}' (attempt {attempt + 1}), retrying without schema"
                )
                messages = [*messages, {"role": "user", "content": _RETRY_PROMPT}]
                use_schema = False
                continue

            total_cost += compute_completion_cost(response, self._model_id)
            content = response.choices[0].message.content

            if response.choices[0].finish_reason == "length":
                if attempt == self._max_retries - 1:
                    msg = f"Completion truncated for '{guid}' after {self._max_retries} attempts"
                    logger.error(f"Skipping ad detection for '{guid}': {msg}")
                    raise AdDetectionError(msg)
                logger.warning(
                    f"Completion truncated (finish_reason=length) for '{guid}' "
                    f"(attempt {attempt + 1}), retrying without reasoning"
                )
                use_reasoning = False
                continue

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
                use_schema = False
                continue

            cost_record = AdDetectionCost(
                provider=self._provider,
                model=self._model,
                cost=total_cost,
            )
            if detections:
                logger.info(f"Detected {len(detections)} ad segment(s) for '{guid}'")
            else:
                logger.info(f"No ads detected for '{guid}'")
            return (guid, detections, cost_record)

        msg = f"Ad detection failed for '{guid}': exhausted retries"  # pragma: no cover
        raise AdDetectionError(msg)  # pragma: no cover
