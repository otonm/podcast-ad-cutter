"""TopicExtractor — extracts topic, hosts, and show name from episode transcriptions via an LLM."""

from __future__ import annotations

import json
import logging

import litellm

from models.topic import TopicExtraction, TopicExtractionCost, TopicExtractionSchema
from utils.exceptions import TopicExtractionError
from utils.llm import compute_completion_cost

logger = logging.getLogger(__name__)


class _JsonValidateFailedError(Exception):
    """Internal sentinel: Groq rejected the JSON schema — the call should be retried without schema."""


_SYSTEM_PROMPT: str = """You are an assistant to a podcast creator that assists with metadata gathering.
Your job is to determine the main topic of a podcast from a given trascription segment.
You are given a transcription of the beggining of a podcast (including possible ad segments etc.).
Analyze the transcript and determine to the best of your ability the main topic of conversation.
Summarize the topic in five sentences.
Try to ignore any ads or promotions that are usually inserted at the beginning of an episode.
Return information about the topic of the episode as a JSON object containing exactly these keys:
- "topic": A 3-sentence description of the main topic discussed.
- "hosts": A comma-separated list of host names as mentioned in the transcript.
- "show": The name of the podcast show as mentioned in the transcript.

No commentary, no markdown, no preamble.
"""

_COMPLETION_RESERVE_TOKENS = 512

_RETRY_PROMPT: str = """
Your previous response was not valid JSON or was missing required keys.
Reply with only a JSON object containing exactly these three keys:
"topic", "hosts", "show". No other text, no commentary, no markdown, no preamble.
"""

type TopicExtractionResult = tuple[str, TopicExtraction, TopicExtractionCost]


class TopicExtractor:
    """Calls a chat LLM via litellm to extract topic, hosts, and show from a transcript.

    Determines the model's context window at construction time and truncates
    oversized transcripts to fit.  Cost is read from the response hidden params,
    with ``litellm.completion_cost`` as a fallback.

    When the LLM returns malformed JSON or omits required keys, the bad response
    is appended to the conversation and a correction prompt is sent.  This repeats
    up to ``max_retries`` times before raising :class:`TopicExtractionError`.
    API-level failures (network errors, auth) are not retried.

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

    def _build_messages(self, transcript: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{transcript}"},
        ]

    def _truncate_transcript(self, transcript: str) -> str:
        """Trim the transcript so the full prompt fits within the context window."""
        budget = self._max_input_tokens - _COMPLETION_RESERVE_TOKENS
        messages = self._build_messages(transcript)
        token_count = litellm.token_counter(model=self._model_id, messages=messages)
        if token_count <= budget:
            return transcript

        # Estimate how many characters to keep.  Use a ratio to avoid an O(n) loop.
        ratio = budget / token_count
        trimmed = transcript[: int(len(transcript) * ratio)]
        logger.warning(
            f"Transcript truncated from ~{token_count} to ~{budget} tokens "
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
            TopicExtractionError: On any other API-level failure (non-retryable).

        """
        response_format = TopicExtractionSchema if use_schema else None
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
            raise TopicExtractionError(msg) from exc
        except Exception as exc:
            msg = f"litellm.acompletion failed for '{guid}': {exc}"
            raise TopicExtractionError(msg) from exc

    def _parse_response(self, content: str, guid: str, podcast: str, title: str) -> TopicExtraction:
        """Parse an LLM JSON response into a TopicExtraction; raises JSONDecodeError or KeyError on failure."""
        data = json.loads(content)
        return TopicExtraction(
            guid=guid,
            podcast=podcast,
            title=title,
            topic=data["topic"],
            hosts=data["hosts"],
            show=data["show"],
        )

    async def extract(
        self,
        guid: str,
        podcast: str,
        title: str,
        transcript: str,
    ) -> TopicExtractionResult:
        """Extract topic metadata from one episode transcription.

        Args:
            guid: Episode GUID — used in error messages and result objects.
            podcast: Config feed title (logical feed identifier).
            title: Episode title.
            transcript: Full transcription text.

        Returns:
            ``(guid, TopicExtraction, TopicExtractionCost)``.

        Raises:
            TopicExtractionError: On any litellm/API failure or malformed JSON response.

        """
        logger.debug(f"Extracting topic for '{guid}' with model {self._model_id}")
        trimmed = self._truncate_transcript(transcript)
        messages = self._build_messages(trimmed)
        total_cost = 0.0
        use_schema = True
        use_reasoning = True

        for attempt in range(self._max_retries):
            logger.debug(f"Calling LLM (attempt {attempt + 1}): schema={use_schema}, reasoning={use_reasoning}")
            try:
                response = await self._call_llm(
                    guid, messages, use_schema=use_schema, use_reasoning=use_reasoning
                )
                logger.debug(f"LLM response: {response}")
            except _JsonValidateFailedError as exc:
                if attempt == self._max_retries - 1:
                    msg = f"JSON schema validation failed for '{guid}' after {self._max_retries} attempts"
                    logger.error(f"Skipping topic extraction for '{guid}': {msg}")
                    raise TopicExtractionError(msg) from exc
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
                    logger.error(f"Skipping topic extraction for '{guid}': {msg}")
                    raise TopicExtractionError(msg)
                logger.warning(
                    f"Completion truncated (finish_reason=length) for '{guid}' "
                    f"(attempt {attempt + 1}), retrying without reasoning"
                )
                use_reasoning = False
                continue

            try:
                extraction = self._parse_response(content, guid, podcast, title)
            except (json.JSONDecodeError, KeyError) as exc:
                if attempt == self._max_retries - 1:
                    msg = f"Failed to parse LLM response for '{guid}' after {self._max_retries} attempts: {exc!r}"
                    logger.error(msg)
                    raise TopicExtractionError(msg) from exc
                logger.warning(
                    f"Topic extraction response parse failed for '{guid}' (attempt {attempt + 1}): {exc!r}"
                )
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": _RETRY_PROMPT},
                ]
                use_schema = False
                continue

            cost_record = TopicExtractionCost(
                provider=self._provider,
                model=self._model,
                cost=total_cost,
            )
            logger.info(f"Extracted topic for '{guid}': show={extraction.show!r}, hosts={extraction.hosts!r}")
            return (guid, extraction, cost_record)

        msg = f"Topic extraction failed for '{guid}': exhausted retries"  # pragma: no cover
        raise TopicExtractionError(msg)  # pragma: no cover
