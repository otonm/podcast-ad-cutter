"""AdDetector — identifies advertisement segments in podcast transcripts via an LLM."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import litellm

from models.ad_detection import AdDetectionCost, AdDetectionResponseSchema, AdSegmentDetection
from utils.exceptions import AdDetectionError
from utils.llm import compute_completion_cost, extract_llm_reasoning

if TYPE_CHECKING:
    from models.topic import TopicExtraction
    from models.transcription import TranscriptionSegment

logger = logging.getLogger(__name__)


class _JsonValidateFailedError(Exception):
    """Internal sentinel: Groq rejected the JSON schema — the call should be retried without schema."""


class _ContextWindowExceededError(Exception):
    """Internal sentinel: API rejected the request because the prompt exceeded the context window."""


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
- Structure: always spans multiple consecutive transcript segments — each segment is only a few seconds long
- Content: usually distinct from the episode's topic, however the theme can be similar
  (economics, politics, entertainment)
- Other: usually include promo codes, discount URLs or codes, affiliate links or are
  cross-promotion of other network shows

A self-promotion by the host for their own show or content is usually not considered an ad for this purpose,
and should be included in the results with a low confidence score.

## Detection workflow

1. Review the given transcript segments in order. Always consider neighboring segments as part of the
same potential ad block, even if they individually seem innocuous.
2. Identify every candidate ad region, especially:
   - the first 10 segments,
   - segments after an opener or introduction,
   - post-roll content after the outro or credits.
3. Group all consecutive segments belonging to the same ad into one entry, listing ALL their indices.
4. If the same sponsor or similar ad copy appears again later, return it as a separate object.

Use the provided episode domain and topic to distinguish ads from content.

**Determine**:
- ALL consecutive segment indices that construct the ad block,
- your confidence (0.0 = not at all, 1.0 = completely confident),
- the reason for the message (the content),
- the sponsor itself.

## Ad repetition

Repeated ads are common in podcast transcripts.

If the same sponsor or substantially the same ad copy appears again later, ALWAYS treat it as a separate ad block.
Return **every** non-contiguous occurrence separately, even if the wording is identical or nearly identical.
**NEVER deduplicate** repeated ads.

## Input format

You will receive transcript segments in the format: [index][start_ms][end_ms] text.
Return ALL consecutive segment indices that belong to each ad block as a list.
The included timestamps will be then used in code to determine the length of the ad.

## Output format

Reply with a JSON object with a single key "ads" whose value is an array of objects, no markdown, no preamble.
Each object must have exactly these keys:
- "indices": list of all consecutive integer segment indices that make up this ad block (e.g. [3, 4, 5])
- "confidence": float 0.0-1.0
- "sponsor": advertiser name (empty string if unknown)
- "ad_topic": brief description of the ad (empty string if unknown)

If no segments are ads, reply with {{"ads": []}}.

## Final notes

Prefer to over-identify rather than under-identify ads, but try to avoid false positives as much as possible.
Use the confidence score to indicate your certainty.

## Show title: {show}

## Hosts: {hosts}

## Episode topic: {topic}

"""

_RETRY_PROMPT: str = """
Your previous response was not valid JSON or was missing the required structure.

Reply with a JSON object with a single key "ads" whose value is an array.
Each element must have exactly these four keys:
- "indices" (list of integers — all consecutive segment indices for this ad block)
- "confidence" (float 0.0-1.0)
- "sponsor" (string)
- "ad_topic" (string)

If there are no ads, reply with {"ads": []}. No other text."""

_SINGLE_INDEX_RETRY_PROMPT: str = """
One or more of your identified ad segments contains only a single transcript index.
Each ad segment must span multiple consecutive indices - ads are typically 15-120 seconds long
and each transcript segment is only a few seconds.

For every ad block, list ALL consecutive segment indices that belong to it under the "indices" key.
Do not return any ad block with only one index.

Reply again with only: {"ads": [...]} - no markdown, no commentary."""

type AdDetectionResult = tuple[str, list[AdSegmentDetection], AdDetectionCost]


