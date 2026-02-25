# Design: Four Pipeline Optimizations

**Date:** 2026-02-25

## Overview

Four targeted improvements to the podcast ad cutter pipeline:

1. API key validation at startup using `litellm.check_valid_key`
2. Fix transcription cost calculation for `groq/whisper-large-v3`
3. Extract LLM system prompts to `config.yaml`
4. Log a summary info line after ad detection

---

## 1. API Key Validation

### Problem

`config_loader.py` validates provider names and YAML structure but does not verify that the configured API keys actually work before the pipeline starts. A bad key surfaces only after transcription or LLM calls fail mid-run.

### Design

Add a `validate_api_keys(cfg: AppConfig) -> None` function to `config/config_loader.py`. Call it from `main.py` immediately after `load_config()`, before `asyncio.run()`.

**Provider → env var mapping:**

| Provider   | Env var           |
|------------|-------------------|
| `groq`     | `GROQ_API_KEY`    |
| `openai`   | `OPENAI_API_KEY`  |
| `openrouter` | `OPENROUTER_API_KEY` |

**Logic:**

1. Collect the set of `(provider_model, api_key)` pairs needed for both `transcription` and `interpretation` configs. Dedup by provider so a provider used for both only gets probed once.
2. For each pair, read the key from `os.environ`. If the key is missing (empty or not set), raise `ConfigError` with a clear message.
3. Call `litellm.check_valid_key(model=provider_model, api_key=key)`. If it returns `False`, raise `ConfigError("Invalid API key for {provider_model}")`.

**Why before `asyncio.run()`:** `check_valid_key` is synchronous and makes a network call. Running it before the event loop avoids thread-safety concerns and provides fail-fast startup feedback.

**Files changed:** `config/config_loader.py`, `main.py`

---

## 2. Transcription Cost Calculation Fix

### Problem

litellm's `atranscription()` cost calculator strips the provider prefix and incorrectly resolves `groq/whisper-large-v3` as `custom_llm_provider=openai`, then fails to find `openai/whisper-large-v3`. As a result, `_hidden_params.get("response_cost")` returns `None` and cost is recorded as `0.0`.

`groq/whisper-large-v3` **is** present in `litellm.model_cost` with `input_cost_per_second: 3.083e-05`.

### Design

In `pipeline/llm_client.py`, after `atranscription()` returns, add a fallback cost computation:

```python
cost = result._hidden_params.get("response_cost") or 0.0
if not cost:
    duration: float = result.get("duration", 0.0)
    model_info = litellm.model_cost.get(cfg.provider_model, {})
    rate: float = model_info.get("input_cost_per_second", 0.0)
    cost = duration * rate
```

- Uses litellm's own price data — no hardcoded rates
- `result.get("duration")` is available in `verbose_json` responses (per OpenAI spec, returned by Groq)
- Bypasses only the broken provider-detection step
- If the model is not in `litellm.model_cost`, `rate` is `0.0` and cost stays `0.0` — acceptable degradation

**Files changed:** `pipeline/llm_client.py`

---

## 3. Extract LLM Prompts to Config

### Problem

`AD_DETECTION_PROMPT` and `TOPIC_EXTRACTION_PROMPT` are module-level constants in `pipeline/ad_detector.py` and `pipeline/topic_extractor.py`. Changing them requires editing source code.

### Design

Add a `PromptsConfig` Pydantic model to `config/config_loader.py` and add it as an optional field on `AppConfig`.

```python
class PromptsConfig(BaseModel, frozen=True):
    ad_detection: str = _DEFAULT_AD_DETECTION_PROMPT
    topic_extraction: str = _DEFAULT_TOPIC_EXTRACTION_PROMPT
```

The default strings are module-level constants `_DEFAULT_AD_DETECTION_PROMPT` and `_DEFAULT_TOPIC_EXTRACTION_PROMPT` defined in `config/config_loader.py`. The pipeline modules import those defaults to seed `PromptsConfig`, so behaviour is unchanged when the user does not set `prompts:` in `config.yaml`.

**Plumbing:**
- `detect_ads()` and `_build_messages()` in `pipeline/ad_detector.py` switch from the local constant to `cfg.prompts.ad_detection`. No signature changes (`cfg: AppConfig` is already passed).
- `extract_topic()` in `pipeline/topic_extractor.py` switches to `cfg.prompts.topic_extraction`.

**`config.yaml`:** Add a `prompts:` section with both keys commented out, showing the full default text. Users uncomment and edit to override.

**`config.example.yaml`:** Same addition.

**Files changed:** `config/config_loader.py`, `pipeline/ad_detector.py`, `pipeline/topic_extractor.py`, `config.yaml`, `config.example.yaml`

---

## 4. Ad Detection Summary Info Line

### Problem

After ad detection completes, there is no visible summary of how many segments were found or how much audio will be cut.

### Design

In `runner.py`'s `detect_ads()` function, after `merge_segments()`, add:

```python
above_threshold = [s for s in merged if s.confidence >= cfg.ad_detection.min_confidence]
total_ms = sum(s.end_ms - s.start_ms for s in above_threshold)
logger.info(
    f"Ad detection complete: {len(merged)} segment(s) found, "
    f"{len(above_threshold)} above threshold "
    f"({total_ms / 1000:.1f}s to cut)"
)
```

This mirrors the log contract in `CLAUDE.md`: `INFO` for every pipeline milestone a user would care about.

**Files changed:** `pipeline/runner.py`

---

## Affected Files Summary

| File | Change |
|------|--------|
| `config/config_loader.py` | Add `PromptsConfig`, `validate_api_keys()`, prompt defaults |
| `main.py` | Call `validate_api_keys(cfg)` before `asyncio.run()` |
| `pipeline/llm_client.py` | Fallback cost computation in `transcribe()` |
| `pipeline/ad_detector.py` | Use `cfg.prompts.ad_detection` instead of local constant |
| `pipeline/topic_extractor.py` | Use `cfg.prompts.topic_extraction` instead of local constant |
| `pipeline/runner.py` | Add summary `logger.info` after ad detection |
| `config.yaml` | Add commented-out `prompts:` section |
| `config.example.yaml` | Add commented-out `prompts:` section |
