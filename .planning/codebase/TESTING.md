# Testing Patterns

**Analysis Date:** 2026-05-14

## Test Framework

**Runner:**
- pytest (≥9.0.2) with pytest-asyncio (≥0.24)
- Config: `pyproject.toml` (`[tool.pytest.ini_options]`)

**Assertion Library:**
- pytest built-ins (`assert`, `pytest.raises`, `pytest.approx`)

**Mocking:**
- `unittest.mock` — `MagicMock`, `AsyncMock`, `patch`
- `aioresponses` (≥0.7) for mocking `aiohttp` HTTP calls

**Coverage:**
- `pytest-cov` (≥7.0.0)
- Target: 100% enforced by project policy (`uv run pytest --cov=.`)
- Omit list: `example_cost_calculation.py` (in `pyproject.toml` `[tool.coverage.run]`)

**Run Commands:**
```bash
uv run pytest                  # Run all tests
uv run pytest --cov=.          # Run with 100% coverage check
uv run ruff                    # Lint (must pass before commit)
```

## Test File Organization

**Location:** All tests live in `tests/` at the project root. No co-located test files.

**Naming:** `test_<module_name>.py` mirrors the source module:
- `components/ad_detector.py` → `tests/test_ad_detector.py`
- `database/connection.py` → `tests/test_database_connection.py`
- `utils/ffmpeg.py` → `tests/test_ffmpeg.py`

**Static fixtures:** `tests/static/` holds RSS snapshot files used for integration tests:
- `tests/static/example.rss` — The Daily (NYT)
- `tests/static/example2.rss` — Prof G Markets (Vox Media)

**Structure:**
```
tests/
├── __init__.py
├── static/
│   ├── example.rss
│   └── example2.rss
├── test_ad_detection_models.py
├── test_ad_detector.py        (827 lines — most complex)
├── test_ad_parser.py
├── test_ad_store.py
├── test_audio_editor.py
├── test_audio_metadata_store.py
├── test_audio_preprocessor.py
├── test_audio_prober.py
├── test_config_loader.py
├── test_cost_models.py
├── test_cost_tracking_store.py
├── test_database_connection.py
├── test_episode_copier.py
├── test_episode_downloader.py
├── test_episode_log.py
├── test_episode_store.py
├── test_episode_transcriptor.py
├── test_exceptions.py
├── test_feed_downloader.py
├── test_feed_parser.py
├── test_feed_parser_integration.py
├── test_feed_publisher.py
├── test_ffmpeg.py
├── test_llm.py
├── test_main.py
├── test_pipeline.py           (3171 lines — largest)
├── test_topic_extractor.py
├── test_topic_store.py
├── test_transcription_models.py
└── test_transcription_store.py
```

## Async Test Mode

`asyncio_mode = "auto"` in `pyproject.toml` means every `async def test_*` function runs as an async test automatically. No `@pytest.mark.asyncio` decorator needed.

## Test Structure

**Two structural styles are used:**

**Style 1 — Top-level async functions** (dominant pattern in component and store tests):
```python
async def test_detect_returns_result_tuple(detector: AdDetector) -> None:
    mock_resp = _make_response()
    with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
        guid, detections, cost = await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert guid == "ep-1"
    assert isinstance(detections, list)
    assert isinstance(cost, AdDetectionCost)
```

**Style 2 — Test classes** (used for logically grouped suites such as `test_main.py`, `test_episode_log.py`):
```python
class TestConfigureLogging:
    def test_info_level(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", log_to_file=False, log_dir=tmp_path)
        assert logging.getLogger().level == logging.INFO

    def test_stream_handler_always_present(self, tmp_path: Path) -> None:
        ...
```
Classes used: `TestConfigureLogging`, `TestRotateLogs`, `TestParseArgs`, `TestMain` (`test_main.py`); `TestOpenEpisodeLog`, `TestCloseEpisodeLog`, `TestRotateEpisodeLogs` (`test_episode_log.py`).

**Section headers** group related tests within large files:
```python
# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------
```

## Fixtures

**`tmp_path`** (built-in pytest) is used universally for database and file system tests:
```python
@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"
```

**Component fixtures** instantiate the subject under test:
```python
@pytest.fixture
def detector() -> AdDetector:
    return _make_detector()

@pytest.fixture
def downloader() -> FeedDownloader:
    return FeedDownloader()
```

**`autouse=True`** fixtures for cross-cutting setup/teardown:
```python
@pytest.fixture(autouse=True)
def _mock_supports_reasoning() -> Generator[None, None, None]:
    with patch("components.ad_detector.litellm.supports_reasoning", return_value=True):
        yield

@pytest.fixture(autouse=True)
def restore_root_logger() -> object:
    """Save and restore root logger state around each test."""
    root = logging.getLogger()
    original_level = root.level
    original_handlers = root.handlers[:]
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    yield
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)
    root.setLevel(original_level)
    for handler in original_handlers:
        root.addHandler(handler)
```

**`scope="module"` fixtures** for expensive shared state (e.g., parsing a full RSS file once):
```python
@pytest.fixture(scope="module")
def parsed_feed() -> ParsedFeed:
    return FeedParser().parse_all([_FULL_INPUT])[0]
```

