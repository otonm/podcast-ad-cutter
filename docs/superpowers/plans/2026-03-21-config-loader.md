# Config Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `utils/exceptions.py` and `config/config_loader.py` with pydantic validation, env-based credential loading, and full test coverage.

**Architecture:** Two-layer config system: `AppConfig` (pydantic `BaseModel`) validates YAML structure; `Credentials` (pydantic-settings `BaseSettings`) reads API keys from env. `load_config()` orchestrates both, wrapping all errors in `ConfigError`. All modules use `from __future__ import annotations`.

**Tech Stack:** Python 3.12, pydantic v2, pydantic-settings v2, python-dotenv, pyyaml, pytest, ruff, mypy

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add runtime deps, ruff per-file-ignores, mypy test override |
| `config.example.yaml` | Modify | Rename `logging:` → `log:` |
| `utils/__init__.py` | Create (empty) | Makes `utils` a package |
| `utils/exceptions.py` | Create | `PodcastAdCutterError`, `ConfigError` |
| `config/__init__.py` | Create (empty) | Makes `config` a package |
| `config/config_loader.py` | Create | All pydantic models + `load_config` |
| `tests/__init__.py` | Create (empty) | Makes `tests` a package |
| `tests/test_exceptions.py` | Create | Exception hierarchy tests |
| `tests/test_config_loader.py` | Create | Config loader tests |
| `main.py` | Modify | Add missing imports, annotate `cfg`, fix `except` |

---

### Task 1: Scaffolding

**Files:** `pyproject.toml`, `config.example.yaml`, `utils/__init__.py`, `config/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Add runtime dependencies via uv**

```bash
uv add "pydantic>=2" "pydantic-settings>=2" "python-dotenv>=1" "pyyaml>=6"
```

Expected: `pyproject.toml` gains `dependencies = [...]` under `[project]`; `uv.lock` is updated; packages installed in `.venv`.

- [ ] **Step 2: Add ruff per-file-ignores and mypy test override to `pyproject.toml`**

Add these two new sections (after `[tool.ruff.lint]`):

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ANN", "D103", "INP001"]
```

And after `[tool.mypy]`:

```toml
[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
```

Rationale:
- `S101` — `assert` is standard pytest style
- `ANN` — pytest fixtures don't need type annotations
- `D103` — test functions don't need docstrings
- `INP001` — safety net (tests/ has `__init__.py` but this avoids surprises)
- `disallow_untyped_defs = false` — test functions don't need mypy-strict annotations

- [ ] **Step 3: Rename `logging:` key in `config.example.yaml`**

Change:

```yaml
logging:
  level: "ERROR"
  to_file: false
```

To:

```yaml
log:
  level: "ERROR"
  to_file: false
```

- [ ] **Step 4: Create empty package init files**

Create three empty files: `utils/__init__.py`, `config/__init__.py`, `tests/__init__.py`.

- [ ] **Step 5: Verify tooling is clean**

```bash
uv run ruff check .
uv run pytest
```

Expected: ruff passes (no Python source files to lint yet besides `main.py`). pytest reports no tests collected.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml config.example.yaml utils/__init__.py config/__init__.py tests/__init__.py uv.lock
git commit -m "chore: scaffold packages, add runtime deps, configure ruff/mypy for tests"
```

---

### Task 2: Exception Hierarchy (TDD)

**Files:** `tests/test_exceptions.py`, `utils/exceptions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_exceptions.py`:

```python
"""Tests for custom exception hierarchy."""

from __future__ import annotations

from utils.exceptions import ConfigError, PodcastAdCutterError


def test_config_error_is_podcast_error():
    assert issubclass(ConfigError, PodcastAdCutterError)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_exceptions.py -v
```

Expected: `ImportError` — `utils.exceptions` does not exist yet.

- [ ] **Step 3: Write the implementation**

Create `utils/exceptions.py`:

```python
"""Custom exceptions for podcast-ad-cutter."""

from __future__ import annotations


class PodcastAdCutterError(Exception):
    """Base exception for all podcast-ad-cutter errors."""


class ConfigError(PodcastAdCutterError):
    """Raised when configuration loading or validation fails."""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_exceptions.py -v
