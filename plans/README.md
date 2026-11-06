# Web GUI — Plan Index and Decision Record

This folder holds one implementation plan per step. This README is the **spec** every plan argues from:
it records what was decided and why. Execute the plans in numeric order; later plans state which
earlier plans they depend on.

| # | Plan | Gap | Depends on |
|---|---|---|---|
| 01 | [Reload config at run start](01-config-reload.md) | G1 | — |
| 02 | [SQLite WAL mode](02-wal-mode.md) | G8 | — |
| 03 | [Per-model LLM params](03-llm-params.md) | G6 | — |
| 04 | [Episode enable / disable / delete](04-episode-skip-delete.md) | G4 | 01 |
| 05 | [Read endpoints for the GUI](05-read-endpoints.md) | G5 | — |
| 06 | [Docker: config dir, output bind mount, serve-mode scheduler](06-docker-serve-mode.md) | G2, G3 | — |
| 07 | [GUI container (Caddy + static Alpine.js app)](07-gui-container.md) | — | 01–06 |
| 08 | [Deployment with host Tailscale](08-deployment-tailscale.md) | — | 06, 07 |

## Decisions

### Architecture
- **Backend stays as is**: aiohttp JSON API under `/api/v1` plus SSE (`api/`). No rewrite. Every change is a
  small addition to existing routes/stores/config.
- **GUI is a separate container** (`gui/`, same repo, separate image `ghcr.io/<repo>-gui`). Caddy serves
  static files and reverse-proxies `/api/*` to the backend container. One origin means no CORS code.
  We use Caddy because it flushes `text/event-stream` responses immediately with no extra config.
- **Frontend: Alpine.js, no build step**, vendored at a pinned version. Hash routing, so the server needs no rewrites.
- **Visual design: direction 5 "Three-Pane"**, chosen from five mockups
  (https://claude.ai/artifact/178BxXfHbAHufgYVUBk9h4, row 5), plus the large audio timeline bar from direction 3.
  - Layout: feeds sidebar | episode list | detail pane.
  - A status bar at the bottom shows the live run on every screen.
  Upgrade path if it outgrows this: a Vite/Svelte build stage inside `gui/Dockerfile` only.
- **Exposure: Tailscale installed on the host.**
  - `tailscale funnel` serves the `output/` folder publicly (RSS + audio).
  - `tailscale serve --https=8443` exposes the GUI container (published on `127.0.0.1:8090`) to the tailnet only.
  - The backend publishes no ports.
- **No authentication** (G7 dropped): internal Docker network + tailnet-only GUI. Anyone who can reach
  the GUI port has full control (settings, paid LLM runs).

### Behaviour
- **G1**: every run start (`POST /run`, `POST /feeds/{slug}/run`) re-reads `config.yaml` through
  `load_config()`. Settings and feed edits take effect on the next run, without a restart.
- **G4 disable** = `skipped = 1`. Skipped episodes are **not processed and not published in the RSS**.
  - **Confirmed by the user:** a skipped episode disappears from the feed. Today they are still
    published, pointing at the original URL with ads.
  - **Enable** = `skipped = 0`.
- **G4 delete** = skip + full reset of pipeline data (`reset_episode`, `download` cascade) + remove the
  output audio file. The episode row stays, so feed sync (`INSERT OR IGNORE`) cannot resurrect it.
  The RSS drops the item on the next run.
- **G6**: each `models.<stage>` gets a free-form `params` dict. It is merged over the built-in defaults
  (e.g. `temperature`) and passed to LiteLLM. Reserved keys cannot be overridden. `PATCH /settings`
  adopts JSON Merge Patch semantics: `null` deletes a key.
- **G5**: add topic endpoint, single-episode endpoint, `q` title search, a `skipped` filter, and an `X-Total-Count` header.
  - No `state=` filter: the `complete` state is derived from the filesystem, so SQL pagination cannot
    filter on it. Add it when the state is stored in the DB.

## Global constraints (apply to every plan)
- Python 3.12, async throughout, context managers for every resource.
- Logging uses f-strings, never `%`.
- TDD: failing test first, then the minimum code.
- After every change all three must pass: `uv run pytest`, `uv run pytest --cov=.` (100%), `uv run ruff check .`.
- Before using any external library or API (aiohttp, pydantic, aiosqlite, litellm, Caddy, Alpine.js,
  Tailscale), look up current docs via Context7, falling back to the web. Never rely on memory.
- Never share the pipeline's aiosqlite connection with API read handlers; reads use `ReadOnlyDatabase`.
- Never use `web.run_app()`.
- Config writes are atomic: validate with Pydantic, write a temp file, then `os.replace()`.
- SSE handlers unsubscribe in `finally`.
- `aiohttp>=3,<3.14` pin stays (aioresponses incompatibility).
- Keep `API.md` in sync with every endpoint change; it is the GUI's contract.
- Commit messages: conventional commits (`feat(api): …`, `fix(api): …`), ending with the
  Co-Authored-By trailer.
