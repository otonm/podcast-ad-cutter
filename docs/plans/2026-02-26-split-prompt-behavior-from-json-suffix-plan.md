# Split Prompt Behavior from JSON Directives Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `prompts.ad_detection` and `prompts.topic_extraction` in `config.yaml` contain only user-editable behavioral text, while the JSON format directives are hardcoded in `config_loader.py` and appended automatically.

**Architecture:** `PromptsConfig` splits each prompt constant into a behavior string and a JSON suffix constant. A `field_validator` (with `validate_default=True`) appends the suffix to whatever text comes in — so `cfg.prompts.ad_detection` always delivers the complete assembled prompt. The pipeline is untouched.

**Tech Stack:** Pydantic v2 (`Field`, `field_validator`), PyYAML, pytest

---

### Task 1: Update `config/config_loader.py`

**Files:**
- Modify: `config/config_loader.py`

**Step 1: Add `Field` to the pydantic import**

In `config/config_loader.py`, line 7, change:

```python
from pydantic import BaseModel, computed_field, field_validator
```
to:
```python
from pydantic import BaseModel, Field, computed_field, field_validator
```

**Step 2: Replace the two default prompt constants**

Remove the existing `_DEFAULT_AD_DETECTION_PROMPT` and `_DEFAULT_TOPIC_EXTRACTION_PROMPT` constants (lines 13–26) and replace with four constants:

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

**Step 3: Update `PromptsConfig`**

Replace the existing `PromptsConfig` class body:

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

**Step 4: Run ruff and mypy to verify no issues**

```bash
uv run ruff check config/config_loader.py
uv run mypy config/config_loader.py
```

Expected: no errors (pre-existing `types-PyYAML` mypy warning is acceptable).

**Step 5: Commit**

```bash
git add config/config_loader.py
git commit -m "feat: split PromptsConfig into behavior text + hardcoded JSON suffix"
```

---

### Task 2: Update `tests/test_config_loader.py`

**Files:**
- Modify: `tests/test_config_loader.py`

**Step 1: Update `test_prompts_can_be_overridden`**

The existing test asserts exact equality against the raw config string — that no longer holds since the JSON suffix is appended. Replace the two final assertions:

Old:
```python
assert cfg.prompts.ad_detection == "Custom ad prompt"
assert cfg.prompts.topic_extraction == "Custom topic prompt"
```

New:
```python
assert "Custom ad prompt" in cfg.prompts.ad_detection
assert "JSON array" in cfg.prompts.ad_detection        # suffix was appended
assert "Custom topic prompt" in cfg.prompts.topic_extraction
assert "JSON object" in cfg.prompts.topic_extraction   # suffix was appended
```

**Step 2: Add a test verifying default prompts include both behavior and JSON suffix**

Append after `test_prompts_defaults_are_non_empty_strings`:

```python
def test_prompts_defaults_include_json_suffix():
    from config.config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    # Behavior text present
    assert "advertisement" in cfg.prompts.ad_detection.lower()
    # JSON suffix present
    assert "JSON array" in cfg.prompts.ad_detection
    assert "start_sec" in cfg.prompts.ad_detection
    # Behavior text present
    assert "transcript" in cfg.prompts.topic_extraction.lower()
    # JSON suffix present
    assert "JSON object" in cfg.prompts.topic_extraction
    assert "domain" in cfg.prompts.topic_extraction
```

**Step 3: Run the tests**

```bash
uv run pytest tests/test_config_loader.py -v
```

Expected: all pass.

**Step 4: Run ruff**

```bash
uv run ruff check tests/test_config_loader.py
```

Expected: no errors.

**Step 5: Commit**

```bash
git add tests/test_config_loader.py
git commit -m "test: update config_loader tests for split prompt behavior/suffix"
```

---

### Task 3: Update `config.yaml` and `config.example.yaml`

**Files:**
- Modify: `config.yaml`
- Modify: `config.example.yaml`

**Step 1: Update `config.yaml`**

Replace the commented-out `prompts:` block (lines 46–62) with:

```yaml
prompts:
  # Customize the ad detection instructions.
  # The JSON schema and format directives are added automatically — do not include them here.
  ad_detection: |
    Identify advertisements in this podcast transcript segment.
    An ad is any span where the host or another person or persons promote a product, service, or sponsor.
    Exclude brand mentions that are naturally part of the episode content.

  # Customize the topic extraction instructions.
  topic_extraction: |
    Analyze the opening of this podcast transcript.
```

**Step 2: Make the same change to `config.example.yaml`**

Replace its commented-out `prompts:` block (same lines) with identical content.

**Step 3: Run the full test suite to confirm nothing broke**

```bash
uv run pytest -v
```

Expected: all tests pass.

**Step 4: Commit**

```bash
git add config.yaml config.example.yaml
git commit -m "config: uncomment prompts section with behavior-only text"
```

---

### Task 4: Final verification

**Step 1: Run full lint + type check**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy config/config_loader.py
```

Expected: ruff clean; mypy shows only the pre-existing `types-PyYAML` warning, nothing new.

**Step 2: Run full test suite**

```bash
uv run pytest -v
```

Expected: all tests pass.
