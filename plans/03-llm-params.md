# 03 — Per-Model LLM Params Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each pipeline stage's model accepts user-defined LiteLLM parameters (`temperature`, `max_tokens`, `reasoning_effort`, …) from `config.yaml`, and the GUI can set them through `PATCH /settings`.

**Architecture:**
- `LLMConfig` gains `params: dict[str, Any]`.
- `AdDetector`, `TopicExtractor` and `EpisodeTranscriptor` take `params` and merge them over their built-in defaults.
- Existing `acompletion_with_param_fallback` already drops params a model rejects, so arbitrary keys are safe for the two completion stages.
- `PATCH /settings` switches to JSON Merge Patch (`null` deletes a key), so params can be removed.

**Tech Stack:** pydantic v2, litellm.

**Spec:** [plans/README.md](README.md) — Decisions → Behaviour → G6.

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan). Additionally:
- Reserved keys that `params` may never set: `model`, `messages`, `api_key`, `response_format`, `file`, `drop_params`.
- Current defaults that user params override:
  - ad detection: `temperature=0.3`, `reasoning_effort="high"|None`;
  - topic extraction: `temperature=0.5`, `reasoning_effort={"effort": "high", "summary": "auto"}|None`, `thinking={"type": "enabled", "budget_tokens": 10000}`.

## Review Focus

1. **`params` contains a reserved key:** expect a `ValidationError` at load time, and 422 on `PATCH /settings`. Tested in Tasks 1 and 3.
2. **The user sets `temperature` on a model that rejects it:** the existing fallback drops it and retries. Already covered by `tests/test_llm.py`; no new test.
3. **The user sets an invalid param for transcription:** `atranscription` has no fallback, so the episode fails with `TranscriptionError` and the message names the provider error. Document this in `config.example.yaml`; don't add a fallback (YAGNI until it bites).
4. **Removing one param via PATCH:** `{"params": {"top_p": null}}` removes only `top_p`. Tested in Task 3.
5. **A user-supplied `reasoning_effort`** overrides the retry loop's `use_reasoning=False` downgrade. Accepted: the user explicitly asked for it. Pinned by a test in Task 2 so the behaviour is deliberate.

---

### Task 1: `LLMConfig.params` with reserved-key validation

**Files:**
- Modify: `config/config_loader.py` (`LLMConfig`)
- Modify: `config.example.yaml` (commented example under `models.ad_detection`)
- Test: `tests/test_config_loader.py`

**Interfaces:**
- Produces:
  - `LLMConfig.params: dict[str, Any] = Field(default_factory=dict)`
  - `RESERVED_LLM_PARAMS: frozenset[str]` (module level, the values above)
  - The validator error message contains the word `reserved` and the sorted offending keys.

- [ ] **Step 1: Write the failing tests**

```python
def test_llm_config_params_default_empty():
    assert LLMConfig(provider="groq", model="m").params == {}

def test_llm_config_accepts_arbitrary_params():
    cfg = LLMConfig(provider="groq", model="m", params={"temperature": 0.1, "top_p": 0.9})
    assert cfg.params == {"temperature": 0.1, "top_p": 0.9}

def test_llm_config_rejects_reserved_params():
    with pytest.raises(ValidationError, match="reserved"):
        LLMConfig(provider="groq", model="m", params={"model": "x", "api_key": "k"})
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_config_loader.py -v -k params`

  Expected: FAIL (unexpected keyword / no `params` attribute).

- [ ] **Step 3: Implement the field, the constant, and a `@field_validator("params")`**

- [ ] **Step 4: Verify**

  Run: `uv run pytest tests/test_config_loader.py -v`

  Expected: PASS.

- [ ] **Step 5: Add a commented example to `config.example.yaml`**

```yaml
    # params:            # optional LiteLLM kwargs, override built-in defaults
    #   temperature: 0.2
    #   max_tokens: 4000
```

- [ ] **Step 6: Commit**

```bash
git add config/config_loader.py config.example.yaml tests/test_config_loader.py
git commit -m "feat(config): add per-model params to LLMConfig"
```

### Task 2: Components and Pipeline pass params to LiteLLM

