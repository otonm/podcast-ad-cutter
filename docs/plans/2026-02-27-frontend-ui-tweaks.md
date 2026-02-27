# Frontend UI Tweaks — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Improve the HTMX frontend with collapsible panels, Start→Stop button, clickable enabled badge, drag-to-reorder feeds, and config caching via watchfiles.

**Architecture:** Vanilla JS toggle functions for collapse behavior; HTMX OOB swaps for the Start→Stop button; SortableJS for drag-and-drop; a `config_cache` module with a watchfiles background watcher eliminates repeated `load_config` calls on every GET request.

**Tech Stack:** FastAPI, HTMX 2.0.4, Jinja2, Tailwind CDN, SortableJS 1.15.3 (CDN), watchfiles (new dep)

**Design doc:** `docs/plans/2026-02-27-frontend-ui-tweaks-design.md`

---

## Task 1: Add watchfiles dependency + config_cache module

**Files:**
- Create: `frontend/config_cache.py`
- Modify: `pyproject.toml` (via uv)

### Step 1: Install watchfiles

```bash
uv add watchfiles
```

Expected: `pyproject.toml` updated, `uv.lock` regenerated.

### Step 2: Create `frontend/config_cache.py`

```python
"""Cached AppConfig — loaded once at startup, refreshed by a file-watcher task.

All routes call get_config() instead of load_config() directly.
set_config() is called from the lifespan and after every config mutation.
"""

from config.config_loader import AppConfig

_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Return the cached config. Raises RuntimeError if not yet loaded."""
    if _config is None:
        raise RuntimeError("Config cache not initialised — lifespan must run first")
    return _config


def set_config(cfg: AppConfig) -> None:
    """Replace the cached config. Called at startup and after every write."""
    global _config
    _config = cfg
```

### Step 3: Verify lint + types

```bash
uv run ruff check frontend/config_cache.py && uv run mypy frontend/config_cache.py
```

Expected: no errors.

### Step 4: Commit

```bash
git add frontend/config_cache.py pyproject.toml uv.lock
git commit -m "feat: add config_cache module and watchfiles dependency"
```

---

## Task 2: Wire config cache into app.py lifespan + all routes

**Files:**
- Modify: `frontend/app.py`
- Modify: `frontend/routes/pages.py`
- Modify: `frontend/routes/feeds.py`
- Modify: `frontend/routes/settings.py`
- Modify: `frontend/routes/pipeline.py`

### Step 1: Rewrite `frontend/app.py`

Replace the entire file with:

```python
"""FastAPI application factory for the Podcast Ad Cutter web frontend."""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from watchfiles import awatch

from config.config_loader import load_config
from frontend import config_cache
from frontend.config_editor import get_config_path

_TEMPLATE_DIR = Path(__file__).parent / "templates"

templates: Jinja2Templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
logger = logging.getLogger(__name__)


def _slugify(value: str) -> str:
    return _NON_ALNUM.sub("-", value.lower()).strip("-")


templates.env.filters["slugify"] = _slugify


async def _watch_config() -> None:
    """Reload config whenever config.yaml is modified on disk (external edits)."""
    async for _ in awatch(get_config_path()):
        try:
            config_cache.set_config(load_config(get_config_path()))
            logger.info("Config reloaded from disk")
        except Exception as exc:
            logger.warning(f"Config reload failed: {exc}")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load config on startup; watch for file changes; cancel watcher on shutdown."""
    config_cache.set_config(load_config(get_config_path()))
    task = asyncio.create_task(_watch_config())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    """Application factory — called by uvicorn when factory=True."""
    from frontend.routes.feeds import router as feeds_router
    from frontend.routes.pages import router as pages_router
    from frontend.routes.pipeline import router as pipeline_router
    from frontend.routes.settings import router as settings_router

    app = FastAPI(
        title="Podcast Ad Cutter",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    app.include_router(pages_router)
    app.include_router(feeds_router)
    app.include_router(pipeline_router)
    app.include_router(settings_router)

    return app
```

### Step 2: Rewrite `frontend/routes/pages.py`

Replace entire file:

```python
"""Page routes: GET / and GET /cost."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db.connection import get_db
from frontend import config_cache
from frontend.app import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Render the main page."""
    cfg = config_cache.get_config()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"feeds": cfg.feeds},
    )


@router.get("/cost", response_class=HTMLResponse)
async def cost_badge(request: Request) -> HTMLResponse:
    """Return the cost badge partial with the current total LLM cost."""
    total_cost = 0.0
    try:
        cfg = config_cache.get_config()
        async with get_db(cfg.paths.database) as db:
            cursor = await db.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls")
            row = await cursor.fetchone()
            if row:
                total_cost = float(row[0])
    except Exception:
        logger.debug("Could not fetch cost from database (may not exist yet)")

    return templates.TemplateResponse(
        request=request,
        name="partials/cost_badge.html",
        context={"total_cost": total_cost},
    )
```