```

Expected: `PASSED`.

- [ ] **Step 5: Run ruff and mypy**

```bash
uv run ruff check utils/
uv run mypy utils/
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add tests/test_exceptions.py utils/exceptions.py
git commit -m "feat: add custom exception hierarchy"
```

---

### Task 3: Config Loader — Failing Tests

**Files:** `tests/test_config_loader.py`

Write all tests before touching `config/config_loader.py`.

- [ ] **Step 1: Look up pydantic and pydantic-settings docs via Context7**

Use the `mcp__context7` tool to:
1. Resolve the pydantic library ID, then query for: `BaseModel model_validate Field min_length`
2. Resolve the pydantic-settings library ID, then query for: `BaseSettings SettingsConfigDict case_sensitive env`

Read the returned docs before writing the implementation (Task 4).

- [ ] **Step 2: Write all tests**

Create `tests/test_config_loader.py`:

```python
"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from config.config_loader import Config, load_config
from utils.exceptions import ConfigError

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

VALID_YAML: str = """\
feeds:
  - title: "Test Podcast"
    url: "https://example.com/feed.rss"
    enabled: true
    episodes_to_keep: 10
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
paths:
  output_dir: "./output"
  cache_dir: "./cache"
  data_dir: "./data"
  log_dir: "./logs"
ad_detection:
  min_duration: 10000
  min_confidence: 0.7
output:
  file_type: "mp3"
  bitrate: "128k"
log:
  level: "ERROR"
  to_file: false
base_url: "http://localhost:8080"
"""

# YAML with feeds: [] to test empty-list validation
EMPTY_FEEDS_YAML: str = """\
feeds: []
models:
  transcription:
    provider: "groq"
    model: "whisper-large-v3"
  context_extraction:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
  ad_detection:
    provider: "groq"
    model: "llama-3.3-70b-versatile"
paths:
  output_dir: "./output"
  cache_dir: "./cache"
  data_dir: "./data"
  log_dir: "./logs"
ad_detection:
  min_duration: 10000
  min_confidence: 0.7
output:
  file_type: "mp3"
  bitrate: "128k"
log:
  level: "ERROR"
  to_file: false
base_url: "http://localhost:8080"
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Write VALID_YAML to a temp file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(VALID_YAML)
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_valid_config(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert isinstance(cfg, Config)
    assert cfg.app.base_url == "http://localhost:8080"
    assert cfg.credentials.groq_api_key == "test-groq-key"


def test_load_dotenv_called(tmp_path):
    # load_dotenv must be called before YAML reading — assert it is called
    # even when the config path does not exist (ConfigError raised after the call)
    with patch("config.config_loader.load_dotenv") as mock_dotenv:
        with pytest.raises(ConfigError):
            load_config(tmp_path / "nonexistent.yaml")
    mock_dotenv.assert_called_once()


def test_missing_config_file(tmp_path):
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")


def test_invalid_yaml(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("key: [unclosed")
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError):
            load_config(bad)


def test_schema_validation_error(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(VALID_YAML.replace("min_confidence: 0.7", "min_confidence: 2.0"))
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError):
            load_config(bad)


def test_empty_feeds_list(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(EMPTY_FEEDS_YAML)
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError):
            load_config(bad)


def test_missing_required_api_key(config_file, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="groq_api_key"):
            load_config(config_file)


def test_empty_string_api_key(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    with patch("config.config_loader.load_dotenv"):
        with pytest.raises(ConfigError, match="groq_api_key"):
            load_config(config_file)


def test_unreferenced_keys_not_required(config_file, monkeypatch):
    # All providers are "groq" in VALID_YAML — only GROQ_API_KEY is required
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert cfg.credentials.groq_api_key == "test-key"


def test_path_coercion(config_file, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with patch("config.config_loader.load_dotenv"):
        cfg = load_config(config_file)
    assert isinstance(cfg.app.paths.output_dir, Path)
```

- [ ] **Step 3: Run tests to confirm they all fail**

```bash
uv run pytest tests/test_config_loader.py -v
```

Expected: `ImportError` — `config.config_loader` does not exist yet.

---

### Task 4: Config Loader — Implementation

**Files:** `config/config_loader.py`

- [ ] **Step 1: Write the implementation**

Create `config/config_loader.py`:

```python
"""Configuration loader for podcast-ad-cutter."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils.exceptions import ConfigError

logger = logging.getLogger(__name__)

# Maps provider name to the Credentials field that holds its API key
PROVIDER_KEY_MAP: dict[str, str] = {
    "groq": "groq_api_key",
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
}


class FeedConfig(BaseModel):
    """Configuration for a single podcast feed."""

    title: str
    url: str
    enabled: bool
    episodes_to_keep: int = Field(ge=1)


class LLMConfig(BaseModel):
    """Configuration for a single LLM provider and model."""

    provider: Literal["groq", "openai", "openrouter"]
    model: str


class ModelsConfig(BaseModel):
    """LLM model assignments for each pipeline stage."""

    transcription: LLMConfig
    context_extraction: LLMConfig
    ad_detection: LLMConfig


