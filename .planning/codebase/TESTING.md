# Testing

## Framework & Runner

- **pytest** (≥9.0.2) with **pytest-asyncio** (≥0.24)
- `asyncio_mode = "auto"` in `[tool.pytest.ini_options]` — all `async def` test functions run as coroutines automatically, no `@pytest.mark.asyncio` decorator needed
- `testpaths = ["tests"]`
- Run: `uv run pytest`

## Coverage

- **pytest-cov** (≥7.0.0)
- **Target: 100%** — enforced via CI-equivalent `uv run pytest --cov=.`
- Only omission: `example_cost_calculation.py` (excluded in `[tool.coverage.run]`)
- The 100% target is a hard requirement per CLAUDE.md

## Test File Organization

```
tests/
├── test_<component>.py       # One file per source module
├── test_<model_group>.py     # Pydantic model validation tests
├── test_pipeline.py          # Largest file — state machine integration tests
└── test_feed_parser_integration.py  # Real RSS XML parsing (controlled input)
```

Naming: `test_<module_name>.py` mirrors the source structure 1:1.

## Mocking Patterns

- **`unittest.mock`** — primary mocking library (`AsyncMock`, `MagicMock`, `patch`)
- `AsyncMock` for all async methods/functions
- `MagicMock` for sync objects (config, database connections)
- `patch` used as context manager (`with patch("module.path") as mock:`) or decorator
- **`aioresponses`** (≥0.7) for mocking `aiohttp` HTTP calls in downloader tests
- No `pytest-mock` — `unittest.mock` used directly throughout

## Async Testing

- `asyncio_mode = "auto"` means top-level `async def test_*()` functions work without decoration
- Async DB fixtures use `tmp_path` (pytest built-in) for isolated SQLite databases
- `aioresponses` context manager wraps HTTP calls in downloader/feed tests

## Shared Test Utilities & Fixtures

- **Factory functions** (not fixtures) for common test objects:
  - `make_feed()`, `make_config()`, `make_episode()` in `test_pipeline.py`
  - `_make_response()` in `test_ad_detector.py` for LLM response mocks
- Module-level constants for reusable test data: `_DEFAULT_AD_SEGMENT`, `_DEFAULT_CUT_RANGE`, `_SEGMENTS`, `_TOPIC`
- `tmp_path` pytest fixture used for all filesystem-dependent tests (DB files, audio cache dirs)
- `caplog` pytest fixture used for log assertion tests

## Test Types

| Type | Description | Examples |
|------|-------------|---------|
| Unit | Single class/function in isolation | `test_ad_detector.py`, `test_ad_parser.py`, `test_feed_parser.py` |
| Integration | Multiple real components with DB | `test_pipeline.py`, `test_*_store.py` |
| Model validation | Pydantic model field constraints | `test_ad_detection_models.py`, `test_transcription_models.py` |
| External process | ffmpeg subprocess mocking | `test_ffmpeg.py`, `test_audio_editor.py` |
| Controlled XML | Real RSS parser with trusted XML | `test_feed_parser_integration.py` |

## Linting in Tests

Test files have relaxed ruff rules (per `[tool.ruff.lint.per-file-ignores]`):
- `S101` — `assert` allowed
- `ANN` — type annotations not required
- `D*` — docstring style relaxed (D101/102/103/400/403/415)
- `PLR2004` — magic values in assertions are idiomatic
- `ARG001` — unused fixture args allowed (fixtures used for side-effects)
- `SLF001` — private method access allowed for white-box testing
