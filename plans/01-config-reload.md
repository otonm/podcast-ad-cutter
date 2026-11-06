# 01 — Reload Config at Run Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settings and feed changes made through the API take effect on the next pipeline run without restarting the server.

**Architecture:** The control router currently builds `Pipeline` from the `Config` captured when the server started. It will re-read `config.yaml` via `load_config()` on every run start. The DB router currently caches the feed list at router build time; it will read the feed list per request.

**Tech Stack:** aiohttp, pydantic, pytest + aiohttp `TestClient`.

**Spec:** [plans/README.md](README.md) — Decisions → Behaviour → G1.

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan).

## Review Focus

1. **Invalid YAML or a missing API key in `config.yaml` at run start:** expect 422 with the `ConfigError` message, and `run_state.state` stays `idle`. Tested in Task 1.
2. **A feed added via `POST /feeds` after startup, then run via `POST /feeds/{slug}/run`:** expect 202. Tested in Task 1.
3. **A feed added after startup, then `GET /db/episodes?feed=<new-slug>`:** expect its episodes. Tested in Task 2.
4. **`config.yaml` deleted while the server runs:** the DB router returns an empty feed list, as today, rather than a 500. Tested in Task 2.
5. **Run start blocking the event loop:** `load_config` does file IO and `load_dotenv`, so it runs via `asyncio.to_thread`. Covered by code review; the tests can't observe it.

---

### Task 1: Control router loads config per run

**Files:**
- Modify: `api/routes/control.py` (router factory signature, `start_run`, `start_feed_run`)
- Modify: `api/server.py` (`create_app` call to `create_control_router`)
- Test: `tests/test_api_control.py`

**Interfaces:**
- Consumes: `config.config_loader.load_config(config_path: Path) -> Config` (raises `utils.exceptions.ConfigError`).
- Produces:
  - `create_control_router(config_path: Path, db_path: Path, event_bus: EventBus, run_state: RunState) -> web.RouteTableDef`. Plan 04 extends this with `output_dir`.
  - `async def _load_run_config(config_path: Path) -> Config`, which raises `web.HTTPUnprocessableEntity` with JSON body `{"error": "<ConfigError message>"}`.
  - `create_app(...)` keeps its current signature and passes `config_path` and `config.app.paths.data_dir / "data.db"`.

- [ ] **Step 1: Add a test fixture for a real config file**

  In `tests/test_api_control.py`:
  - Add a module constant `_VALID_CONFIG_YAML`, a valid config with feeds `"My Show"` and `"Another Show"`, all three models on provider `groq`, and `models.ad_detection.model: "model-from-disk"`. Copy the shape from `config.example.yaml`.
  - Add a helper `_write_config(tmp_path: Path, text: str = _VALID_CONFIG_YAML) -> Path` that writes `tmp_path / "config.yaml"` and returns the path.

  Every test that posts to `/run` or `/feeds/{slug}/run` must:
  - call `monkeypatch.setenv("GROQ_API_KEY", "test")`;
  - write the config with `_write_config`.

- [ ] **Step 2: Write the failing tests**

```python
async def test_run_uses_config_reloaded_from_disk(tmp_path, monkeypatch):
    # app built with stale _make_config() mock; disk says "model-from-disk"
    ...
    with patch("api.routes.control.Pipeline") as mock_pipeline, patch(
        "api.routes.control.asyncio.create_task", return_value=MagicMock()):
        resp = await client.post("/api/v1/run")
    assert resp.status == 202
    assert mock_pipeline.call_args.args[0].app.models.ad_detection.model == "model-from-disk"

async def test_feed_run_finds_feed_added_after_startup(tmp_path, monkeypatch):
    # config on disk contains feed "New Show"; startup mock does not
    resp = await client.post("/api/v1/feeds/new-show/run")
    assert resp.status == 202

async def test_run_returns_422_and_stays_idle_when_config_invalid(tmp_path, monkeypatch):
    _write_config(tmp_path, "feeds: []\n")   # fails min_length=1
    resp = await client.post("/api/v1/run")
    assert resp.status == 422
    assert "error" in await resp.json()
    assert run_state.state == "idle"

async def test_feed_run_returns_422_when_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # patch("config.config_loader.load_dotenv") so a developer's .env can't re-supply the key
    resp = await client.post("/api/v1/feeds/my-show/run")
    assert resp.status == 422
```

