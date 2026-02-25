# Four Pipeline Optimizations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add API key validation at startup, fix transcription cost calculation, extract LLM prompts to config, and log an ad detection summary line.

**Architecture:** All four tasks are independent — each touches a small, isolated area. Tasks can be done in any order. The prompts task (Task 3) has a strict sub-ordering: config model first, then pipeline wiring, then YAML edits.

**Tech Stack:** Python 3.12, litellm, Pydantic v2, pytest with `asyncio_mode = "auto"`, uv

**Constraint from CLAUDE.md:** `pipeline/llm_client.py` is the **only** module that imports `litellm`. `validate_api_keys()` therefore lives there, not in `config_loader.py`.

---

## Task 1: Fix transcription cost in `pipeline/llm_client.py`

**Files:**
- Modify: `pipeline/llm_client.py` — `transcribe()` function (lines 61–86)
- Modify: `tests/test_llm_client.py`

**Background:** `litellm.atranscription()` internally resolves `groq` provider as `openai` when computing cost, so `_hidden_params["response_cost"]` comes back as `None`. The entry `groq/whisper-large-v3` *is* in `litellm.model_cost` with `input_cost_per_second: 3.083e-05`. The `verbose_json` response includes a `duration` field (total seconds of audio). Fix: fall back to `litellm.model_cost[cfg.provider_model]["input_cost_per_second"] × duration`.

---

### Step 1 — Write the failing test

Open `tests/test_llm_client.py`. Add after the existing `test_transcribe_returns_dict`:

```python
async def test_transcribe_fallback_cost_from_model_cost(tmp_path):
    from pipeline.llm_client import transcribe

    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"fake audio data")

    mock_result = MagicMock()
    mock_result.get = lambda key, default=None: {
        "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
        "duration": 120.0,
    }.get(key, default)
    mock_result._hidden_params = {"response_cost": None}

    config = TranscriptionConfig(provider="groq", model="whisper-large-v3", language="en")
    expected_cost = 120.0 * 3.083e-05

    with patch("pipeline.llm_client.litellm") as mock_litellm:
        mock_litellm.atranscription = AsyncMock(return_value=mock_result)
        mock_litellm.APIError = Exception
        mock_litellm.model_cost = {
            "groq/whisper-large-v3": {"input_cost_per_second": 3.083e-05}
        }
        _, cost = await transcribe(audio_file, config)

    assert cost == pytest.approx(expected_cost)
```

### Step 2 — Run test, confirm it fails

```bash
uv run pytest tests/test_llm_client.py::test_transcribe_fallback_cost_from_model_cost -v
```

Expected: **FAIL** — cost is `0.0`, not `expected_cost`.

### Step 3 — Implement the fallback in `pipeline/llm_client.py`

Find the `transcribe()` function. The current cost extraction line (around line 82) reads:

```python
cost: float = result._hidden_params.get("response_cost") or 0.0
```

Replace that single line with:

```python
cost: float = result._hidden_params.get("response_cost") or 0.0
if not cost:
    duration: float = result.get("duration", 0.0)
    model_info: dict[str, object] = litellm.model_cost.get(cfg.provider_model, {})
    rate: float = float(model_info.get("input_cost_per_second", 0.0))
    cost = duration * rate
```

### Step 4 — Run all llm_client tests, confirm they pass

```bash
uv run pytest tests/test_llm_client.py -v
```

Expected: **all PASS**.

### Step 5 — Commit

```bash
git add pipeline/llm_client.py tests/test_llm_client.py
git commit -m "fix: compute transcription cost from litellm.model_cost when hidden_params returns None"
```

---

## Task 2: Ad detection summary log line in `pipeline/runner.py`

**Files:**
- Modify: `pipeline/runner.py` — `detect_ads()` function (around line 123)

No new test: this is a single `logger.info` call. Existing tests exercise the surrounding logic.

---

### Step 1 — Locate the target in `pipeline/runner.py`

Find the `detect_ads()` function (line 115). Find these two consecutive lines:

```python
merged = merge_segments(segments, cfg.ad_detection.merge_gap_sec)
await ad_repo.save_all(merged)
```

### Step 2 — Insert the summary log between those two lines

```python
merged = merge_segments(segments, cfg.ad_detection.merge_gap_sec)
above_threshold = [s for s in merged if s.confidence >= cfg.ad_detection.min_confidence]
total_ms = sum(s.end_ms - s.start_ms for s in above_threshold)
logger.info(
    f"Ad detection complete: {len(merged)} segment(s) found, "
    f"{len(above_threshold)} above threshold "
    f"({total_ms / 1000:.1f}s to cut)"
)
await ad_repo.save_all(merged)
```

### Step 3 — Run full test suite, confirm nothing broke

```bash
uv run pytest -v
```

