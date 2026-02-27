# Frontend UI Tweaks — Design

Date: 2026-02-27
Status: Approved

## Summary

A set of focused UX improvements to the HTMX/Tailwind frontend in `frontend/`. Approach: vanilla JS for client-side toggles + HTMX OOB swaps for button state. No new UI framework.

---

## Section 1: Collapsible Panels

### Settings

- The settings accordion button in `index.html` gets an `id="settings-toggle-btn"` and calls `toggleSettings()`.
- `toggleSettings()`: if `#settings-accordion` has no children, fetches `/settings` via `htmx.ajax` and injects the form. If it has children, clears the div (collapsing).
- `POST /settings`: returns **empty string** on success (collapsing the accordion); returns settings form HTML with error message on validation failure. Silent save — no green banner.
- Clicking the settings section header button also calls `toggleSettings()` (same handler).

### Pipeline Log

- The "Pipeline" section heading gets `onclick="togglePipelineLog()"` and a `▼/▶` chevron icon.
- `togglePipelineLog()`: toggles `display: none` on `#progress-section` and flips the icon.
- SSE streaming continues in the background even when the log is collapsed.

---

## Section 2: Start → Stop Button

### New state in `state.py`

```python
_pipeline_task: asyncio.Task | None = None

def set_task(task: asyncio.Task | None) -> None: ...
def get_task() -> asyncio.Task | None: ...
```

### Flow

1. `POST /pipeline/run`:
   - Creates background task, stores via `state.set_task(task)`.
   - Response: `progress.html` partial **plus** an OOB swap block:
     ```html
     <div id="pipeline-actions" hx-swap-oob="true">
       <button hx-post="/pipeline/stop" ...>⏹ Stop</button>
     </div>
     ```

2. `POST /pipeline/stop`:
   - Calls `state.get_task().cancel()`.
   - `_run_pipeline_task` finally block: sets running=False, enqueues None sentinel, detaches log handler.
   - Returns empty string (OOB swap not needed — browser restores buttons on SSE "done").

3. SSE `done` event (in `progress.html` JS):
   - Extended to call `htmx.ajax('GET', '/pipeline/actions', {target: '#pipeline-actions', swap: 'outerHTML'})`.
   - `GET /pipeline/actions` returns the Run / Dry Run button pair partial.

### New files / endpoints

- `frontend/templates/partials/pipeline_actions.html` — Run + Dry Run buttons.
- `GET /pipeline/actions` — renders that partial.
- `POST /pipeline/stop` — cancels task, returns 200 empty.

---

## Section 3: Enabled Label Toggle + Feed Reorder

### Enabled label

- In `feed_row.html`, the status `<span>` becomes an HTMX `<button>`:
  - `hx-put="/feeds/{name}/toggle"`, `hx-target="#feed-{slug}"`, `hx-swap="outerHTML"`
  - Displays "✓ enabled" (green) or "● disabled" (gray), `cursor-pointer`, hover opacity effect.
- The separate Enable/Disable button in the Actions column is removed.

### Drag-and-drop reorder

- SortableJS loaded from CDN: `https://cdn.jsdelivr.net/npm/sortablejs@1.15.3/Sortable.min.js`
- A `≡` drag handle cell is added as the first `<td>` in each feed row.
- `Sortable.create('#feed-tbody', { handle: '.drag-handle', animation: 150, onEnd })`.
- `onEnd`: collects `data-name` attributes from all rows in DOM order; sends `PUT /feeds/reorder` with JSON body `{"names": [...]}`.
- New `config_editor.reorder_feeds(names: list[str])` filters and reorders the feeds list in config.yaml.
- New route: `PUT /feeds/reorder` — parses JSON body, calls `reorder_feeds`, returns 200 empty.
- No response body required — DOM already reflects new order.

---

## Section 4: Config Caching with watchfiles

### New module: `frontend/config_cache.py`

```python
_config: AppConfig | None = None

def get_config() -> AppConfig: ...   # raises if not loaded
def set_config(cfg: AppConfig) -> None: ...
```

### Startup watcher

In `frontend/app.py`, replace `@app.on_event("startup")` with a FastAPI lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config(get_config_path())
    config_cache.set_config(cfg)
    task = asyncio.create_task(_watch_config())
    yield
    task.cancel()

async def _watch_config():
    async for _ in awatch(get_config_path()):
        cfg = load_config(get_config_path())
        config_cache.set_config(cfg)
```

### Route changes

All `load_config(get_config_path())` calls in `routes/pages.py`, `routes/feeds.py`, `routes/settings.py`, `routes/pipeline.py` are replaced with `config_cache.get_config()`.

### New dependency

```
uv add watchfiles
```

---

## Files Changed

| File | Change |
|---|---|
| `frontend/config_cache.py` | New — config cache module |
| `frontend/app.py` | Add lifespan, start watcher, import config_cache |
| `frontend/state.py` | Add `_pipeline_task` slot + accessors |
| `frontend/config_editor.py` | Add `reorder_feeds(names)` |
| `frontend/routes/pages.py` | Use `config_cache.get_config()` |
| `frontend/routes/feeds.py` | Use cache; add `PUT /feeds/reorder` |
| `frontend/routes/settings.py` | Use cache; POST returns empty on success |
| `frontend/routes/pipeline.py` | Add `GET /pipeline/actions`, `POST /pipeline/stop`; store task in state |
| `frontend/templates/index.html` | Toggle JS, settings accordion, pipeline header chevron |
| `frontend/templates/partials/feed_row.html` | Clickable badge, drag handle, remove Enable button |
| `frontend/templates/partials/feed_table.html` | Add drag handle column header |
| `frontend/templates/partials/settings_form.html` | Remove success banner |
| `frontend/templates/partials/progress.html` | OOB stop button in response; restore actions on SSE done |
| `frontend/templates/partials/pipeline_actions.html` | New — Run + Dry Run buttons partial |
| `pyproject.toml` | Add `watchfiles` dependency |

---

## Constraints

- No Alpine.js, no additional UI framework.
- All JS remains inline in templates (consistent with existing patterns).
- `POST /settings` collapses silently on success — no toast.
- SortableJS is the only new CDN script added.
