# Testing

**Analysis Date:** 2026-03-28

## Framework

- **Test runner:** pytest 9.0.2
- **Async mode:** pytest-asyncio in `auto` mode (all async tests run without explicit decorator)
- **Coverage:** pytest-cov; 100% coverage required before proceeding (enforced in CLAUDE.md)
- **Config:** `pyproject.toml` `[tool.pytest.ini_options]` and `[tool.coverage]`

## Structure

- **Location:** `tests/` directory, 23 test files, ~7000 lines total
- **Naming:** One test file per module — `test_pipeline.py`, `test_feed_downloader.py`, `test_config_loader.py`, etc.
- **Integration tests:** `tests/test_feed_parser_integration.py` — full XML + DB round-trip
- **Fixtures:** `tests/static/` — sample RSS/Atom XML files for parser tests

## Async Patterns

All test functions that test async code are `async def`:

```python
async def test_run_no_enabled_feeds(self, pipeline):
    result = await pipeline.run()
    assert result == []
```

pytest-asyncio `auto` mode handles event loop lifecycle without explicit `@pytest.mark.asyncio`.

## Mocking Patterns

**subprocess (ffmpeg/ffprobe):**
```python
# Helper factory pattern
def _make_proc(stdout=b"", stderr=b"", returncode=0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.returncode = returncode
    return proc

with patch("components.audio_prober.subprocess.run", return_value=_make_proc(...)):
    ...
```

**aiohttp (HTTP):**
```python
# AsyncMock for coroutine-returning methods
mock_response = AsyncMock()
mock_response.status = 200
mock_response.read = AsyncMock(return_value=b"audio bytes")
mock_session = MagicMock()
mock_session.__aenter__ = AsyncMock(return_value=mock_session)
mock_session.__aexit__ = AsyncMock(return_value=False)
mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
```

**litellm:**
```python
with patch("components.episode_transcriptor.litellm.atranscription") as mock_transcribe:
    mock_transcribe.return_value = Mock(text="transcript text", usage=Mock(...))
    result = await transcriptor.transcribe(...)
```

**aiosqlite (database):**
- Integration tests use real SQLite with `tmp_path` fixture (not mocked)
- Unit tests that touch DB mock the connection/cursor

**Environment variables:**
```python
def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ...
```

## Fixture Patterns

**Database fixture (real SQLite):**
```python
@pytest.fixture
async def db(tmp_path):
    async with Database(tmp_path / "test.db") as database:
        yield database
```

**Component fixtures:**
```python
@pytest.fixture
def pipeline(tmp_path):
    config = _make_config(tmp_path)
    return Pipeline(config)
```

**Helper factories for test data:**
```python
def _make_episode(**kwargs) -> Episode:
    defaults = {"guid": "ep1", "title": "Test", "url": "http://example.com/ep.mp3", ...}
    return Episode(**{**defaults, **kwargs})
```

## Test Categories

**Unit tests:** Each component tested in isolation with mocked dependencies
- `test_feed_downloader.py` — HTTP mocking
- `test_audio_prober.py` — subprocess mocking
- `test_audio_preprocessor.py` — subprocess mocking
- `test_episode_transcriptor.py` — litellm mocking
- `test_topic_extractor.py` — litellm mocking

**Store tests:** Real SQLite via `tmp_path`, foreign key enforcement enabled
- `test_episode_store.py`, `test_transcription_store.py`, `test_audio_metadata_store.py`
- `test_cost_tracking_store.py`, `test_topic_store.py`

**Integration tests:** XML parsing → DB round-trip
- `test_feed_parser_integration.py` — feeds a real RSS XML file through parser and stores to DB

**Config tests:** Validation, error cases, missing fields, env var loading
- `test_config_loader.py`

**Entry point tests:** CLI argument parsing, logging setup, exit codes
- `test_main.py`

## Error Testing

```python
# pytest.raises for expected exceptions
async def test_transcription_failure(transcriptor):
    with pytest.raises(TranscriptionError, match="API failed"):
        await transcriptor.transcribe(...)

# caplog for log message verification
def test_warning_on_missing_feed(caplog):
    with caplog.at_level(logging.WARNING):
        result = parser.parse(bad_xml)
    assert "Failed to parse" in caplog.text
```

## Parametrized Tests

```python
@pytest.mark.parametrize("codec,expected", [
    ("mp3", "mp3"),
    ("aac", "aac"),
    ("opus", "opus"),
])
def test_codec_detection(codec, expected):
    ...
```

## Coverage Requirements

- **Target:** 100% line coverage
- **Run:** `uv run pytest --cov=.`
- **Exclusions:** Configured in `pyproject.toml` `[tool.coverage.report]`
- **Enforcement:** All tests must pass AND coverage must be 100% before any feature is considered done

## Running Tests

```bash
uv run pytest                  # run all tests
uv run pytest --cov=.          # run with coverage
uv run pytest tests/test_pipeline.py  # run specific file
uv run pytest -k "test_branch_a"      # run by name pattern
uv run ruff                    # lint check (must pass alongside tests)
```

---

*Testing analysis: 2026-03-28*