Expected: **all PASS**.

### Step 4 — Commit

```bash
git add pipeline/runner.py
git commit -m "feat: log ad detection summary with segment count and seconds to cut"
```

---

## Task 3: Extract LLM prompts to config

Three sub-parts in strict order: (A) add `PromptsConfig` to `config_loader.py`, (B) wire through pipeline modules, (C) add commented section to YAML files.

**Files:**
- Modify: `config/config_loader.py`
- Modify: `pipeline/ad_detector.py`
- Modify: `pipeline/topic_extractor.py`
- Modify: `config.yaml`
- Modify: `config.example.yaml`
- Modify: `tests/test_config_loader.py`
- No change: `tests/fixtures/test_config.yaml` — omitting `prompts:` exercises the defaults

---

### Part A — `PromptsConfig` in `config/config_loader.py`

#### Step 1 — Write the failing tests

Add to `tests/test_config_loader.py`:

```python
def test_prompts_defaults_are_non_empty_strings():
    from config.config_loader import load_config

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    assert isinstance(cfg.prompts.ad_detection, str)
    assert len(cfg.prompts.ad_detection) > 10
    assert isinstance(cfg.prompts.topic_extraction, str)
    assert len(cfg.prompts.topic_extraction) > 10


def test_prompts_can_be_overridden(tmp_path):
    import shutil
    from config.config_loader import load_config

    dst = tmp_path / "config.yaml"
    shutil.copy(Path("tests/fixtures/test_config.yaml"), dst)
    with dst.open("a") as f:
        f.write(
            "\nprompts:\n"
            "  ad_detection: 'Custom ad prompt'\n"
            "  topic_extraction: 'Custom topic prompt'\n"
        )
    cfg = load_config(dst)
    assert cfg.prompts.ad_detection == "Custom ad prompt"
    assert cfg.prompts.topic_extraction == "Custom topic prompt"
```

#### Step 2 — Run tests, confirm they fail

```bash
uv run pytest tests/test_config_loader.py::test_prompts_defaults_are_non_empty_strings \
             tests/test_config_loader.py::test_prompts_can_be_overridden -v
```

Expected: **FAIL** — `AppConfig` has no `prompts` attribute.

#### Step 3 — Add `_DEFAULT_*` constants and `PromptsConfig` to `config/config_loader.py`

The current prompts live in `pipeline/ad_detector.py` (constant `AD_DETECTION_PROMPT`, lines 20–26) and `pipeline/topic_extractor.py` (constant `TOPIC_EXTRACTION_PROMPT`, lines 15–18). Copy their exact text into `config_loader.py` as private module-level constants.

Add these two constants **after the imports, before `class AudioFormat`**. Copy the exact strings from the pipeline modules so defaults are identical to current behaviour:

```python
_DEFAULT_AD_DETECTION_PROMPT: str = (
    "Identify advertisements in this podcast transcript segment.\n"
    "An ad is any span where the host or another person or persons"
    " promote a product, service, or sponsor.\n"
    "Exclude brand mentions that are naturally part of the episode content.\n"
    "Return only a JSON array — no markdown, no preamble.\n"
    'Schema: [{"start_sec": float, "end_sec": float, "confidence": float,\n'
    '          "reason": str, "sponsor": str | null}]\n'
    "Return [] if no ads are found."
)

_DEFAULT_TOPIC_EXTRACTION_PROMPT: str = (
    "Analyze the opening of this podcast transcript.\n"
    "Return only a JSON object — no markdown, no preamble.\n"
    'Schema: {"domain": str, "topic": str, "hosts": list[str], "notes": str}'
)
```

Add `PromptsConfig` class **after `RetryConfig`**:

```python
class PromptsConfig(BaseModel, frozen=True):
    ad_detection: str = _DEFAULT_AD_DETECTION_PROMPT
    topic_extraction: str = _DEFAULT_TOPIC_EXTRACTION_PROMPT
```

Add `prompts` field to `AppConfig` **after `episodes_to_keep`**:

```python
class AppConfig(BaseModel, frozen=True):
    feeds: list[FeedConfig]
    paths: PathsConfig
    transcription: TranscriptionConfig
    interpretation: InterpretationConfig
    ad_detection: AdDetectionConfig
    audio: AudioConfig
    logging: LoggingConfig
    retry: RetryConfig
    episodes_to_keep: int = 5
    prompts: PromptsConfig = PromptsConfig()
```

#### Step 4 — Run tests, confirm they pass

```bash
uv run pytest tests/test_config_loader.py -v
```

Expected: **all PASS**.

---

### Part B — Wire prompts through pipeline modules

#### Step 5 — Update `_build_messages` in `pipeline/ad_detector.py`

