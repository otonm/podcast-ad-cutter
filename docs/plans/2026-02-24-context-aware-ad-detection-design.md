# Context-Aware Ad Detection Design

**Date:** 2026-02-24

## Problem

Ad detection chunks transcripts into fixed 300-second windows regardless of LLM context window size. For modern models (Claude 200K, GPT-4o 128K), a typical 1-hour podcast (~9,000–12,000 tokens) fits comfortably in a single call. Chunking adds complexity, concurrent overhead, and gives the LLM worse global context for distinguishing ads from organic content.

## Goal

Send the full transcript in one LLM call when it fits. Fall back to chunking only when the transcript genuinely exceeds the model's context window.

## Approach: Context-Aware Single-Call with Chunking Fallback

### Decision logic (`detect_ads`)

1. Build the candidate full-transcript prompt (system + user message with `transcript.full_text`)
2. Call `_fits_in_context(messages, model, max_output_tokens)`:
   - `litellm.get_max_tokens(model)` → if `None` (unknown/local model): return `True` (assume it fits)
   - Safe input budget = `max_ctx * 0.85` (buffer for output tokens and token-count approximation)
   - `litellm.token_counter(model=model, messages=messages)` → compare against budget
3. If fits → `_detect_single()` → merge → return
4. If doesn't fit → log `WARNING` → fall back to existing chunk path

### New functions in `pipeline/ad_detector.py`

**`_fits_in_context(messages, model, max_output_tokens) → bool`**
- Returns `True` when context window is unknown (no chunking for unknown models)
- Returns `True` when prompt token count ≤ 85% of context window
- Returns `False` otherwise

**`_detect_single(transcript, context, cfg) → tuple[list[AdSegment], float]`**
- Builds system + user messages with full `transcript.full_text`
- Calls `llm_client.complete()` once (no semaphore needed)
- Parses with `_parse_ad_segments()`, returns `(segments, cost_usd)`

### Config changes

- Remove `max_tokens_per_chunk` from `AdDetectionConfig` — it was never referenced in code
- All other chunk fields (`chunk_duration_sec`, `chunk_overlap_sec`, `merge_gap_sec`, `min_confidence`) remain for the fallback path

### Log levels

| Situation | Level | Message |
|---|---|---|
| Single call used | INFO | `"Sending full transcript as single LLM call (%d tokens)"` |
| Context unknown, single call | INFO | `"Context window unknown for %s, sending full transcript"` |
| Overflow, falling back | WARNING | `"Transcript (%d tokens) exceeds context window (%d), falling back to chunking"` |

## Files to Modify

- `pipeline/ad_detector.py` — add `_fits_in_context`, `_detect_single`, modify `detect_ads`
- `config_loader.py` — remove `max_tokens_per_chunk` field from `AdDetectionConfig`
- `config.yaml` — remove `max_tokens_per_chunk`
- `config.example.yaml` — remove `max_tokens_per_chunk`, add comment on context-aware behavior
- `tests/test_ad_detector.py` — add tests for single-call path and `_fits_in_context` logic

## Verification

1. Run `uv run pytest tests/test_ad_detector.py -v` — all existing tests pass, new tests cover:
   - Single-call path when context fits
   - Chunk fallback when context is exceeded
   - `_fits_in_context` returns `True` when `get_max_tokens` returns `None`
2. Run `uv run mypy pipeline/ad_detector.py` — strict passes with zero errors
3. Run `uv run ruff check pipeline/ad_detector.py` — zero violations