- [ ] **Step 3: Run the tests to verify they fail**

  Run: `uv run pytest tests/test_api_control.py -v`

  Expected: the new tests FAIL. The model assertion fails because the mock config is used; the new-show run returns 404; invalid config returns 202.

- [ ] **Step 4: Implement**

  - In `create_control_router`, replace the `config` parameter with `config_path` and `db_path`. Drop the `db_path = config.app.paths.data_dir / "data.db"` line.
  - `_load_run_config` runs `await asyncio.to_thread(load_config, config_path)` and maps `ConfigError` to 422.
  - In both run handlers, **load the config before mutating `run_state`**, so a failed load leaves the state `idle`.
  - `start_feed_run` resolves the slug against the freshly loaded `cfg.app.feeds`.
  - Return errors with `web.json_response({"error": ...}, status=422)`, or raise `HTTPUnprocessableEntity(text=json.dumps(...), content_type="application/json")`. Never hand-format JSON with f-strings; the message may contain quotes.
  - Update `create_app` accordingly.

- [ ] **Step 5: Update existing tests in `tests/test_api_control.py` to the new contract**

  - Tests posting to run endpoints use `_write_config` + `monkeypatch.setenv`.
  - Skip and reprocess tests keep patching `api.routes.control.Database`.

- [ ] **Step 6: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, coverage 100%, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add api/routes/control.py api/server.py tests/test_api_control.py
git commit -m "fix(api): reload config.yaml at every run start"
```

### Task 2: DB router reads the feed list per request

**Files:**
- Modify: `api/routes/db.py` (replace the module-level `_feeds` load inside `create_db_router`)
- Test: `tests/test_api_db.py`

**Interfaces:**
- Produces: `def _load_feeds(config_path: Path) -> list[FeedConfig]`.
  - Returns `[]` on `FileNotFoundError`/`OSError`, as today.
  - Validates via `AppConfig.model_validate`.
  - Is called synchronously inside `get_episodes` and `get_costs`. The file is tiny, and this matches the existing settings route, which also reads it synchronously.

- [ ] **Step 1: Write the failing tests**

```python
async def test_episodes_feed_filter_sees_feed_added_after_startup(tmp_path):
    app, config_path = _make_app(tmp_path)                 # two feeds
    config_path.write_text(_TWO_FEEDS_YAML + <third feed "New Show">)
    <insert episode with podcast="New Show">
    resp = await client.get("/api/v1/db/episodes?feed=new-show")
    assert [e["podcast"] for e in await resp.json()] == ["New Show"]

async def test_episodes_feed_filter_returns_empty_when_config_deleted(tmp_path):
    app, config_path = _make_app(tmp_path)
    config_path.unlink()
    resp = await client.get("/api/v1/db/episodes?feed=my-show")
    assert resp.status == 200 and await resp.json() == []
```

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run pytest tests/test_api_db.py -v -k "after_startup or config_deleted"`

  Expected: the first test FAILS (empty list, because the feed list was cached at startup).

- [ ] **Step 3: Implement `_load_feeds` and call it per request; remove the startup `_feeds` block**

- [ ] **Step 4: Verify**

  Run: `uv run pytest && uv run pytest --cov=. && uv run ruff check .`

  Expected: all pass, 100%, clean.

- [ ] **Step 5: Document and commit**

  - In `API.md` §2.4 (Settings), add: "Changes apply at the next run start; a running pipeline keeps the config it started with."
  - In §2.2, document 422 on `POST /run` and `POST /feeds/{slug}/run` when `config.yaml` fails to load.

```bash
git add api/routes/db.py tests/test_api_db.py API.md
git commit -m "fix(api): read feed list per request in DB viewer routes"
```