`_build_messages()` currently hard-codes `AD_DETECTION_PROMPT`. Add a `system_prompt` keyword-only parameter:

Current signature (line 79):
```python
def _build_messages(topic_context: TopicContext, transcript_text: str) -> list[dict[str, str]]:
```

New signature:
```python
def _build_messages(
    topic_context: TopicContext,
    transcript_text: str,
    *,
    system_prompt: str,
) -> list[dict[str, str]]:
```

In the body, replace `AD_DETECTION_PROMPT` with `system_prompt`:
```python
    return [
        {"role": "system", "content": system_prompt},
        ...
    ]
```

Update the three call sites (all already have `cfg` in scope):

- `detect_ads()` line 51: `full_messages = _build_messages(topic_context, transcript.full_text)`
  → `full_messages = _build_messages(topic_context, transcript.full_text, system_prompt=cfg.prompts.ad_detection)`

- `_detect_single()` line 105: `messages = _build_messages(topic_context, transcript.full_text)`
  → `messages = _build_messages(topic_context, transcript.full_text, system_prompt=cfg.prompts.ad_detection)`

- `_detect_chunk()` line 148: `messages = _build_messages(topic_context, chunk.text)`
  → `messages = _build_messages(topic_context, chunk.text, system_prompt=cfg.prompts.ad_detection)`

Remove the old module-level constant `AD_DETECTION_PROMPT` (lines 20–26).

#### Step 6 — Update `pipeline/topic_extractor.py`

Replace the usage of `TOPIC_EXTRACTION_PROMPT` at line 52:

Current:
```python
{"role": "system", "content": TOPIC_EXTRACTION_PROMPT},
```

New:
```python
{"role": "system", "content": cfg.prompts.topic_extraction},
```

Remove the module-level constant `TOPIC_EXTRACTION_PROMPT` (lines 15–18).

#### Step 7 — Run full test suite, confirm nothing broke

```bash
uv run pytest -v
```

Expected: **all PASS**.

---

### Part C — Add commented `prompts:` section to YAML files

#### Step 8 — Append to `config.yaml`

Add this block at the end of `config.yaml`:

```yaml
# prompts:
#   # Override the LLM system prompt for ad detection.
#   # Uncomment and edit to customise. Default shown below.
#   ad_detection: |
#     Identify advertisements in this podcast transcript segment.
#     An ad is any span where the host or another person or persons promote a product, service, or sponsor.
#     Exclude brand mentions that are naturally part of the episode content.
#     Return only a JSON array — no markdown, no preamble.
#     Schema: [{"start_sec": float, "end_sec": float, "confidence": float,
#               "reason": str, "sponsor": str | null}]
#     Return [] if no ads are found.
#
#   # Override the LLM system prompt for topic extraction.
#   topic_extraction: |
#     Analyze the opening of this podcast transcript.
#     Return only a JSON object — no markdown, no preamble.
#     Schema: {"domain": str, "topic": str, "hosts": list[str], "notes": str}
```

Make the same addition to `config.example.yaml`.

#### Step 9 — Verify config still loads cleanly

```bash
uv run pytest tests/test_config_loader.py -v
```

Expected: **all PASS**.

#### Step 10 — Commit Task 3

```bash
git add config/config_loader.py pipeline/ad_detector.py pipeline/topic_extractor.py \
        config.yaml config.example.yaml tests/test_config_loader.py
git commit -m "feat: extract LLM system prompts to config via PromptsConfig"
```

---

## Task 4: API key validation at startup

**Constraint:** Only `pipeline/llm_client.py` imports litellm. `validate_api_keys()` lives there.

**Files:**
- Modify: `pipeline/llm_client.py`
- Modify: `main.py`
- Modify: `tests/test_llm_client.py`

---

### Step 1 — Write the failing tests

Add to `tests/test_llm_client.py`:

```python
def test_validate_api_keys_raises_when_env_var_missing(monkeypatch):
    from pathlib import Path
    from config.config_loader import load_config
    from pipeline.exceptions import ConfigError
    from pipeline.llm_client import validate_api_keys

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        validate_api_keys(cfg)


def test_validate_api_keys_raises_when_key_invalid(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch
    from config.config_loader import load_config
    from pipeline.exceptions import ConfigError
    from pipeline.llm_client import validate_api_keys

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.setenv("OPENAI_API_KEY", "bad-key")

    with patch("pipeline.llm_client.litellm.check_valid_key", return_value=False):
        with pytest.raises(ConfigError, match="Invalid API key"):
            validate_api_keys(cfg)


def test_validate_api_keys_passes_with_valid_key(monkeypatch):
    from pathlib import Path
    from unittest.mock import patch
    from config.config_loader import load_config
    from pipeline.llm_client import validate_api_keys

    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-valid")

    with patch("pipeline.llm_client.litellm.check_valid_key", return_value=True):
        validate_api_keys(cfg)  # must not raise


def test_validate_api_keys_deduplicates_providers(monkeypatch):
    """When transcription and interpretation use the same provider, only one probe fires."""
    from pathlib import Path
    from unittest.mock import patch
    from config.config_loader import load_config
    from pipeline.llm_client import validate_api_keys

    # test_config.yaml uses openai for both transcription and interpretation
    cfg = load_config(Path("tests/fixtures/test_config.yaml"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-valid")

    with patch("pipeline.llm_client.litellm.check_valid_key", return_value=True) as mock_check:
        validate_api_keys(cfg)

    assert mock_check.call_count == 1  # deduped: openai only probed once
```

