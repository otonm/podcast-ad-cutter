# LLM Reasoning Token Logging — Design

**Date:** 2026-04-21
**Status:** Approved

## Problem

Three related issues make LLM reasoning tokens inaccessible in per-episode logs:

1. **Wrong attribute name.** `_log_llm_reasoning` checks `reasoning_content`, but providers like Qwen/Alibaba return the field as `reasoning`. The extraction silently returns `None` for these providers.
2. **Root logger level pre-filters debug messages.** Per-episode file handlers are set to DEBUG, but Python's root logger level (WARNING when `--debug` is not passed) discards DEBUG messages before they reach any handler — including the episode file handler.
3. **LiteLLM logger not silenced.** Line 118 in `main.py` that pins the `LiteLLM` logger to WARNING was commented out. When root is at DEBUG, LiteLLM's verbose raw-response JSON floods both the console and any file handlers.

**Goal:** Reasoning tokens always appear in per-episode log files, regardless of the `--debug` flag. LiteLLM's own debug noise does not appear anywhere.

## Approach

Adjust the root logger level for the duration of each episode's processing window, and explicitly silence LiteLLM's logger. Extract reasoning via a shared utility that handles multiple provider field names.

## Design

### `utils/llm.py` — shared reasoning extractor

Add `extract_llm_reasoning(response)` alongside `compute_completion_cost`:

```python
def extract_llm_reasoning(response: litellm.ModelResponse) -> str | None:
    msg = response.choices[0].message
    return getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
```

Tries `reasoning_content` first (Anthropic, Deepseek), falls back to `reasoning` (Alibaba/Qwen and other providers that do not normalise the field name).

### `utils/episode_log.py` — root level adjustment

`open_episode_log` saves the root logger's current level onto the handler object before lowering it to match the file handler level. `close_episode_log` reads it back and restores.

```python
# open_episode_log (additions):
root = logging.getLogger()
handler._pac_prior_root_level = root.level
if root.level > handler_level_int:
    root.setLevel(handler_level_int)
root.addHandler(handler)

# close_episode_log (additions):
prior = getattr(handler, "_pac_prior_root_level", None)
if prior is not None:
    root.setLevel(prior)
```

Public API signatures are unchanged. The `_pac_prior_root_level` attribute is an internal convention between the two functions in the same module.

This works safely because episodes are processed serially by `Pipeline.run` — there is no concurrent episode where a restore could race against a newly-opened handler.

### `main.py` → `configure_logging` — LiteLLM silencing

Uncomment and expand the existing (commented-out) suppression block:

```python
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
```

Child loggers with an explicit level set are not affected by changes to the root level in `open_episode_log`, so these pins hold even during episode processing.

### `components/ad_detector.py`

- Replace the body of `_log_llm_reasoning` to call `extract_llm_reasoning` from `utils.llm`.
- Remove the no-op `verbosity="high"` parameter from `_call_llm`.

### `components/topic_extractor.py`

- Add `_log_llm_reasoning` (identical to ad_detector).
- Call it in `extract()` immediately after `_call_llm` returns, matching the ad_detector pattern.
- Remove `verbosity="high"` from `_call_llm`.

## Testing

| Area | Tests |
|------|-------|
| `utils/llm.py` | `reasoning_content` present; `reasoning` present; neither returns `None`; `reasoning_content` is empty string falls through to `reasoning` |
| `utils/episode_log.py` | Root level lowered when file_level < current root; root restored after close; root level unchanged when file_level >= root level |
| `ad_detector.py` | Existing `emits_debug_when_present` and `silent_when_absent` updated to use `extract_llm_reasoning` |
| `topic_extractor.py` | Add `emits_debug_when_present` and `silent_when_absent` (mirrors ad_detector) |

## Files Changed

- `utils/llm.py`
- `utils/episode_log.py`
- `main.py`
- `components/ad_detector.py`
- `components/topic_extractor.py`
- `tests/test_llm.py` (new tests for `extract_llm_reasoning`)
- `tests/test_episode_log.py` (new root-level tests)
- `tests/test_ad_detector.py` (update existing reasoning tests)
- `tests/test_topic_extractor.py` (new reasoning tests)
