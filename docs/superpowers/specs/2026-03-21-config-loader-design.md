# Config Loader Design

**Date:** 2026-03-21
**Status:** In Review

## Overview

Implement structured configuration loading for the podcast-ad-cutter project. Files changed or created:

- `utils/__init__.py` — empty, makes `utils` a package (new)
- `utils/exceptions.py` — shared custom exception hierarchy (new)
- `config/__init__.py` — empty, makes `config` a package (new)
- `config/config_loader.py` — pydantic-based YAML config validation + env credential loading (new)
- `tests/__init__.py` — empty, makes `tests` a package (new)
- `tests/test_config_loader.py` — config loader tests (new)
- `tests/test_exceptions.py` — exception hierarchy tests (new)
- `config.example.yaml` — rename `logging:` key to `log:` (modified)
- `main.py` — add missing imports, annotate `cfg`, remove dead `yaml.YAMLError` except arm (modified)
- `pyproject.toml` — add runtime deps and test ruff overrides (modified)

---

## `pyproject.toml` Changes

Two additions to the existing file:

**1. Add `dependencies` to the existing `[project]` table** (do not duplicate `name`, `dynamic`, `requires-python`):

```toml
dependencies = [
    "pydantic>=2",
    "pydantic-settings>=2",
    "python-dotenv>=1",  # explicit: load_dotenv() called directly; pydantic-settings also pulls it in transitively
    "pyyaml>=6",
]
```

**2. Add a new `[tool.ruff.lint.per-file-ignores]` section** to suppress rules that are incompatible with pytest-style test files:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = ["S101", "ANN", "D103", "INP001"]
```

- `S101` — `assert` statements are standard pytest style
- `ANN` — pytest fixture parameters do not need type annotations
- `D103` — test functions do not require docstrings
- `INP001` — `tests/` is a package via `tests/__init__.py`, but suppressed here as a safety net

---

## `config.example.yaml` Change (required deliverable)

Rename the top-level `logging:` key to `log:` to match the `AppConfig` field name:

```yaml
# Before
logging:
  level: "ERROR"
  to_file: false

# After
log:
  level: "ERROR"
  to_file: false
```

---

## `main.py` Changes (required deliverable)

Add missing imports and annotate `cfg`. Final import block:

```python
from pathlib import Path
from config.config_loader import Config, load_config
from utils.exceptions import ConfigError
```

In `main()`, annotate the config assignment and update the `except` clause:

```python
cfg: Config = load_config(args.config)
```

```python
# Change from:
except (ConfigError, yaml.YAMLError) as exc:
# To:
except ConfigError as exc:
```

`yaml.YAMLError` is always wrapped by `load_config` before it escapes — the second arm was dead code. `import yaml` is dropped entirely.

Note: `Path` is already used in `parse_args()` — the import is simply missing. `Config` must be imported explicitly for the `cfg: Config` annotation to satisfy `mypy --strict`.

---

## Exception Hierarchy (`utils/exceptions.py`)

```
PodcastAdCutterError(Exception)
    ConfigError
```

- `PodcastAdCutterError` — base for all project-level exceptions.
- `ConfigError` — raised for any config loading or validation failure: missing file, malformed YAML, schema validation errors, missing API keys.

---

## Pydantic Models (`config/config_loader.py`)

All new files use `from __future__ import annotations` at the top. This satisfies ruff `TCH001`/`TCH002` rules. `Literal` types inside `BaseModel` subclasses work correctly with deferred annotations in pydantic v2 — no `model_rebuild()` calls are needed (there are no forward references).

### Key imports

```python
from __future__ import annotations