### Step 3: Update `frontend/routes/feeds.py`

Replace the two `load_config(get_config_path())` calls that are used for GET reads with `config_cache.get_config()`. For mutations that need fresh config data (add_feed, toggle_feed), update the cache first then read from it.

Replace the entire file:

```python
"""Feed management routes."""

import logging
from typing import Any, cast
from urllib.parse import unquote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from config.config_loader import load_config
from frontend import config_cache, config_editor
from frontend.app import templates
from frontend.config_editor import get_config_path
from frontend.state import FeedStatus, is_running

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feeds")


def _feed_status(enabled: bool) -> str:
    """Return the display status string for a feed."""
    if is_running():
        return FeedStatus.RUNNING if enabled else FeedStatus.DISABLED
    return FeedStatus.ENABLED if enabled else FeedStatus.DISABLED


@router.get("", response_class=HTMLResponse)
async def list_feeds(request: Request) -> HTMLResponse:
    """Return the feed table rows partial."""
    cfg = config_cache.get_config()
    return templates.TemplateResponse(
        request=request,
        name="partials/feed_table.html",
        context={"feeds": cfg.feeds},
    )


@router.get("/add-form", response_class=HTMLResponse)
async def add_feed_form(request: Request) -> HTMLResponse:
    """Return the inline add-feed form row."""
    return templates.TemplateResponse(
        request=request,
        name="partials/add_feed_form.html",
        context={},
    )


@router.get("/cancel-add", response_class=HTMLResponse)
async def cancel_add(request: Request) -> HTMLResponse:  # noqa: ARG001
    """Return an empty div to replace the add-feed form row."""
    return HTMLResponse('<div id="add-feed-row"></div>')


@router.post("", response_class=HTMLResponse)
async def add_feed(
    request: Request,
    name: str = Form(...),
    url: str = Form(...),
    enabled: str = Form(default=""),
) -> HTMLResponse:
    """Add a new feed and return the full updated feed table body."""
    is_enabled = enabled.lower() in ("true", "on", "1", "yes")
    config_editor.add_feed(name, url, enabled=is_enabled)
    cfg = load_config(get_config_path())
    config_cache.set_config(cfg)
    return templates.TemplateResponse(
        request=request,
        name="partials/feed_table.html",
        context={"feeds": cfg.feeds},
    )


@router.delete("/{name}", response_class=HTMLResponse)
async def delete_feed(name: str) -> HTMLResponse:
    """Delete a feed and return an empty string to remove the row."""
    config_editor.delete_feed(unquote(name))
    return HTMLResponse("")


@router.put("/reorder")
async def reorder_feeds(request: Request) -> Response:
    """Reorder feeds in config.yaml to match the provided name order."""
    body = cast(dict[str, Any], await request.json())
    names = [str(n) for n in body.get("names", [])]
    config_editor.reorder_feeds(names)
    return Response(status_code=200)


@router.put("/{name}/toggle", response_class=HTMLResponse)
async def toggle_feed(request: Request, name: str) -> HTMLResponse:
    """Toggle a feed's enabled state and return the updated row."""
    decoded_name = unquote(name)
    config_editor.toggle_feed(decoded_name)
    cfg = load_config(get_config_path())
    config_cache.set_config(cfg)
    feed = next((f for f in cfg.feeds if f.name == decoded_name), None)
    if feed is None:
        return HTMLResponse("")
    return templates.TemplateResponse(
        request=request,
        name="partials/feed_row.html",
        context={"feed": feed, "status": _feed_status(feed.enabled)},
    )
```

### Step 4: Update `frontend/routes/settings.py`

Replace the entire file. Key changes:
- `GET /settings`: use `config_cache.get_config()` instead of `load_config`
- `POST /settings` success: update cache, return `HTMLResponse("")` (collapses accordion)
- `POST /settings` failure: render form with error (unchanged)