class AdDetector:
    """Calls a chat LLM via litellm to identify advertisement segments in a transcript.

    Uses topic context (show, hosts, topic) to distinguish ads from content.
    Retries up to ``max_retries`` times on malformed JSON.  If the LLM returns an
    ad block with only a single segment index, one additional retry is issued with
    explicit instructions to return all consecutive indices per block.
    API-level failures are not retried.

    Args:
        provider: Provider name, e.g. ``"openai"`` or ``"groq"``.
        model: Model name, e.g. ``"gpt-4o-mini"``.
        api_key: API key for the provider.
        max_retries: Maximum number of LLM call attempts (default 3).

    """

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        max_retries: int = 3,
        context_window: int | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._model_id = model if provider == "openai" else f"{provider}/{model}"
        self._api_key = api_key
        self._max_retries = max_retries
        self._context_window = context_window
        self._reasoning_supported = litellm.supports_reasoning(self._model_id)
        if not self._reasoning_supported:
            logger.warning(
                f"Model '{self._model_id}' does not support reasoning; reasoning will be disabled"
            )

    def _get_context_window_limit(self) -> int:
        """Return the model's max input token count, falling back to 8192.

        Called lazily only when a ContextWindowExceededError occurs.
        """
        try:
            info = litellm.get_model_info(self._model_id)
            limit = info.get("max_input_tokens") or info.get("max_tokens")
            if limit:
                logger.debug(f"Model {self._model_id} context window is {limit} tokens")
                return int(limit)
        except Exception as exc:  # noqa: BLE001 — litellm raises various errors for unknown models
            logger.warning(
                f"Could not retrieve model info for {self._model_id} ({exc}), defaulting context window to 16384 tokens"
            )
        return 16384

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

    def _truncate_segments(self, formatted_segments: str, system_prompt: str, limit: int) -> str:
        """Trim the formatted segments so the full prompt fits within ``limit`` tokens."""
        budget = limit - _COMPLETION_RESERVE_TOKENS
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

    def _log_llm_reasoning(self, response: litellm.ModelResponse, guid: str) -> None:
        reasoning = extract_llm_reasoning(response)
        if reasoning:
            logger.debug(f"LLM reasoning for '{guid}':\n{reasoning}")

    def _log_detection_summary(
        self,
        guid: str,
        segments: list[TranscriptionSegment],
        detections: list[AdSegmentDetection],
    ) -> None:
        ad_indices: set[int] = set()
        for d in detections:
            ad_indices.update(d.indices)
            start_ms = segments[d.indices[0]].start_ms if d.indices and d.indices[0] < len(segments) else 0
            end_ms = segments[d.indices[-1]].end_ms if d.indices and d.indices[-1] < len(segments) else 0
            logger.debug(
                f"AD '{guid}' indices={d.indices} "
                f"time={start_ms}ms-{end_ms}ms "
                f"confidence={d.confidence:.0%} "
                f"sponsor={d.sponsor!r} topic={d.ad_topic!r}"
            )
        non_ad = [i for i in range(len(segments)) if i not in ad_indices]
        logger.debug(f"Non-ad segment indices for '{guid}': {non_ad}")

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
                temperature=0.3,
                drop_params=True
            )
        except litellm.ContextWindowExceededError as exc:
            raise _ContextWindowExceededError from exc
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
        detections = []
        for item in items:
            indices = item["indices"]
            if not isinstance(indices, list):
                msg = f"Expected list for 'indices', got {type(indices).__name__} for '{guid}'"
                raise TypeError(msg)
            detections.append(AdSegmentDetection(
                indices=indices,
                confidence=item["confidence"],
                sponsor=item["sponsor"],
                ad_topic=item["ad_topic"],
            ))
        return detections

    async def detect(  # noqa: PLR0912, PLR0915, C901
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
        formatted_original = self._format_segments(segments)
        if self._context_window is not None:
            formatted = self._truncate_segments(formatted_original, system_prompt, self._context_window)
        else:
            formatted = formatted_original
        messages = self._build_messages(system_prompt, formatted)
        total_cost = 0.0
        use_schema = True
        use_reasoning = self._reasoning_supported
        single_index_retry_done = False

        for attempt in range(self._max_retries):
            logger.debug(f"Calling LLM (attempt {attempt + 1}): schema={use_schema}, reasoning={use_reasoning}")
            try:
                response = await self._call_llm(
                    guid, messages, use_schema=use_schema, use_reasoning=use_reasoning
                )
                self._log_llm_reasoning(response, guid)
            except _ContextWindowExceededError as exc:
                if attempt == self._max_retries - 1:
                    msg = f"Context window exceeded for '{guid}' after {self._max_retries} attempts"
                    logger.error(f"Skipping ad detection for '{guid}': {msg}")
                    raise AdDetectionError(msg) from exc
                limit = self._get_context_window_limit()
                formatted = self._truncate_segments(formatted_original, system_prompt, limit)
                messages = self._build_messages(system_prompt, formatted)
                use_schema = True
                single_index_retry_done = False
                continue
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
            logger.debug(f"LLM raw response content for '{guid}': {content}")

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
                self._log_detection_summary(guid, segments, detections)
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

            # If any ad block has only one index, retry once with specific guidance.
            if (
                not single_index_retry_done
                and attempt < self._max_retries - 1
                and any(len(d.indices) == 1 for d in detections)
            ):
                logger.warning(
                    f"Ad detection for '{guid}' returned single-index block(s) "
                    f"(attempt {attempt + 1}), retrying with index-expansion prompt"
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": _SINGLE_INDEX_RETRY_PROMPT},
                ]
                single_index_retry_done = True
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
