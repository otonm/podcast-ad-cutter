# Podcast Ad Cutter

See README.md (usage), ARCHITECTURE.md (design), API.md (HTTP contract).

## Workflow

- Look up current docs (Context7, else the web) before using any external library, API or tool; never rely on memory.
- TDD: write the failing test first and watch it fail, then the minimum code.
- After every change all must pass:
  - `uv run pytest` and `uv run pytest --cov=.` (coverage must stay at 100%)
  - `uv run ruff check .`
  - GUI changes: `cd gui && node --test tests/`
- API.md is the GUI's contract: update it with every endpoint or payload change.

## Conventions

- Async throughout; a context manager (`with`/`async with`) for every resource.
- Logging: f-strings, never `%` formatting.
- New code goes in subfolders; the repo root is only for the entry point (`main.py`), `pyproject.toml` (single source of truth for deps and tool config), Docker files, example configs and docs.

## Hard constraints

- Never share the pipeline's aiosqlite connection with API handlers; API reads use `ReadOnlyDatabase`.
- Never use `web.run_app()`; use `AppRunner` + `TCPSite`.
- Config writes: validate through Pydantic, write a temp file in the same directory, `os.replace()`.
- SSE handlers unsubscribe/close in a `finally` block.
- GUI (`gui/web`): no build step, no npm dependencies. Vendored libraries are pinned to exact versions at least two weeks old, recorded in `index.html`. Encode GUIDs with `encodeURIComponent`. Never `x-html` server data.