```python
"""Settings routes."""

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from config.config_loader import SUPPORTED_PROVIDERS, AppConfig, load_config
from frontend import config_cache, config_editor
from frontend.app import templates
from frontend.config_editor import get_config_path

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request) -> HTMLResponse:
    """Return the settings form partial with current values."""
    cfg = config_cache.get_config()
    return templates.TemplateResponse(
        request=request,
        name="partials/settings_form.html",
        context={
            "cfg": cfg,
            "providers": sorted(SUPPORTED_PROVIDERS),
            "error": None,
        },
    )


@router.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    transcription_provider: str = Form(...),
    transcription_model: str = Form(...),
    interpretation_provider: str = Form(...),
    interpretation_model: str = Form(...),
    min_confidence: float = Form(...),
) -> HTMLResponse:
    """Validate and save settings. Returns empty on success (collapses accordion)."""
    cfg: AppConfig | None = None
    error: str | None = None

    try:
        config_editor.update_settings(
            transcription_provider=transcription_provider,
            transcription_model=transcription_model,
            interpretation_provider=interpretation_provider,
            interpretation_model=interpretation_model,
            min_confidence=min_confidence,
        )
        validated = load_config(get_config_path())
        config_cache.set_config(validated)
        # Return empty — HTMX clears #settings-accordion, collapsing the panel.
        return HTMLResponse("")
    except Exception as exc:
        logger.warning(f"Settings save failed: {exc}")
        error = str(exc)
        try:
            cfg = config_cache.get_config()
        except RuntimeError:
            cfg = None

    return templates.TemplateResponse(
        request=request,
        name="partials/settings_form.html",
        context={
            "cfg": cfg,
            "providers": sorted(SUPPORTED_PROVIDERS),
            "error": error,
        },
    )
```

### Step 5: Update `frontend/routes/pipeline.py`

Replace `load_config(get_config_path())` in `_run_pipeline_task` with `config_cache.get_config()`. Remove unused imports.

Replace the entire file (task 3 will add more changes; apply them together):

```python
"""Pipeline control routes: run, stop, SSE events, and status."""

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from sse_starlette import EventSourceResponse

from frontend import config_cache, sse, state
from frontend.app import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline")

_active_queue: asyncio.Queue[str | None] | None = None


@router.post("/run", response_class=HTMLResponse)
async def run_pipeline(
    request: Request,
    dry_run: bool = False,
) -> HTMLResponse:
    """Start the pipeline in a background task and return the progress partial."""
    if state.is_running():
        return HTMLResponse(
            '<p class="text-yellow-700 text-sm">⚠ Pipeline is already running.</p>'
        )

    global _active_queue
    loop = asyncio.get_event_loop()
    _active_queue = sse.attach_handler(loop)

    state.set_running(True)
    task = asyncio.create_task(_run_pipeline_task(dry_run))
    state.set_task(task)

    return templates.TemplateResponse(
        request=request,
        name="partials/progress.html",
        context={},
    )


async def _run_pipeline_task(dry_run: bool) -> None:
    """Background task: run the pipeline then enqueue None sentinel."""
    from pipeline.runner import run_pipeline as _run_pipeline

    try:
        cfg = config_cache.get_config()
        await _run_pipeline(cfg, dry_run=dry_run)
    except asyncio.CancelledError:
        logger.info("Pipeline cancelled by user")
        raise
    except Exception as exc:
        logger.error(f"Pipeline failed: {exc}")
    finally:
        state.set_running(False)
        state.set_task(None)
        queue = sse._active_queue  # noqa: SLF001
        if queue is not None:
            queue.put_nowait(None)
        sse.detach_handler()


@router.post("/stop", response_class=HTMLResponse)
async def stop_pipeline() -> HTMLResponse:
    """Cancel the running pipeline task. The SSE 'done' event restores the UI."""
    task = state.get_task()
    if task is not None and not task.done():
        task.cancel()
    return HTMLResponse("")


@router.get("/actions", response_class=HTMLResponse)
async def pipeline_actions(request: Request) -> HTMLResponse:
    """Return the Run / Dry Run button pair partial (used to restore UI after stop)."""
    return templates.TemplateResponse(
        request=request,
        name="partials/pipeline_actions.html",
        context={},
    )


@router.get("/events")
async def pipeline_events(request: Request) -> EventSourceResponse:
    """SSE endpoint: stream log lines from the active pipeline run."""
    global _active_queue

    async def _generator() -> AsyncGenerator[dict[str, str], None]:
        queue = _active_queue
        if queue is None:
            yield {"event": "done", "data": ""}
            return
        async for event in sse.event_generator(queue):
            if await request.is_disconnected():
                break
            yield event

    return EventSourceResponse(_generator(), send_timeout=60)


@router.get("/status")
async def pipeline_status() -> dict[str, bool]:
    """Return JSON with the current pipeline running state."""
    return {"running": state.is_running()}
```