from dotenv import load_dotenv               # from dotenv, not dotenv.main — enables patch("config.config_loader.load_dotenv")
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict  # NOT from pydantic — different module
```

### YAML models (`BaseModel`)

| Model | Fields | Constraints |
|---|---|---|
| `FeedConfig` | `title: str`, `url: str`, `enabled: bool`, `episodes_to_keep: int` | `episodes_to_keep = Field(ge=1)` |
| `LLMConfig` | `provider: Literal["groq", "openai", "openrouter"]`, `model: str` | — |
| `ModelsConfig` | `transcription: LLMConfig`, `context_extraction: LLMConfig`, `ad_detection: LLMConfig` | — |
| `PathsConfig` | `output_dir: Path`, `cache_dir: Path`, `data_dir: Path`, `log_dir: Path` | Pydantic v2 coerces strings to `Path` automatically. Paths are stored as-is (not resolved at load time). |
| `AdDetectionConfig` | `min_duration: int`, `min_confidence: float` | `min_duration = Field(gt=0, description="Minimum ad duration in milliseconds")`, `min_confidence = Field(ge=0.0, le=1.0)` |
| `OutputConfig` | `file_type: str`, `bitrate: str` | — |
| `LoggingConfig` | `level: Literal["DEBUG","INFO","WARNING","ERROR","CRITICAL"]`, `to_file: bool` | — |
| `AppConfig` | `feeds: list[FeedConfig]`, `models: ModelsConfig`, `paths: PathsConfig`, `ad_detection: AdDetectionConfig`, `output: OutputConfig`, `log: LoggingConfig`, `base_url: str` | `feeds = Field(min_length=1)` — pydantic v2 list constraint; do not use the deprecated v1 `min_items` |

> Notes:
> - `LLMConfig` avoids the reserved pydantic v2 name `ModelConfig`.
> - `AppConfig` uses `log` (not `logging`) to avoid ruff `A003` — `logging` is a stdlib module name. The YAML key is also `log:` (see `config.example.yaml` change above).

### Credentials model (`BaseSettings`)

`Credentials` reads from env. `case_sensitive=False` is required on Linux where env vars are case-sensitive, ensuring `groq_api_key` matches `GROQ_API_KEY`:

```python
class Credentials(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    groq_api_key: str | None = None        # reads GROQ_API_KEY
    openai_api_key: str | None = None      # reads OPENAI_API_KEY
    openrouter_api_key: str | None = None  # reads OPENROUTER_API_KEY
```

Empty-string values (e.g. `GROQ_API_KEY=`) are treated as missing via falsy check in step 6.

### Combined container

`Config` is a plain `BaseModel` (not `BaseSettings`). Env vars are read once during `Credentials()` construction and frozen into `Config`. `Config` itself has no env-reading behaviour — intentional:

```python
class Config(BaseModel):
    app: AppConfig
    credentials: Credentials
```

---

## Provider → Key Mapping

Module-level constant:

```python
PROVIDER_KEY_MAP: dict[str, str] = {
    "groq": "groq_api_key",
    "openai": "openai_api_key",
    "openrouter": "openrouter_api_key",
}
```

---

## Loading Logic (`load_config`)

```
load_config(config_path: Path) -> Config
```

1. Call `load_dotenv()` — searches from CWD upward; acceptable for a CLI tool run from the project root.
2. Read YAML file at `config_path` — wrap `FileNotFoundError` and `yaml.YAMLError` in `ConfigError`.
3. Instantiate `AppConfig` from parsed dict — wrap `pydantic.ValidationError` in `ConfigError`, including validation detail in the message.
4. Collect unique providers used across `app.models.transcription`, `app.models.context_extraction`, `app.models.ad_detection`.
5. Instantiate `Credentials()` — pydantic-settings reads from env automatically.
6. For each required provider, look up its field name in `PROVIDER_KEY_MAP`, check `not getattr(credentials, field_name)` (catches both `None` and empty string). Collect all missing keys and raise a single `ConfigError` listing them.
7. Return `Config(app=app_config, credentials=credentials)`.

---

## Tests

All tests use `pytest`. Env vars are injected via `monkeypatch.setenv`. YAML files are written to `tmp_path` fixtures. `load_dotenv` is patched via `unittest.mock.patch("config.config_loader.load_dotenv")` — this works because `config_loader.py` imports it as `from dotenv import load_dotenv`, binding the name in that module's namespace.

### `tests/test_config_loader.py`

| Test | Scenario | Fixture notes |
|---|---|---|
| `test_load_valid_config` | Valid YAML + required env vars → returns `Config` with correct values | Full valid YAML in `tmp_path`; all three provider keys set via `monkeypatch.setenv` |
| `test_load_dotenv_called` | `load_dotenv` is called during `load_config` | Patch `config.config_loader.load_dotenv`; pass non-existent path; catch `ConfigError`; assert mock was called |
| `test_missing_config_file` | Non-existent path → raises `ConfigError` | — |
| `test_invalid_yaml` | Malformed YAML content → raises `ConfigError` | Write `"key: [unclosed"` to `tmp_path` |
| `test_schema_validation_error` | Valid YAML but `min_confidence: 2.0` → raises `ConfigError` | — |
| `test_empty_feeds_list` | `feeds: []` → raises `ConfigError` | — |
| `test_missing_required_api_key` | Provider configured, env var absent → raises `ConfigError` naming the key | `monkeypatch.delenv("GROQ_API_KEY", raising=False)` |
| `test_empty_string_api_key` | Provider configured, `GROQ_API_KEY=""` → raises `ConfigError` | `monkeypatch.setenv("GROQ_API_KEY", "")` |
| `test_unreferenced_keys_not_required` | All three model providers set to `groq`; `OPENAI_API_KEY` and `OPENROUTER_API_KEY` absent → no error | `monkeypatch.setenv("GROQ_API_KEY", "test-key")`, `monkeypatch.delenv("OPENAI_API_KEY", raising=False)`, `monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)` |
| `test_path_coercion` | String paths in YAML (`"./output"`) → `PathsConfig` fields are `Path` objects | `assert isinstance(cfg.app.paths.output_dir, Path)` |

### `tests/test_exceptions.py`

| Test | Scenario |
|---|---|
| `test_config_error_is_podcast_error` | `issubclass(ConfigError, PodcastAdCutterError)` is `True` |

---

## Error Handling

All errors surface as `ConfigError`. `main.py` catches `ConfigError` and writes to stderr before exiting with code 1.

---

## Constraints

- Python 3.12, `from __future__ import annotations` in all new files
- `ruff select = ["ALL"]` — all rules must pass (test files have per-file-ignores as above)
- `mypy --strict` with `pydantic.mypy` plugin
- Tests written before implementation (TDD per AGENTS.md)
- Consult pydantic and pydantic-settings docs via Context7 before implementing
