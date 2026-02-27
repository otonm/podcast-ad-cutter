# Design: Split User-Editable Prompt Behavior from Hardcoded JSON Directives

## Context

`config.yaml` exposes a `prompts:` section (currently commented out) that lets users override the full LLM system prompt for ad detection and topic extraction. The full prompt includes both the behavioral instructions ("what counts as an ad") and the JSON format directives (schema, "Return only a JSON array", etc.). Letting users edit the JSON schema portion is error-prone — any change breaks response parsing.

The fix: expose only the behavioral text in config, append the JSON directives in code automatically.

## Approach

Use Pydantic `field_validator` with `validate_default=True` on `PromptsConfig`. The validator appends the hardcoded JSON suffix to whatever behavior text the user provides (or the default). The stored field value is the complete assembled prompt, so the pipeline (`ad_detector.py`, `topic_extractor.py`) is entirely untouched.

## Files to Change

### `config/config_loader.py`

Split the two existing prompt constants into behavior + suffix:

```python
_DEFAULT_AD_DETECTION_BEHAVIOR: str = (
    "Identify advertisements in this podcast transcript segment.\n"
    "An ad is any span where the host or another person or persons"
    " promote a product, service, or sponsor.\n"
    "Exclude brand mentions that are naturally part of the episode content."
)

_AD_DETECTION_JSON_SUFFIX: str = (
    "Return only a JSON array — no markdown, no preamble.\n"
    'Schema: [{"start_sec": float, "end_sec": float, "confidence": float,\n'
    '          "reason": str, "sponsor": str | null}]\n'
    "Return [] if no ads are found."
)

_DEFAULT_TOPIC_EXTRACTION_BEHAVIOR: str = (
    "Analyze the opening of this podcast transcript."
)

_TOPIC_EXTRACTION_JSON_SUFFIX: str = (
    "Return only a JSON object — no markdown, no preamble.\n"
    'Schema: {"domain": str, "topic": str, "hosts": list[str], "notes": str}'
)
```

Update `PromptsConfig`:

```python
class PromptsConfig(BaseModel, frozen=True):
    ad_detection: str = Field(default=_DEFAULT_AD_DETECTION_BEHAVIOR, validate_default=True)
    topic_extraction: str = Field(default=_DEFAULT_TOPIC_EXTRACTION_BEHAVIOR, validate_default=True)

    @field_validator("ad_detection", mode="after")
    @classmethod
    def _append_ad_suffix(cls, v: str) -> str:
        return v.rstrip("\n") + "\n" + _AD_DETECTION_JSON_SUFFIX

    @field_validator("topic_extraction", mode="after")
    @classmethod
    def _append_topic_suffix(cls, v: str) -> str:
        return v.rstrip("\n") + "\n" + _TOPIC_EXTRACTION_JSON_SUFFIX
```

Add `Field` to the pydantic import line.

### `config.yaml`

Replace the commented-out block with an always-active section containing behavior text only:

```yaml
prompts:
  # Customize the ad detection instructions. The JSON format directives are
  # appended automatically — do not add schema or formatting instructions here.
  ad_detection: |
    Identify advertisements in this podcast transcript segment.
    An ad is any span where the host or another person or persons promote a product, service, or sponsor.
    Exclude brand mentions that are naturally part of the episode content.

  # Customize the topic extraction instructions.
  topic_extraction: |
    Analyze the opening of this podcast transcript.
```

### `config.example.yaml`

Same change as `config.yaml`.

### `tests/test_config_loader.py`

Update `test_prompts_can_be_overridden` — the stored value is now behavior + suffix, not the raw string:

```python
def test_prompts_can_be_overridden(tmp_path):
    ...
    cfg = load_config(dst)
    assert "Custom ad prompt" in cfg.prompts.ad_detection
    assert "JSON array" in cfg.prompts.ad_detection   # suffix was appended
    assert "Custom topic prompt" in cfg.prompts.topic_extraction
    assert "JSON object" in cfg.prompts.topic_extraction
```

Optionally add a test that the default prompts include both the behavior text and the JSON suffix.

## No Changes Needed

- `pipeline/ad_detector.py` — reads `cfg.prompts.ad_detection` unchanged
- `pipeline/topic_extractor.py` — reads `cfg.prompts.topic_extraction` unchanged

## Verification

```bash
uv run pytest tests/test_config_loader.py -v
uv run ruff check config/config_loader.py tests/test_config_loader.py
uv run mypy config/config_loader.py
```