### Step 6: Update `frontend/state.py`

Add `_pipeline_task` slot and accessors. Replace the entire file:

```python
"""In-memory pipeline running state.

Resets on server restart. One pipeline at a time enforced by is_running() check.
"""

import asyncio
from enum import StrEnum

# ---------------------------------------------------------------------------
# Pipeline running flag
# ---------------------------------------------------------------------------

_pipeline_running: bool = False


def is_running() -> bool:
    return _pipeline_running


def set_running(value: bool) -> None:
    global _pipeline_running
    _pipeline_running = value


# ---------------------------------------------------------------------------
# Active pipeline task (for cancellation)
# ---------------------------------------------------------------------------

_pipeline_task: asyncio.Task[None] | None = None


def set_task(task: asyncio.Task[None] | None) -> None:
    global _pipeline_task
    _pipeline_task = task


def get_task() -> asyncio.Task[None] | None:
    return _pipeline_task


# ---------------------------------------------------------------------------
# Feed status enum
# ---------------------------------------------------------------------------


class FeedStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    RUNNING = "running"
    ERROR = "error"
    DONE = "done"
```

### Step 7: Run existing tests to confirm nothing is broken

```bash
uv run pytest
```

Expected: all 59 tests pass (pipeline/DB tests are unaffected by route changes).

### Step 8: Run lint + type checks

```bash
uv run ruff check frontend/ web.py && uv run mypy frontend/ web.py
```

Expected: no errors.

### Step 9: Commit

```bash
git add frontend/app.py frontend/state.py frontend/config_cache.py \
        frontend/routes/pages.py frontend/routes/feeds.py \
        frontend/routes/settings.py frontend/routes/pipeline.py
git commit -m "feat: wire config cache into lifespan and all routes; add pipeline stop/actions endpoints"
```

---

## Task 3: Create pipeline_actions.html partial

**Files:**
- Create: `frontend/templates/partials/pipeline_actions.html`

### Step 1: Create `frontend/templates/partials/pipeline_actions.html`

This partial contains the Run + Dry Run buttons wrapped in the `#pipeline-actions` div. It is included in `index.html` on initial load, returned by `GET /pipeline/actions` to restore buttons after pipeline completes, and its ID is targeted by the OOB swap in `progress.html` to show the Stop button.

```html
<div id="pipeline-actions" class="flex gap-2">
  <button
    hx-post="/pipeline/run"
    hx-target="#progress-section"
    hx-swap="innerHTML"
    hx-indicator="#run-spinner"
    class="text-sm px-4 py-1.5 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors flex items-center gap-1.5">
    <span>▶ Run</span>
    <span id="run-spinner" class="htmx-indicator text-xs">⏳</span>
  </button>
  <button
    hx-post="/pipeline/run?dry_run=true"
    hx-target="#progress-section"
    hx-swap="innerHTML"
    hx-indicator="#dry-spinner"
    class="text-sm px-4 py-1.5 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 transition-colors flex items-center gap-1.5">
    <span>⚙ Dry Run</span>
    <span id="dry-spinner" class="htmx-indicator text-xs">⏳</span>
  </button>
</div>
```

### Step 2: Commit

```bash
git add frontend/templates/partials/pipeline_actions.html
git commit -m "feat: add pipeline_actions partial for Run/Dry Run buttons"
```

---

## Task 4: Update index.html and progress.html for Start→Stop + pipeline log collapse

**Files:**
- Modify: `frontend/templates/index.html`
- Modify: `frontend/templates/partials/progress.html`

### Step 1: Rewrite `frontend/templates/index.html`

Changes made:
1. Pipeline section header `<h2>` becomes clickable with `togglePipelineLog()` + chevron
2. Run/Dry Run buttons replaced with `{% include "partials/pipeline_actions.html" %}`
3. Settings button replaced with `onclick="toggleSettings()"` (no HTMX, pure JS)
4. Settings chevron text updated dynamically
5. JS functions added at bottom of content block

