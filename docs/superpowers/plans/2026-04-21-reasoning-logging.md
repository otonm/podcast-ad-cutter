# Reasoning Token Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Log LLM reasoning tokens to per-episode log files unconditionally, without enabling LiteLLM's verbose debug output.

**Architecture:** Add a shared `extract_llm_reasoning()` utility that handles provider differences in field names; adjust `open_episode_log` to lower the root logger level to DEBUG while an episode is active so `logger.debug()` calls always reach the file handler; pin LiteLLM's own logger to WARNING to prevent it from flooding output even when root is at DEBUG.

**Tech Stack:** Python 3.12, `litellm`, standard `logging` module, `pytest`, `uv`.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `utils/llm.py` | Modify | Add `extract_llm_reasoning(response)` |
| `utils/episode_log.py` | Modify | Lower/restore root level around episode file handler |
| `main.py` | Modify | Pin LiteLLM loggers to WARNING in `configure_logging` |
| `components/ad_detector.py` | Modify | Use `extract_llm_reasoning`; remove `verbosity="high"` |
| `components/topic_extractor.py` | Modify | Add `_log_llm_reasoning`; use `extract_llm_reasoning`; remove `verbosity="high"` |
| `tests/test_llm.py` | Create | Tests for `extract_llm_reasoning` |
| `tests/test_episode_log.py` | Modify | Add root-level adjustment tests |
| `tests/test_main.py` | Modify | Add LiteLLM silencing test |
| `tests/test_ad_detector.py` | Modify | Add `reasoning` fallback test; update `_make_response` |
| `tests/test_topic_extractor.py` | Modify | Add reasoning logging tests; update `_make_response` |

---

## Task 1: `extract_llm_reasoning` in `utils/llm.py`

**Files:**
- Modify: `utils/llm.py`
- Create: `tests/test_llm.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_llm.py`:

```python
"""Tests for shared LiteLLM utility helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.llm import extract_llm_reasoning


def _make_response(**msg_attrs: object) -> SimpleNamespace:
    msg = SimpleNamespace(**msg_attrs)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def test_extract_reasoning_content_field() -> None:
    """Returns reasoning_content when present."""
    resp = _make_response(reasoning_content="thinking here")
    assert extract_llm_reasoning(resp) == "thinking here"


def test_extract_reasoning_field_fallback() -> None:
    """Falls back to reasoning when reasoning_content is absent."""
    resp = _make_response(reasoning="thinking here via reasoning field")
    assert extract_llm_reasoning(resp) == "thinking here via reasoning field"


def test_extract_returns_none_when_neither_field_present() -> None:
    """Returns None when neither reasoning_content nor reasoning is present."""
    resp = _make_response()
    assert extract_llm_reasoning(resp) is None


def test_extract_falls_through_empty_reasoning_content() -> None:
    """Empty string reasoning_content falls through to reasoning field."""
    resp = _make_response(reasoning_content="", reasoning="actual thinking")
    assert extract_llm_reasoning(resp) == "actual thinking"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_llm.py -v
```

Expected: `ImportError` or `AttributeError` — `extract_llm_reasoning` does not exist yet.

- [ ] **Step 3: Implement `extract_llm_reasoning` in `utils/llm.py`**

Add after the `compute_completion_cost` function (after line 46):

