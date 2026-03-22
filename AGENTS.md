# Podcast Ad Cutter

## Documentation Policy

**Before implementing any feature that uses an external library or API, look up the current documentation.**

1. Always use Context7 when I need library/API documentation, code generation, setup or configuration steps without me having to explicitly ask.
2. If Context7 has no coverage, use sources from the web.
3. Never rely on training-data knowledge — it may be outdated.

### Library Documentation Sources

- `aiohttp`:
  - Example: _use context7 to show me how to open a http connection_
  - Documentation:`https://docs.aiohttp.org/en/stable/`
  - `https://github.com/aio-libs/aiohttp`

- `pydantic`:
  - Example: _use context7 to show me how to create a model_
  - Documentation:`https://docs.pydantic.dev/latest/`
  - Source: `https://github.com/pydantic/pydantic`

- `aiosqlite`:
  - Example: _use context7 to show me how to create a table_
  - Documentation: `https://aiosqlite.omnilib.dev/en/latest/api.html`
  - Source: `https://github.com/omnilib/aiosqlite`

- `litellm`:
  - Example: _use context7 to show me how to connect to openai_
  - Documentation: `https://docs.litellm.ai/docs/#litellm-python-sdk`
  - Source: `https://github.com/BerriAI/litellm`

  - `ffmpeg` and `ffprobe`:
  - Example: _use context7 for ffmpeg syntax on how to export an audio file to aac_
  - Documentation: `https://ffmpeg.org/ffmpeg.html`

---

## Test-Driven Development

Write tests before implementation.

1. Write a failing test that defines the expected behavior.
2. Implement the minimum code to pass it.
3. Refactor, keeping tests green.

---

## Linting/Code Checking

- Run `uv run pytest` after every change; all tests must pass before proceeding.
- Run `uv run pytest --cov=.` after every change; coverage must be 100% before proceeding.
- Run `uv run ruff` after every change; all errors must be resolved before proceeding.

---

## Running

```bash
uv run python main.py       # run app
uv run pytest               # run tests
uv run pytest --cov=.       # run coverage
uv run ruff                 # run ruff
uv run python               # run local python version
```

---

## Project Structure
```
/  # contains main.py, config.example.yaml, .env.example, pyproject.toml
```

All other files live in subfolders. `pyproject.toml` is the single source of truth for metadata, dependencies, and tool config.

---

## Code Style

- Python 3.12 target.
- Async throughout.
- Context managers for every resource (`with`/`async with`).
- Modular, decoupled architecture, each feature in a separate class with a public API.

---

## Logging Style

When composing the messages always use f-strings and never the modulo operator (%).

---