**Files:**
- Modify: `components/ad_detector.py` (`__init__` around :168, call site around :283–295)
- Modify: `components/topic_extractor.py` (`__init__` around :79, call site around :182–195)
- Modify: `components/episode_transcriptor.py` (`__init__` :52, `_call_litellm` around :104)
- Modify: `components/pipeline.py` (constructor wiring around :128–147)
- Test: `tests/test_ad_detector.py`, `tests/test_topic_extractor.py`, `tests/test_episode_transcriptor.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `LLMConfig.params` (Task 1).
- Produces:
  - `AdDetector.__init__(..., context_window: int | None = None, params: dict[str, Any] | None = None)`
  - `TopicExtractor.__init__(..., context_window: int | None = None, params: dict[str, Any] | None = None)`
  - `EpisodeTranscriptor.__init__(provider: str, model: str, api_key: str, params: dict[str, Any] | None = None)`

- [ ] **Step 1: Write the failing tests**

  One per component. Patch `acompletion_with_param_fallback`, or `litellm.atranscription` for the transcriptor.

```python
async def test_ad_detector_user_params_override_defaults():
    det = AdDetector("groq", "m", "k", params={"temperature": 0.0, "max_tokens": 500})
    <run one detection with patched acompletion_with_param_fallback>
    kwargs = mock.call_args.kwargs
    assert kwargs["temperature"] == 0.0 and kwargs["max_tokens"] == 500
    assert kwargs["model"] == "groq/m"

async def test_ad_detector_defaults_without_params():
    ...; assert kwargs["temperature"] == 0.3

async def test_ad_detector_user_reasoning_effort_survives_reasoning_downgrade():
    det = AdDetector("groq", "m", "k", params={"reasoning_effort": "low"})
    <force a retry path where use_reasoning=False>
    assert all(c.kwargs["reasoning_effort"] == "low" for c in mock.call_args_list)

# same three for TopicExtractor (default temperature 0.5)

async def test_transcriptor_passes_params():
    tr = EpisodeTranscriptor("groq", "whisper", "k", params={"language": "de"})
    <transcribe small file with patched litellm.atranscription>
    assert mock.call_args.kwargs["language"] == "de"

def test_pipeline_passes_model_params_to_components():
    <config with models.ad_detection.params={"temperature": 0.1}>
    with patch("components.pipeline.AdDetector") as ad:
        Pipeline(cfg)
    assert ad.call_args.kwargs["params"] == {"temperature": 0.1}
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_ad_detector.py tests/test_topic_extractor.py tests/test_episode_transcriptor.py tests/test_pipeline.py -v -k params`

  Expected: FAIL (unexpected keyword `params`).

- [ ] **Step 3: Implement**

  - Store `self._params = params or {}`.
  - At each call site, build the kwargs as: built-in defaults first, then `**self._params`, then the fixed keys (`model`, `messages`, `response_format`, `api_key`, `drop_params`) last, so they can never be overridden even if validation is bypassed.
  - The transcriptor splats `**self._params` into `litellm.atranscription`.
  - The Pipeline passes `params=<stage cfg>.params` to each of the three constructors.

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean.

- [ ] **Step 5: Commit**

```bash
git add components/ tests/
git commit -m "feat(llm): pass per-model params from config to LiteLLM calls"
```

### Task 3: `PATCH /settings` uses JSON Merge Patch semantics

**Files:**
- Modify: `api/routes/settings.py` (`_deep_merge`)
- Modify: `API.md` §2.4 (`PATCH /settings`: document `null` deletes a key; show a `params` example)
- Test: `tests/test_api_settings.py`

**Interfaces:**
- Produces: `_deep_merge(base: dict, patch: dict) -> dict`. When a patch value is `None`, the key is removed from the result. Everything else behaves as today.

- [ ] **Step 1: Write the failing tests**

```python
def test_deep_merge_null_removes_key():
    assert _deep_merge({"a": 1, "b": 2}, {"b": None}) == {"a": 1}

def test_deep_merge_null_for_missing_key_is_noop():
    assert _deep_merge({"a": 1}, {"z": None}) == {"a": 1}

async def test_patch_removes_single_param(tmp_path, monkeypatch):
    <config with models.ad_detection.params {"temperature": 0.2, "top_p": 0.9}>
    resp = await client.patch("/api/v1/settings",
        json={"models": {"ad_detection": {"params": {"top_p": None}}}})
    assert (await resp.json())["models"]["ad_detection"]["params"] == {"temperature": 0.2}

async def test_patch_reserved_param_returns_422(tmp_path, monkeypatch):
    resp = await client.patch("/api/v1/settings",
        json={"models": {"ad_detection": {"params": {"model": "x"}}}})
    assert resp.status == 422
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_api_settings.py -v -k "null or param"`

  Expected: the null tests FAIL (the key is kept with value `None`).

- [ ] **Step 3: Implement**

  In `_deep_merge`, when `patch_val is None`, use `result.pop(key, None)` and continue.

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean.

- [ ] **Step 5: Commit**

```bash
git add api/routes/settings.py tests/test_api_settings.py API.md
git commit -m "feat(api): PATCH /settings treats null as delete (JSON Merge Patch)"
```