### Step 2 — Run tests, confirm they fail

```bash
uv run pytest tests/test_llm_client.py::test_validate_api_keys_raises_when_env_var_missing \
             tests/test_llm_client.py::test_validate_api_keys_raises_when_key_invalid \
             tests/test_llm_client.py::test_validate_api_keys_passes_with_valid_key \
             tests/test_llm_client.py::test_validate_api_keys_deduplicates_providers -v
```

Expected: **FAIL** — `validate_api_keys` does not exist.

### Step 3 — Implement `validate_api_keys` in `pipeline/llm_client.py`

Add `import os` near the top of `pipeline/llm_client.py` if not present.

Add this module-level constant **after `litellm.drop_params = True`**:

```python
_PROVIDER_ENV_VAR: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
```

Add this function **after `fits_in_context()`**:

```python
def validate_api_keys(cfg: "AppConfig") -> None:  # noqa: F821
    """Probe each configured provider's API key. Raises ConfigError on missing or invalid keys.

    Deduplicates by provider — a provider used for both transcription and interpretation
    is probed only once.
    """
    from config.config_loader import AppConfig  # local import avoids circular dependency
    from pipeline.exceptions import ConfigError

    seen: set[str] = set()
    for sub_cfg in (cfg.transcription, cfg.interpretation):
        if sub_cfg.provider in seen:
            continue
        seen.add(sub_cfg.provider)

        env_var = _PROVIDER_ENV_VAR.get(sub_cfg.provider, f"{sub_cfg.provider.upper()}_API_KEY")
        key = os.environ.get(env_var, "")
        if not key:
            raise ConfigError(
                f"Missing {env_var} environment variable (required for {sub_cfg.provider_model})"
            )
        if not litellm.check_valid_key(model=sub_cfg.provider_model, api_key=key):
            raise ConfigError(
                f"Invalid API key for {sub_cfg.provider_model} (check {env_var})"
            )

    logger.info("API key validation passed")
```

**Note on the local import:** `config_loader.py` imports from `pipeline.exceptions`, and `llm_client.py` imports from `config_loader`. Using a local import inside the function body breaks the potential circular import at the top-level.

Actually, `llm_client.py` already imports `InterpretationConfig` and `TranscriptionConfig` from `config.config_loader` at the top. `AppConfig` can be added to that import without issue since `config_loader.py` only imports from `pipeline.exceptions` (not from `llm_client`). Remove the local import for `AppConfig` and add it to the top-level import:

Change the existing top-level import in `llm_client.py` from:
```python
from config.config_loader import InterpretationConfig, TranscriptionConfig
```
to:
```python
from config.config_loader import AppConfig, InterpretationConfig, TranscriptionConfig
```

Then the function signature becomes:
```python
def validate_api_keys(cfg: AppConfig) -> None:
```

And remove the local import for `AppConfig` inside the function body.

### Step 4 — Run tests, confirm they pass

```bash
uv run pytest tests/test_llm_client.py -v
```

Expected: **all PASS**.

### Step 5 — Wire `validate_api_keys` into `main.py`

Update the import at the top of `main.py`:

```python
from config.config_loader import load_config
```
→
```python
from config.config_loader import load_config
from pipeline.llm_client import validate_api_keys
```

In `async def main()`, add the validation block **after `setup_logging()`**, before the `run_pipeline()` call:

```python
    try:
        validate_api_keys(cfg)
    except Exception as exc:
        logging.error(f"API key validation failed: {exc}")
        sys.exit(1)
```

### Step 6 — Run full test suite

```bash
uv run pytest -v
```

Expected: **all PASS**.

### Step 7 — Commit

```bash
git add pipeline/llm_client.py main.py tests/test_llm_client.py
git commit -m "feat: validate API keys at startup using litellm.check_valid_key"
```

---

## Final Verification

```bash
uv run pytest -v && uv run ruff check . && uv run mypy .
```

Expected: all green. If mypy reports issues with `litellm.check_valid_key` types, add `# type: ignore[no-untyped-call]` on that line only.