```python
def extract_llm_reasoning(response: object) -> str | None:
    """Extract reasoning/thinking text from a completion response.

    Tries reasoning_content (Anthropic, Deepseek) then reasoning (Alibaba/Qwen
    and other providers that do not normalise the field name).
    """
    msg = response.choices[0].message  # type: ignore[union-attr]
    return getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_llm.py -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Run full suite and linter**

```bash
uv run pytest --cov=. && uv run ruff
```

Expected: all tests pass, 100% coverage, no ruff errors.

- [ ] **Step 6: Commit**

```bash
git add utils/llm.py tests/test_llm.py
git commit -m "feat(llm): add extract_llm_reasoning utility with provider fallback"
```

---

## Task 2: Root level adjustment in `utils/episode_log.py`

**Files:**
- Modify: `utils/episode_log.py`
- Modify: `tests/test_episode_log.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_episode_log.py` inside `class TestOpenEpisodeLog` (after `test_handler_level_defaults_to_debug`):

```python
def test_open_lowers_root_level_when_above_file_handler_level(self, tmp_path: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    _, handler = open_episode_log(
        guid="ep-1",
        podcast_title="My Podcast",
        episode_title="My Episode",
        log_dir=tmp_path,
        file_level="DEBUG",
    )
    assert root.level == logging.DEBUG
    close_episode_log(handler)

def test_open_does_not_change_root_level_when_already_at_or_below_file_level(
    self, tmp_path: Path
) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    _, handler = open_episode_log(
        guid="ep-1",
        podcast_title="My Podcast",
        episode_title="My Episode",
        log_dir=tmp_path,
        file_level="DEBUG",
    )
    assert root.level == logging.DEBUG
    close_episode_log(handler)

def test_debug_message_reaches_file_when_root_was_at_warning(self, tmp_path: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    _, handler = open_episode_log(
        guid="ep-1",
        podcast_title="My Podcast",
        episode_title="My Episode",
        log_dir=tmp_path,
        file_level="DEBUG",
    )
    logging.getLogger("components.ad_detector").debug("reasoning text here")
    close_episode_log(handler)
    log_file = next((tmp_path / "episodes").glob("*.log"))
    assert "reasoning text here" in log_file.read_text()
```

Add to `class TestCloseEpisodeLog` (after `test_handler_closed_after_removal`):

```python
def test_close_restores_root_level(self, tmp_path: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    _, handler = open_episode_log(
        guid="ep-1",
        podcast_title="My Podcast",
        episode_title="My Episode",
        log_dir=tmp_path,
        file_level="DEBUG",
    )
    assert root.level == logging.DEBUG
    close_episode_log(handler)
    assert root.level == logging.WARNING
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_episode_log.py -v -k "lowers_root or does_not_change_root or reaches_file_when or restores_root"
```

Expected: 4 FAILED — root level is not adjusted yet.

- [ ] **Step 3: Implement root level adjustment in `utils/episode_log.py`**

Replace the body of `open_episode_log` from the `root = logging.getLogger()` line onward (currently just `logging.getLogger().addHandler(handler)`):

Current (line 57):
```python
    logging.getLogger().addHandler(handler)
    episode_logger = logging.getLogger(f"episode.{guid}")
    return episode_logger, handler
```

Replace with:
```python
    root = logging.getLogger()
    handler._pac_prior_root_level = root.level
    if root.level > handler_level_int:
        root.setLevel(handler_level_int)
    root.addHandler(handler)

    episode_logger = logging.getLogger(f"episode.{guid}")
    return episode_logger, handler
```

Also replace the line `handler = logging.FileHandler(log_path, encoding="utf-8")` block — you need `handler_level_int` available before the `if` check. The full updated section from line 53 onward:

```python
    handler_level_int = getattr(logging, file_level)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(handler_level_int)
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    handler._pac_prior_root_level = root.level
    if root.level > handler_level_int:
        root.setLevel(handler_level_int)
    root.addHandler(handler)

    episode_logger = logging.getLogger(f"episode.{guid}")
    return episode_logger, handler
```

Replace `close_episode_log` body:

```python
def close_episode_log(handler: logging.FileHandler) -> None:
    """Remove *handler* from the root logger and close the underlying file.

    Args:
        handler: The :class:`logging.FileHandler` previously returned by
            :func:`open_episode_log`.

    """
    root = logging.getLogger()
    root.removeHandler(handler)
    prior = getattr(handler, "_pac_prior_root_level", None)
    if prior is not None:
        root.setLevel(prior)
    handler.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_episode_log.py -v
```

Expected: all tests pass (old + 4 new).

- [ ] **Step 5: Run full suite and linter**

```bash
uv run pytest --cov=. && uv run ruff
```

Expected: all tests pass, 100% coverage, no ruff errors.

- [ ] **Step 6: Commit**

```bash
git add utils/episode_log.py tests/test_episode_log.py
git commit -m "feat(episode-log): lower root logger level for duration of episode processing"
```

---

## Task 3: Silence LiteLLM loggers in `main.py`

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main.py`

- [ ] **Step 1: Write failing test**

Find the `configure_logging` test class in `tests/test_main.py` and add after the last configure_logging test:

```python
def test_configure_logging_silences_litellm_loggers(self, tmp_path: Path) -> None:
    configure_logging(level="DEBUG", log_to_file=False, log_dir=tmp_path)
    assert logging.getLogger("LiteLLM").level == logging.WARNING
    assert logging.getLogger("LiteLLM Router").level == logging.WARNING
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_main.py -v -k "silences_litellm"
```

Expected: FAILED — LiteLLM loggers are not explicitly silenced.

- [ ] **Step 3: Update `configure_logging` in `main.py`**

Replace lines 116–118 (the aiosqlite + commented LiteLLM block):

Current:
```python
    # Some libraries are extremely chatty at DEBUG — keep them at WARNING
    # regardless of the application log level so they don't drown out our own messages.
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    #logging.getLogger("LiteLLM").setLevel(logging.WARNING)
```

Replace with:
```python
    # Some libraries are extremely chatty at DEBUG — keep them at WARNING
    # regardless of the application log level so they don't drown out our own messages.
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("LiteLLM Router").setLevel(logging.WARNING)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_main.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run full suite and linter**

```bash
uv run pytest --cov=. && uv run ruff
```

Expected: all tests pass, 100% coverage, no ruff errors.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "fix(logging): silence LiteLLM loggers at WARNING regardless of root level"
```

---

## Task 4: Fix `ad_detector.py`

**Files:**
- Modify: `components/ad_detector.py`
- Modify: `tests/test_ad_detector.py`

- [ ] **Step 1: Update `_make_response` and write failing test**

`_make_response` in `tests/test_ad_detector.py` (lines 42–55) does not set `msg.reasoning` explicitly. After switching to `extract_llm_reasoning`, which falls back to `getattr(msg, "reasoning", None)`, the auto-generated MagicMock attribute for `reasoning` is truthy and breaks `test_log_llm_reasoning_silent_when_absent`. Fix the helper to explicitly set both fields:

Replace the existing `_make_response` function (lines 42–55):

```python
def _make_response(
    content: str = _VALID_DETECTIONS,
    response_cost: float | None = 0.002,
    reasoning_content: str | None = None,
    reasoning: str | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.reasoning_content = reasoning_content
    msg.reasoning = reasoning
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp._hidden_params = {"response_cost": response_cost}
    return resp
```

Then add the fallback test after `test_log_llm_reasoning_silent_when_absent` (around line 585):

```python
async def test_log_llm_reasoning_falls_back_to_reasoning_field(
    detector: AdDetector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When reasoning_content is None but reasoning is set, reasoning is still logged."""
    mock_resp = _make_response(
        content=_INBOUNDS_DETECTIONS,
        reasoning_content=None,
        reasoning="Segment 1 has a promo code.",
    )
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.ad_detector"),
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert any(
        "Segment 1 has a promo code." in r.message
        for r in caplog.records
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_ad_detector.py::test_log_llm_reasoning_falls_back_to_reasoning_field -v
```

Expected: FAILED — `_log_llm_reasoning` only checks `reasoning_content`.

- [ ] **Step 3: Update `ad_detector.py`**

Add `extract_llm_reasoning` to the imports at the top (line 13):

```python
from utils.llm import compute_completion_cost, extract_llm_reasoning
```

Replace `_log_llm_reasoning` body (lines 233–236):

```python
    def _log_llm_reasoning(self, response: litellm.ModelResponse, guid: str) -> None:
        reasoning = extract_llm_reasoning(response)
        if reasoning:
            logger.debug(f"LLM reasoning for '{guid}':\n{reasoning}")
```

Remove `verbosity="high",` from `_call_llm` (line 283 — it is the only `verbosity` line in that method).

- [ ] **Step 4: Run all ad_detector tests**

```bash
uv run pytest tests/test_ad_detector.py -v
```

Expected: all tests pass including the 3 reasoning tests.

- [ ] **Step 5: Run full suite and linter**

```bash
uv run pytest --cov=. && uv run ruff
```

Expected: all tests pass, 100% coverage, no ruff errors.

- [ ] **Step 6: Commit**

```bash
git add components/ad_detector.py tests/test_ad_detector.py
git commit -m "fix(ad-detector): extract reasoning via shared utility, handle reasoning field fallback"
```

---

## Task 5: Add reasoning logging to `topic_extractor.py`

**Files:**
- Modify: `components/topic_extractor.py`
- Modify: `tests/test_topic_extractor.py`

- [ ] **Step 1: Update `_make_response` and write failing tests in `test_topic_extractor.py`**

Replace the existing `_make_response` function (lines 24–35) with a version that accepts reasoning fields:

```python
def _make_response(
    content: str = _VALID_JSON,
    response_cost: float | None = 0.002,
    reasoning_content: str | None = None,
    reasoning: str | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.reasoning_content = reasoning_content
    if reasoning is not None:
        msg.reasoning = reasoning
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp._hidden_params = {"response_cost": response_cost}
    return resp
```

Add these tests after the existing tests (find the end of the file or a suitable section boundary):

```python
# ---------------------------------------------------------------------------
# LLM reasoning logging
# ---------------------------------------------------------------------------

_VALID_EXTRACTION = json.dumps({
    "topic": "Hosts discuss AI advances.",
    "hosts": "Alice, Bob",
    "show": "Tech Talk",
})


async def test_log_llm_reasoning_emits_debug_when_present(
    extractor: TopicExtractor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When reasoning_content is set, a DEBUG message containing the reasoning is logged."""
    mock_resp = _make_response(
        content=_VALID_EXTRACTION,
        reasoning_content="The topic is clearly about AI.",
    )
    with (
        patch("components.topic_extractor.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.topic_extractor"),
    ):
        await extractor.extract("ep-1", "Tech Talk", "AI Episode", "Tech Talk", _TRANSCRIPT)
    assert any(
        "The topic is clearly about AI." in r.message
        for r in caplog.records
    )


async def test_log_llm_reasoning_silent_when_absent(
    extractor: TopicExtractor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When reasoning_content is None, no reasoning DEBUG message is logged."""
    mock_resp = _make_response(content=_VALID_EXTRACTION, reasoning_content=None)
    with (
        patch("components.topic_extractor.litellm.acompletion", new=AsyncMock(return_value=mock_resp)),
        caplog.at_level(logging.DEBUG, logger="components.topic_extractor"),
    ):
        await extractor.extract("ep-1", "Tech Talk", "AI Episode", "Tech Talk", _TRANSCRIPT)
    assert not any("LLM reasoning" in r.message for r in caplog.records)
```

You will also need to add `import logging` to the imports in `tests/test_topic_extractor.py` if not already present (check line 1–10).

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_topic_extractor.py -v -k "log_llm_reasoning"
```

Expected: FAILED — `topic_extractor` has no `_log_llm_reasoning` method.

- [ ] **Step 3: Update `topic_extractor.py`**

Add `extract_llm_reasoning` to the existing import (line 12):

```python
from utils.llm import compute_completion_cost, extract_llm_reasoning
```

Add `_log_llm_reasoning` method to `TopicExtractor` after `_truncate_transcript` and before `_call_llm` (insert around line 156):

```python
    def _log_llm_reasoning(self, response: litellm.ModelResponse, guid: str) -> None:
        reasoning = extract_llm_reasoning(response)
        if reasoning:
            logger.debug(f"LLM reasoning for '{guid}':\n{reasoning}")
```

In the `extract` method, replace line 259 (`logger.debug(f"LLM response: {response}")`) with the reasoning call:

```python
                self._log_llm_reasoning(response, guid)
```

Remove `verbosity="high",` from `_call_llm` (line 182 — the only `verbosity` line in that method).

- [ ] **Step 4: Run all topic_extractor tests**

```bash
uv run pytest tests/test_topic_extractor.py -v
```

Expected: all tests pass including the 2 new reasoning tests.

- [ ] **Step 5: Run full suite and linter**

```bash
uv run pytest --cov=. && uv run ruff
```

Expected: all tests pass, 100% coverage, no ruff errors.

- [ ] **Step 6: Commit**

```bash
git add components/topic_extractor.py tests/test_topic_extractor.py
git commit -m "feat(topic-extractor): add LLM reasoning logging, remove no-op verbosity param"
```
