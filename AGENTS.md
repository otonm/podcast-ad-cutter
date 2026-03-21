# Podcast Ad Cutter

## Documentation Policy

**Before implementing any feature that uses an external library or API, look up the current documentation.**

1. Use Context7 MCP first: resolve the library ID, then query the relevant docs.
2. If Context7 has no coverage, use web search.
3. Do not rely on training-data knowledge for these — it may be outdated.

---

## Test-Driven Development

Write tests before implementation.

1. Write a failing test that defines the expected behavior.
2. Implement the minimum code to pass it.
3. Refactor, keeping tests green.

---

## Linting/Code Checking

- Run `uv run pytest` after every change; all tests must pass before proceeding.
- Run `uv run ruff` after every change; all errors must be resolved before proceeding.

---

## Running

```bash
uv run python main.py       # run app
uv run pytest               # run tests
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
- Modular design, each feature in a separate class with a public API.

---

## Logging Style

When composing the messages always use f-strings and never the modulo operator (%).

---