## Mocking

**Framework:** `unittest.mock` — `patch`, `MagicMock`, `AsyncMock`

**Patch target is always the import location of the object being called, not where it is defined:**
```python
# Patches litellm.acompletion as imported in ad_detector, not in litellm itself
with patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)):
    ...
```

**Factory functions for complex mocks** (avoids repetition in large test files):
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

**`aioresponses`** for mocking `aiohttp` sessions:
```python
async def test_download_all_success(downloader: FeedDownloader) -> None:
    with aioresponses() as m:
        m.get(FEED_A[1], status=200, body=XML_A)
        m.get(FEED_B[1], status=200, body=XML_B)
        results = await downloader.download_all([FEED_A, FEED_B])
    assert results == [("Feed A", XML_A), ("Feed B", XML_B)]
```

**What to Mock:**
- External API calls (litellm `acompletion`, HTTP requests via aiohttp)
- Subprocess execution (ffmpeg, ffprobe)
- File system for non-`tmp_path` scenarios

**What NOT to Mock:**
- aiosqlite (real in-memory/temp DB used via `tmp_path`)
- Pure logic functions with no I/O

## Test Data

**Module-level constants** for shared inputs (avoids fixture overhead for immutable data):
```python
_SEGMENTS = [
    TranscriptionSegment(guid="ep-1", start_ms=0, end_ms=4500, text="Welcome to the show."),
    ...
]
_VALID_DETECTIONS = json.dumps({"ads": [...]})
_EMPTY_DETECTIONS = json.dumps({"ads": []})
```

**Private factory functions** for lightweight object construction:
```python
def _ep(guid: str) -> Episode:
    return Episode(guid=guid, url=f"https://example.com/{guid}.mp3", title=guid)

def _seg(guid: str, start_ms: int = 60000, end_ms: int = 90000) -> AdSegment:
    return AdSegment(guid=guid, start_ms=start_ms, end_ms=end_ms, confidence=0.95, ...)
```

**Static RSS fixtures** (`tests/static/*.rss`) loaded at module level for integration tests:
```python
_EXAMPLE_XML = (Path(__file__).parent / "static" / "example.rss").read_text(encoding="utf-8")
_FEED: ParsedFeed = FeedParser().parse_all([_FULL_INPUT])[0]
```

## Log Assertion Pattern

Use `caplog` with scoped logger and level to assert log output:
```python
async def test_detect_omits_reasoning_when_model_unsupported(caplog: pytest.LogCaptureFixture) -> None:
    with (
        patch("components.ad_detector.litellm.supports_reasoning", return_value=False),
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(return_value=mock_resp)) as mock_call,
        caplog.at_level(logging.WARNING, logger="components.ad_detector"),
    ):
        await _make_detector().detect("ep-1", _SEGMENTS, _TOPIC)
    assert any("does not support reasoning" in r.message for r in caplog.records)
```

Always scope `caplog.at_level` to the specific logger module to avoid noise from other loggers.

## Error Assertion Pattern

```python
async def test_acompletion_exception_raises_ad_detection_error(detector: AdDetector) -> None:
    with (
        patch("components.ad_detector.litellm.acompletion", new=AsyncMock(side_effect=RuntimeError("API down"))),
        pytest.raises(AdDetectionError) as exc_info,
    ):
        await detector.detect("ep-1", _SEGMENTS, _TOPIC)
    assert "ep-1" in exc_info.value.message
```

Always assert on `.message` attribute, not `str(exc)`, for domain errors.

## Float Assertion Pattern

Use `pytest.approx` for all float comparisons:
```python
assert cost.cost == pytest.approx(0.005)
assert detections[0].confidence == pytest.approx(0.95)
```

## Test Types

**Unit Tests:**
- Mock all I/O; test one component's logic in isolation
- Examples: `test_ad_detector.py`, `test_feed_downloader.py`, `test_topic_extractor.py`

**Integration Tests (real DB):**
- Use real aiosqlite on `tmp_path` to verify schema, constraints, and store logic
- Examples: `test_ad_store.py`, `test_database_connection.py`, `test_episode_store.py`

**Integration Tests (real file parsing):**
- Parse actual RSS snapshot files to guard against parser regressions
- Example: `test_feed_parser_integration.py`

**E2E Tests:**
- Not present — no browser or network E2E tests

## Coverage

**Requirement:** 100% — enforced by project policy in `CLAUDE.md`

**View Coverage:**
```bash
uv run pytest --cov=.
```

**Omit list** (`pyproject.toml`):
```toml
[tool.coverage.run]
omit = ["example_cost_calculation.py"]
```

**Ruff per-file ignores for tests** (relaxed rules in `tests/**/*.py`):
- `S101` — assert statements allowed
- `ANN` — type annotations not required
- `D101`/`D102`/`D103` — docstrings not required
- `PLR2004` — magic values in assertions are idiomatic
- `SLF001` — direct private method testing allowed
- `ARG001` — unused fixture arguments (side-effect-only fixtures)

---

*Testing analysis: 2026-05-14*