```html
{% extends "base.html" %}
{% block title %}Podcast Ad Cutter{% endblock %}

{% block content %}
<div class="space-y-6">

  <!-- ------------------------------------------------------------------ -->
  <!-- Feed management -->
  <!-- ------------------------------------------------------------------ -->
  <section class="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
    <div class="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
      <h2 class="text-base font-semibold">Feeds</h2>
      <button
        hx-get="/feeds/add-form"
        hx-target="#add-feed-row"
        hx-swap="outerHTML"
        class="text-sm px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
        + Add Feed
      </button>
    </div>

    <table class="w-full text-sm">
      <thead class="bg-gray-50 text-xs font-medium text-gray-500 uppercase tracking-wide">
        <tr>
          <th class="pl-3 pr-1 py-2 w-8"></th>
          <th class="px-5 py-2 text-left">Name</th>
          <th class="px-5 py-2 text-left">URL</th>
          <th class="px-5 py-2 text-center">Status</th>
          <th class="px-5 py-2 text-center">Actions</th>
        </tr>
      </thead>
      <tbody id="feed-tbody">
        {% include "partials/feed_table.html" %}
      </tbody>
    </table>

    <!-- Placeholder row for the add-feed inline form -->
    <div id="add-feed-row"></div>
  </section>

  <!-- ------------------------------------------------------------------ -->
  <!-- Run pipeline -->
  <!-- ------------------------------------------------------------------ -->
  <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
    <div class="flex items-center justify-between mb-4">
      <h2 class="text-base font-semibold cursor-pointer select-none flex items-center gap-1"
          onclick="togglePipelineLog()">
        Pipeline
        <span id="pipeline-chevron" class="text-gray-400 font-normal text-sm">▼</span>
      </h2>
      {% include "partials/pipeline_actions.html" %}
    </div>
    <div id="progress-section" class="text-sm text-gray-500 italic">
      Click ▶ Run to start processing enabled feeds.
    </div>
  </section>

  <!-- ------------------------------------------------------------------ -->
  <!-- Settings accordion -->
  <!-- ------------------------------------------------------------------ -->
  <section class="bg-white rounded-xl border border-gray-200 shadow-sm">
    <button
      id="settings-toggle-btn"
      onclick="toggleSettings()"
      class="w-full px-5 py-4 flex items-center justify-between text-left hover:bg-gray-50 transition-colors">
      <h2 class="text-base font-semibold">⚙ Settings</h2>
      <span id="settings-chevron" class="text-gray-400 text-sm">▶ Click to expand</span>
    </button>
    <div id="settings-accordion" class="px-5 pb-4"></div>
  </section>

</div>

<script>
function toggleSettings() {
  var accordion = document.getElementById('settings-accordion');
  var chevron = document.getElementById('settings-chevron');
  if (accordion.children.length > 0) {
    accordion.innerHTML = '';
    chevron.textContent = '▶ Click to expand';
  } else {
    htmx.ajax('GET', '/settings', {target: '#settings-accordion', swap: 'innerHTML'});
    chevron.textContent = '▼ Click to collapse';
  }
}

function togglePipelineLog() {
  var section = document.getElementById('progress-section');
  var chevron = document.getElementById('pipeline-chevron');
  if (section.style.display === 'none') {
    section.style.display = '';
    chevron.textContent = '▼';
  } else {
    section.style.display = 'none';
    chevron.textContent = '▶';
  }
}
</script>
{% endblock %}
```

### Step 2: Rewrite `frontend/templates/partials/progress.html`

Changes:
1. SSE "done" handler calls `htmx.ajax` to restore `#pipeline-actions`
2. An OOB swap block at the end replaces `#pipeline-actions` with a Stop button when the partial is loaded

```html
<div class="space-y-3">
  <div class="flex items-center gap-2 text-sm font-medium text-green-700">
    <span id="progress-status">⏳ Pipeline running…</span>
  </div>
  <div id="log-output"
       class="bg-gray-900 text-green-400 rounded-lg p-4 text-xs font-mono h-64 overflow-y-auto space-y-0.5">
  </div>
</div>

<script>
(function () {
  const output = document.getElementById("log-output");
  const status = document.getElementById("progress-status");
  const es = new EventSource("/pipeline/events");

  es.addEventListener("log", function (e) {
    const p = document.createElement("p");
    p.className = "whitespace-pre-wrap break-all";
    p.textContent = e.data;
    output.appendChild(p);
    output.scrollTop = output.scrollHeight;
  });

  es.addEventListener("done", function () {
    es.close();
    status.textContent = "✓ Pipeline complete";
    status.className = "text-green-700 font-medium";
    htmx.trigger("#header-cost", "load");
    htmx.ajax("GET", "/pipeline/actions", {target: "#pipeline-actions", swap: "outerHTML"});
  });

  es.onerror = function () {
    es.close();
    status.textContent = "⚠ Connection lost";
    status.className = "text-yellow-700 font-medium";
    htmx.ajax("GET", "/pipeline/actions", {target: "#pipeline-actions", swap: "outerHTML"});
  };
})();
</script>

<!-- OOB: swap #pipeline-actions with a Stop button while pipeline is running -->
<div id="pipeline-actions" hx-swap-oob="true">
  <button
    hx-post="/pipeline/stop"
    hx-swap="none"
    class="text-sm px-4 py-1.5 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors">
    ⏹ Stop
  </button>
</div>
```