class PathsConfig(BaseModel):
    """Directory paths for outputs and caches."""

    output_dir: Path
    cache_dir: Path
    data_dir: Path
    log_dir: Path


class AdDetectionConfig(BaseModel):
    """Settings for the ad detection algorithm."""

    min_duration: int = Field(gt=0, description="Minimum ad duration in milliseconds")
    min_confidence: float = Field(ge=0.0, le=1.0)


class OutputConfig(BaseModel):
    """Settings for the processed audio output files."""

    file_type: str
    bitrate: str


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    to_file: bool


class AppConfig(BaseModel):
    """Top-level application configuration loaded from YAML."""

    feeds: list[FeedConfig] = Field(min_length=1)
    models: ModelsConfig
    paths: PathsConfig
    ad_detection: AdDetectionConfig
    output: OutputConfig
    log: LoggingConfig
    base_url: str


class Credentials(BaseSettings):
    """API credentials loaded from environment variables."""

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    groq_api_key: str | None = None        # reads GROQ_API_KEY
    openai_api_key: str | None = None      # reads OPENAI_API_KEY
    openrouter_api_key: str | None = None  # reads OPENROUTER_API_KEY


class Config(BaseModel):
    """Combined application config and credentials container.

    Env vars are read once during Credentials() construction and frozen here.
    This class itself has no env-reading behaviour.
    """

    app: AppConfig
    credentials: Credentials


def load_config(config_path: Path) -> Config:
    """Load and validate configuration from a YAML file.

    Loads environment variables from .env, parses and validates the YAML
    config, then verifies that API keys exist for all configured providers.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Validated Config instance with app config and credentials.

    Raises:
        ConfigError: If the file is missing, YAML is malformed, schema
            validation fails, or required API keys are absent or empty.

    """
    load_dotenv()

    try:
        with config_path.open() as f:
            raw: Any = yaml.safe_load(f)
    except FileNotFoundError as exc:
        msg = f"Config file not found: {config_path}"
        raise ConfigError(msg) from exc
    except yaml.YAMLError as exc:
        msg = f"Failed to parse config file: {exc}"
        raise ConfigError(msg) from exc

    try:
        app_config = AppConfig.model_validate(raw)
    except ValidationError as exc:
        msg = f"Config validation failed:\n{exc}"
        raise ConfigError(msg) from exc

    # Collect unique providers used across all model pipeline stages
    models_cfg = app_config.models
    required_providers: set[str] = {
        models_cfg.transcription.provider,
        models_cfg.context_extraction.provider,
        models_cfg.ad_detection.provider,
    }

    credentials = Credentials()

    # Check that each required provider has a non-empty API key
    missing_keys = [
        PROVIDER_KEY_MAP[provider]
        for provider in sorted(required_providers)
        if not getattr(credentials, PROVIDER_KEY_MAP[provider])
    ]

    if missing_keys:
        keys_str = ", ".join(missing_keys)
        msg = f"Missing required API keys for configured providers: {keys_str}"
        raise ConfigError(msg)

    return Config(app=app_config, credentials=credentials)
```

- [ ] **Step 2: Run all tests**

```bash
uv run pytest tests/ -v
```

Expected: all 11 tests pass (`test_exceptions.py` + `test_config_loader.py`).

- [ ] **Step 3: Run ruff**

```bash
uv run ruff check .
```

Expected: no errors. If any fire, fix them before proceeding — do not suppress unless the rule is a known false positive.

- [ ] **Step 4: Run mypy**

```bash
uv run mypy config/ utils/
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add config/config_loader.py tests/test_config_loader.py
git commit -m "feat: implement pydantic config loader with credential validation"
```

---

### Task 5: Fix `main.py`

**Files:** `main.py`

- [ ] **Step 1: Add missing imports**

Add these three lines at the top of `main.py` (before other imports):

```python
from pathlib import Path
from config.config_loader import Config, load_config
from utils.exceptions import ConfigError
```

- [ ] **Step 2: Annotate `cfg` and fix the `except` clause**

In `main()`, change:

```python
cfg = load_config(args.config)
```

to:

```python
cfg: Config = load_config(args.config)
```

And change:

```python
except (ConfigError, yaml.YAMLError) as exc:
```

to:

```python
except ConfigError as exc:
```

Drop any remaining `import yaml` line — it is no longer used.

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest -v
```

Expected: all tests pass.

- [ ] **Step 4: Run ruff and mypy on all files**

```bash
uv run ruff check .
uv run mypy config/ utils/ main.py
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "fix: add missing imports and annotate cfg in main.py"
```