### Step 3: Commit

```bash
git add frontend/templates/index.html frontend/templates/partials/progress.html
git commit -m "feat: Start→Stop OOB swap, pipeline log collapse, settings accordion toggle"
```

---

## Task 5: Remove success banner from settings_form.html

**Files:**
- Modify: `frontend/templates/partials/settings_form.html`

### Step 1: Remove the `{% if saved %}` block from `settings_form.html`

The settings POST now returns empty on success (accordion collapses). The `saved` variable is no longer passed from the route. Remove the green success banner block.

Replace the top of the file — remove lines 12–16 (the `{% if saved %}` block):

```html
<form hx-post="/settings"
      hx-target="#settings-accordion"
      hx-swap="innerHTML"
      class="space-y-4 pt-2">

  {% if error %}
  <div class="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
    {{ error }}
  </div>
  {% endif %}

  <fieldset class="border border-gray-200 rounded-lg p-4">
    <legend class="text-xs font-semibold text-gray-500 uppercase tracking-wide px-1">Transcription</legend>
    <div class="grid grid-cols-2 gap-3 mt-2">
      <div>
        <label class="block text-xs font-medium text-gray-700 mb-1">Provider</label>
        <select name="transcription_provider"
                class="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
          {% for p in providers %}
          <option value="{{ p }}" {% if p == cfg.transcription.provider %}selected{% endif %}>{{ p }}</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-700 mb-1">Model</label>
        <input type="text"
               name="transcription_model"
               value="{{ cfg.transcription.model }}"
               class="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
      </div>
    </div>
  </fieldset>

  <fieldset class="border border-gray-200 rounded-lg p-4">
    <legend class="text-xs font-semibold text-gray-500 uppercase tracking-wide px-1">Interpretation</legend>
    <div class="grid grid-cols-2 gap-3 mt-2">
      <div>
        <label class="block text-xs font-medium text-gray-700 mb-1">Provider</label>
        <select name="interpretation_provider"
                class="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400">
          {% for p in providers %}
          <option value="{{ p }}" {% if p == cfg.interpretation.provider %}selected{% endif %}>{{ p }}</option>
          {% endfor %}
        </select>
      </div>
      <div>
        <label class="block text-xs font-medium text-gray-700 mb-1">Model</label>
        <input type="text"
               name="interpretation_model"
               value="{{ cfg.interpretation.model }}"
               class="w-full border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
      </div>
    </div>
  </fieldset>

  <fieldset class="border border-gray-200 rounded-lg p-4">
    <legend class="text-xs font-semibold text-gray-500 uppercase tracking-wide px-1">Ad Detection</legend>
    <div class="max-w-xs mt-2">
      <label class="block text-xs font-medium text-gray-700 mb-1">
        Min confidence ({{ cfg.ad_detection.min_confidence }})
      </label>
      <input type="range"
             name="min_confidence"
             min="0.0" max="1.0" step="0.05"
             value="{{ cfg.ad_detection.min_confidence }}"
             oninput="this.previousElementSibling.previousElementSibling.textContent = 'Min confidence (' + parseFloat(this.value).toFixed(2) + ')'"
             class="w-full accent-blue-600" />
    </div>
  </fieldset>

  <div class="flex gap-2 pt-1">
    <button type="submit"
            class="px-4 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
      Save
    </button>
  </div>
</form>
```

### Step 2: Commit

```bash
git add frontend/templates/partials/settings_form.html
git commit -m "feat: settings form collapses on save, remove success banner"
```

---

## Task 6: Feed drag-and-drop, clickable enabled badge, reorder endpoint

**Files:**
- Modify: `frontend/config_editor.py`
- Modify: `frontend/templates/base.html`
- Modify: `frontend/templates/partials/feed_row.html`
- Modify: `frontend/templates/partials/feed_table.html`
- Modify: `frontend/templates/partials/add_feed_form.html`

(The reorder route `PUT /feeds/reorder` was already added in Task 2.)

### Step 1: Add `reorder_feeds` to `frontend/config_editor.py`

Add this function after `toggle_feed`, before `update_settings`:

```python
def reorder_feeds(names: list[str]) -> None:
    """Reorder feeds in config.yaml to match the given name order.

    Any feed name not present in `names` is appended at the end unchanged.
    """
    data = _load()
    existing = data.get("feeds")
    feeds: list[dict[str, object]] = cast(list[dict[str, object]], existing) if existing else []
    feed_map = {str(f.get("name", "")): f for f in feeds}
    reordered = [feed_map[n] for n in names if n in feed_map]
    leftover = [f for f in feeds if str(f.get("name", "")) not in names]
    data["feeds"] = reordered + leftover
    _save(data)
```

### Step 2: Add SortableJS to `frontend/templates/base.html`

Add a SortableJS `<script>` tag after the existing two scripts in `<head>`:

```html
  <script src="https://unpkg.com/htmx.org@2.0.4" crossorigin="anonymous"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.3/Sortable.min.js"></script>
```

### Step 3: Rewrite `frontend/templates/partials/feed_row.html`

Changes:
1. Add `data-name="{{ feed.name }}"` attribute to the `<tr>` for SortableJS
2. Add drag handle `<td>` as first column
3. Status badge (enabled/disabled only) becomes a clickable HTMX button
4. Remove the Enable/Disable toggle button from Actions column

```html
<tr id="feed-{{ feed.name | slugify }}" data-name="{{ feed.name }}"
    class="border-t border-gray-100 hover:bg-gray-50 transition-colors">
  <td class="pl-3 pr-1 py-3 text-gray-400 cursor-grab drag-handle select-none" title="Drag to reorder">≡</td>
  <td class="px-5 py-3 font-medium">{{ feed.name }}</td>
  <td class="px-5 py-3 text-gray-500 max-w-xs truncate">
    <a href="{{ feed.url }}" target="_blank" class="hover:text-blue-600 hover:underline">{{ feed.url }}</a>
  </td>
  <td class="px-5 py-3 text-center">
    {% if status == "running" %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800">⚡ running</span>
    {% elif status == "done" %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">✓ done</span>
    {% elif status == "error" %}
      <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">✕ error</span>
    {% elif feed.enabled %}
      <button
        hx-put="/feeds/{{ feed.name | urlencode }}/toggle"
        hx-target="#feed-{{ feed.name | slugify }}"
        hx-swap="outerHTML"
        class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800 cursor-pointer hover:opacity-75 transition-opacity"
        title="Click to disable">
        ● enabled
      </button>
    {% else %}
      <button
        hx-put="/feeds/{{ feed.name | urlencode }}/toggle"
        hx-target="#feed-{{ feed.name | slugify }}"
        hx-swap="outerHTML"
        class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600 cursor-pointer hover:opacity-75 transition-opacity"
        title="Click to enable">
        ● disabled
      </button>
    {% endif %}
  </td>
  <td class="px-5 py-3 text-center">
    <button
      hx-delete="/feeds/{{ feed.name | urlencode }}"
      hx-target="#feed-{{ feed.name | slugify }}"
      hx-swap="outerHTML"
      hx-confirm="Delete feed '{{ feed.name }}'?"
      class="text-xs px-2 py-1 rounded bg-red-50 text-red-600 hover:bg-red-100 transition-colors"
      title="Delete feed">
      ✕
    </button>
  </td>
</tr>
```

### Step 4: Update `frontend/templates/partials/feed_table.html`

Update `colspan="4"` → `colspan="5"` in the empty-state row:

```html
{% for feed in feeds %}
{% include "partials/feed_row.html" %}
{% else %}
<tr>
  <td colspan="5" class="px-5 py-4 text-center text-gray-400 italic text-sm">
    No feeds configured. Click "+ Add Feed" to add one.
  </td>
</tr>
{% endfor %}
```

### Step 5: Update `frontend/templates/partials/add_feed_form.html`

Add empty drag-handle cell before the form cell, reduce main cell colspan to 4:

```html
<tr id="add-feed-row" class="border-t border-gray-100 bg-blue-50">
  <td class="pl-3 pr-1 py-3"></td>
  <td class="px-5 py-3" colspan="4">
    <form hx-post="/feeds"
          hx-target="#feed-tbody"
          hx-swap="innerHTML"
          hx-on::after-request="document.getElementById('add-feed-row').outerHTML = '<div id=\'add-feed-row\'></div>'"
          class="flex items-center gap-3 flex-wrap">
      <input
        type="text"
        name="name"
        placeholder="Feed name"
        required
        class="flex-1 min-w-[140px] border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
      <input
        type="url"
        name="url"
        placeholder="https://..."
        required
        class="flex-[2] min-w-[200px] border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400" />
      <label class="flex items-center gap-1 text-sm text-gray-700 cursor-pointer">
        <input type="checkbox" name="enabled" value="true" checked class="rounded" />
        Enabled
      </label>
      <button type="submit"
              class="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
        Add
      </button>
      <button type="button"
              hx-get="/feeds/cancel-add"
              hx-target="#add-feed-row"
              hx-swap="outerHTML"
              class="px-3 py-1.5 text-sm bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200 transition-colors">
        Cancel
      </button>
    </form>
  </td>
</tr>
```

### Step 6: Add SortableJS initialisation to `frontend/templates/index.html`

Add this `<script>` block after the existing `toggleSettings` / `togglePipelineLog` script, still inside `{% block content %}`:

```html
<script>
document.addEventListener('DOMContentLoaded', function () {
  var tbody = document.getElementById('feed-tbody');
  if (tbody && typeof Sortable !== 'undefined') {
    Sortable.create(tbody, {
      handle: '.drag-handle',
      animation: 150,
      onEnd: function () {
        var names = Array.from(tbody.querySelectorAll('tr[data-name]'))
          .map(function (row) { return row.dataset.name; });
        fetch('/feeds/reorder', {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({names: names})
        });
      }
    });
  }
});
</script>
```

Merge both `<script>` blocks into one (put all JS together at the end of `{% block content %}`):

Final `<script>` section at the bottom of `index.html`:

```html
<script>
function toggleSettings() {
  var accordion = document.getElementById('settings-accordion');
  var chevron = document.getElementById('settings-chevron');
  if (accordion.children.length > 0) {
    accordion.innerHTML = '';
    chevron.textContent = '▶ Click to expand';
  } else {
    htmx.ajax('GET', '/settings', {target: '#settings-accordion', swap: 'innerHTML'});
    chevron.textContent = '▼ Click to collapse';
  }
}

function togglePipelineLog() {
  var section = document.getElementById('progress-section');
  var chevron = document.getElementById('pipeline-chevron');
  if (section.style.display === 'none') {
    section.style.display = '';
    chevron.textContent = '▼';
  } else {
    section.style.display = 'none';
    chevron.textContent = '▶';
  }
}

document.addEventListener('DOMContentLoaded', function () {
  var tbody = document.getElementById('feed-tbody');
  if (tbody && typeof Sortable !== 'undefined') {
    Sortable.create(tbody, {
      handle: '.drag-handle',
      animation: 150,
      onEnd: function () {
        var names = Array.from(tbody.querySelectorAll('tr[data-name]'))
          .map(function (row) { return row.dataset.name; });
        fetch('/feeds/reorder', {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({names: names})
        });
      }
    });
  }
});
</script>
```

### Step 7: Run lint + type checks

```bash
uv run ruff check frontend/ && uv run mypy frontend/ web.py
```

Expected: no errors.

### Step 8: Run tests

```bash
uv run pytest
```

Expected: all tests pass.

### Step 9: Commit

```bash
git add frontend/config_editor.py \
        frontend/templates/base.html \
        frontend/templates/index.html \
        frontend/templates/partials/feed_row.html \
        frontend/templates/partials/feed_table.html \
        frontend/templates/partials/add_feed_form.html
git commit -m "feat: drag-to-reorder feeds, clickable enabled badge, remove Enable button"
```

---

## Manual Smoke Test Checklist

Start the dev server:

```bash
uv run python web.py --reload
```

Open `http://127.0.0.1:8000` and verify:

- [ ] **Settings collapse**: Click "⚙ Settings" header → form opens. Click header again → form closes. Click "Save" → form closes. Invalid save → error stays visible (form does NOT close).
- [ ] **Pipeline log collapse**: Click "Pipeline ▼" header → log collapses. Click again → log expands.
- [ ] **Start→Stop**: Click "▶ Run" → button pair replaced with "⏹ Stop". Pipeline completes or click Stop → "▶ Run / ⚙ Dry Run" restored.
- [ ] **Enabled toggle**: Click "● enabled" or "● disabled" badge → state flips without a separate button.
- [ ] **No Enable/Disable button** in Actions column — only ✕ Delete.
- [ ] **Drag handles** visible (≡) on feed rows. Drag a row — order updates. Refresh page — order persists in config.yaml.
- [ ] **Config cache**: Check server logs — `load_config` called once at startup ("Config reloaded" only appears after external edits to config.yaml or a settings save).
