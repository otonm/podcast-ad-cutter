# 07 — GUI Container Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A separate `podcast-ad-cutter-gui` image that serves a browser UI for monitoring runs, managing episodes and feeds, viewing transcripts and ad analysis, editing settings, and tailing logs.

**Architecture:**
- `gui/` holds a Caddy image. Caddy serves static files from `/srv` and reverse-proxies `/api/*` to the backend container. One origin, no CORS.
- The frontend is plain HTML + Alpine.js (vendored, pinned), with no build step: one `index.html`, one `app.js`, one `style.css`.
- The layout is **direction 5 "Three-Pane"**:
  - feeds sidebar | episode list | detail pane;
  - a status bar at the bottom fed by SSE on every screen;
  - the large audio timeline from direction 3 in the episode detail pane.

**Tech Stack:** Caddy 2 (pinned image tag), Alpine.js 3 (pinned file), browser `EventSource`/`fetch`.

**Spec:** [plans/README.md](README.md) — Architecture.

**Visual reference:** https://claude.ai/artifact/178BxXfHbAHufgYVUBk9h4
- Row 5, artboards "5 · Three-Pane — Overview" and "— Episode detail".
- Row 3, "3 · Timeline — Episode detail", for the timeline bar only.

**Depends on:**
- Plans 01–05 (the API endpoints it calls, including plan 05's `skipped` filter).
- Plan 06 (the compose layout).

## Global Constraints

See [README.md → Global constraints](README.md#global-constraints-apply-to-every-plan). Additionally:
- **No build tooling**: no npm, no bundler, no CDN at runtime.
  - Vendor `alpine.min.js` into `gui/web/vendor/` at an exact version, recorded in a comment in `index.html`.
  - Vendor Inter (woff2, 400/600/700) into `gui/web/vendor/` too. No Google Fonts call at runtime.
- **Design tokens** (from the mockup), defined as CSS custom properties on `:root` in `style.css`:

  | Token | Value |
  |---|---|
  | `--bg` | `#ffffff` |
  | `--side` | `#f7f8fa` |
  | `--line` | `#e5e7eb` |
  | `--line-soft` | `#eef0f4` |
  | `--text` | `#111827` |
  | `--muted` | `#6b7280` |
  | `--accent` | `#4338ca` |
  | `--accent-soft` | `#e8e9fb` |
  | `--ok` | `#15803d` |
  | `--fail` | `#c2410c` |
  | `--ad` | `#ea580c` |
  | `--ad-soft` | `#fff1e6` |
  | `--ad-text` | `#9a3412` |

  - Font: Inter. Radius 6–8 px. Controls at least 44 px tall.
  - Dark mode is out of scope: the mockup is light only. Add it when wanted, by overriding the tokens under `prefers-color-scheme`.
- **No JS unit tests.** Python coverage rules don't apply to `gui/`. Each task is verified by its manual checklist. Add Playwright only if regressions start slipping through.
- Every API path is built with `encodeURIComponent(guid)` (plan 05, Review Focus 5).
- The event stream uses **named events** (`event: episode.stage_changed`).
  - The frontend must use `es.addEventListener("<type>", …)`; `onmessage` receives nothing.
  - Log tail uses unnamed `data:` frames, so `onmessage` is correct there.
- Mutations return 409 while a run is active. The UI disables mutation buttons when `status.state !== "idle"` and still shows any 409 error body as a toast.
- **"Failed" is not stored by the backend.** The list shows "failed this run" only for GUIDs seen in an `episode.failed` event during this browser session. The DB-derived `pipeline_state` stays authoritative.
- Before writing the Caddyfile and the Alpine code, look up current docs (Context7: `/websites/caddyserver_caddyfile`, Alpine.js).
- At phone width the panes stack: sidebar collapses to a top row, list above detail. Use `flex-wrap` as in the mockup.

## Review Focus

1. **Transcript timestamps don't match the cut audio**, because ads were removed. The detail pane's timeline and player use the **original** (`source_url`). The cut file (`url`, when `pipeline_state === "complete"`) is a plain link or player without seeking. Verified in Task 3.
2. **SSE reconnect after a backend restart:** `EventSource` auto-reconnects. The status bar must re-fetch `/status` on `open` and show "SSE disconnected" between `error` and `open`. Verified in Task 2.
3. **Removing a model param in the settings form** must send `null` for the removed key (plan 03 Merge Patch). Verified in Task 4.
4. **A 422 from `PATCH /settings`** (Pydantic error array) must show field paths (`loc`) and messages. Verified in Task 4.
5. **Caddy buffering SSE:** events must arrive live through the proxy. Verified in Task 1 with `curl -N`.

## File Structure

```
gui/
  Dockerfile          # FROM caddy:<pinned>; COPY Caddyfile; COPY web /srv
  Caddyfile           # :80 — /api/* → backend, else file_server
  web/
    index.html        # three-pane shell, status bar, view templates
    app.js            # api() helper, stores, router, view components
    style.css         # tokens + layout
    vendor/alpine.min.js
    vendor/inter-*.woff2
```

## Routes (hash) → what each pane shows

| Hash | Sidebar highlight | Middle pane | Right pane |
|---|---|---|---|
| `#/` | All episodes | episode list (all) | Run overview |
| `#/feed/<slug>` | that feed | list filtered `?feed=<slug>` | Run overview |
| `#/skipped` | Skipped | list `?skipped=true` | Run overview |
| `#/episodes/<guid>` | (unchanged) | list stays as it was | Episode detail |
| `#/feeds`, `#/settings`, `#/logs` | that entry | hidden | full width (middle + right) |

The middle pane remembers its filter, search and page across hash changes, so opening an episode keeps your place in the list.

---

### Task 1: Container skeleton, proxy, and CI

**Files:**
- Create: `gui/Dockerfile`, `gui/Caddyfile`, `gui/web/index.html` (placeholder that calls `/api/v1/health`), `gui/web/vendor/alpine.min.js`, `gui/web/vendor/inter-*.woff2`
- Modify: `.dockerignore` (add `gui/`, so the backend image doesn't ship it)
- Modify: `.github/workflows/docker-publish.yml` (second build: context `gui`, image `ghcr.io/${{ github.repository }}-gui`, tags `latest` + `sha-…`, cache scope `gui`)
- Modify: `docker-compose.example.yml` (add the `gui` service: image `ghcr.io/otonm/podcast-ad-cutter-gui:latest`, `ports: ["127.0.0.1:8090:80"]`, `depends_on: [podcast-ad-cutter]`)

**Interfaces:**
- Produces:
  - The Caddyfile listens on `:80` (no automatic HTTPS).
  - `/api/*` is proxied to `{$API_UPSTREAM:podcast-ad-cutter:8080}`. Check the env-placeholder syntax in the Caddy docs. Use `handle`, **not** `handle_path`: the backend expects the `/api/v1` prefix.
  - Everything else goes to `root * /srv` + `file_server`.

- [ ] **Step 1: Write `gui/Caddyfile`, `gui/Dockerfile`, the placeholder `index.html`, and vendor Alpine and Inter**

  Record the versions and source URLs in an HTML comment.

- [ ] **Step 2: Build and run the stack**

  Run: `docker build -t pac-gui:test gui/ && docker compose -f <local compose using pac:test + pac-gui:test> up -d`

  Expected: both containers are running.

- [ ] **Step 3: Verify proxy and static**

  Run: `curl -fsS http://127.0.0.1:8090/api/v1/health && curl -fsS http://127.0.0.1:8090/ | head -5`

  Expected: health JSON `{"status": "ok", …}`, then the placeholder HTML.

- [ ] **Step 4: Verify SSE is not buffered (Review Focus 5)**

  Run `curl -N http://127.0.0.1:8090/api/v1/events` in one shell. In another, run `curl -X POST http://127.0.0.1:8090/api/v1/run`.

  Expected: `event: run.started` appears within about 1 s of the POST.

- [ ] **Step 5: Commit**

```bash
git add gui/ .dockerignore .github/workflows/docker-publish.yml docker-compose.example.yml
git commit -m "feat(gui): add Caddy-based GUI container proxying the API"
```

### Task 2: Three-pane shell, status bar, Run overview

**Files:**
- Modify: `gui/web/index.html`
- Create: `gui/web/app.js`, `gui/web/style.css`

**Interfaces:**
- Consumes:
  - `GET /status`
  - `POST /run`, `POST /run/stop?force=true|false`
  - `GET /feeds` (title, slug, episode count)
  - `GET /db/costs`
  - SSE `/events` with events `run.started`, `run.completed`, `episode.stage_changed`, `episode.download_progress`, `episode.encode_progress`, `episode.completed`, `episode.failed`.
- Produces (used by Tasks 3–5):
  - `async function api(path: string, opts?: RequestInit & {json?: unknown}): Promise<{data, headers}>`
    - Prefixes `/api/v1`.
    - Sends a JSON body when `opts.json` is set.
    - On a non-2xx response, throws `Error(<error field or JSON text>)` and pushes a toast.
  - `Alpine.store("status")`: `{ state, started_at, active_feed_slug, current_episode_guid, feeds, sse: "connected"|"disconnected", stage, percent, failedGuids: Set<string>, events: Array(≤50), get idle() }`.
    - Refreshed on `run.started`, `run.completed`, `episode.completed` and `episode.failed`, and on SSE `open`.
    - `stage`/`percent` come from `stage_changed` / `*_progress`.
  - `Alpine.store("feeds")`: list from `/feeds`, refreshed after any feed mutation.
  - `Alpine.store("toasts")`: `push(msg, kind)`.
  - `Alpine.store("route")`: `{ view, param }`, parsed from `location.hash` per the Routes table.

- [ ] **Step 1: Implement the shell**

  - **Sidebar:**
    - "All episodes" with the total (sum of feed counts).
    - One link per feed with its count.
    - A separator, then Skipped, Feeds, Settings, Logs.
    - The active entry uses `--accent-soft`/`--accent`.
  - **Status bar** (always visible):
    - state dot and label, plus the active feed;
    - current episode title, stage and percent;
    - overall progress bar (sum done / sum total over `status.feeds`);
    - "SSE connected/disconnected".

- [ ] **Step 2: Implement the Run overview (right pane)**

  - Four stat tiles: State, Progress (done/total), Failed (this run), LLM cost (`/db/costs` total).
  - Buttons:
    - Run all, only when idle.
    - Stop after episode and Force stop, only when not idle.
  - Event stream: the last 50 events, newest first, as time + one-line text.
  - Below it: cost by model (`by_model`).

- [ ] **Step 3: Manual checklist** (against the compose stack)

  - [ ] The page loads with no console errors, and the sidebar counts match `/feeds`.
  - [ ] Run all → the status bar shows running, and events stream into the overview.
  - [ ] Stop → stopping, then idle; the buttons swap.
  - [ ] `docker restart` of the backend → the status bar shows "SSE disconnected", then reconnects with correct state (Review Focus 2).
  - [ ] Run from a second tab while running → toast "a run is already active".
  - [ ] At 390 px width, the panes stack and nothing scrolls horizontally.

- [ ] **Step 4: Commit**

```bash
git add gui/web
git commit -m "feat(gui): three-pane shell with live status bar and run overview"
```

### Task 3: Episode list (middle pane) + Episode detail (right pane)

**Files:**
- Modify: `gui/web/index.html`, `gui/web/app.js`, `gui/web/style.css`

**Interfaces:**
- Consumes:
  - `GET /db/episodes?feed=&q=&skipped=&limit=&offset=` (body array plus the `X-Total-Count` header)
  - `GET /db/episodes/{guid}`, `GET /db/transcriptions/{guid}`, `GET /db/ads/{guid}`, `GET /db/topics/{guid}` (404 → "not yet available")
  - `POST /episodes/{guid}/skip`, `DELETE /episodes/{guid}/skip`, `POST /episodes/{guid}/reprocess?stage=`, `DELETE /episodes/{guid}`
- Produces:
  - `function overlapsAd(seg: {start, end}, ads: {start_ms, end_ms}[]): object | null`. Transcript segments use `start`/`end` in ms; ad segments use `start_ms`/`end_ms`.
  - `function timelineBlocks(ads, durationMs): {left: string, width: string}[]`. Returns percentages.

- [ ] **Step 1: Implement the list (middle pane)**

  - Search box (debounced 300 ms) and the filter from the route.
  - Each row: date (`DD.MM.`), title, and a state dot plus label. Colors:
    - running = `--accent` (the GUID equals `status.current_episode_guid`; show the stage and percent);
    - complete = `--ok`;
    - failed this run = `--fail`;
    - pending = `#9ca3af`;
    - skipped = `--muted`.
  - The selected row uses `--accent-soft`.
  - Pager: "x–y of N" from `X-Total-Count`, with prev/next; 50 per page.
  - Refresh the current page on `episode.completed` and `episode.failed`.

- [ ] **Step 2: Implement the detail (right pane, `#/episodes/<guid>`)**

  - **Header:** title, then `DD.MM.YYYY · original duration → cut duration · state`.
  - **Actions:** Disable/Enable (skip/unskip), Reprocess… (stage `<select>`: full, download, transcribe, topic, ad-detect, edit), Delete.
    - Delete asks `confirm("Delete … ? Output file and processed data are removed.")`.
    - All actions are disabled unless `$store.status.idle`.
    - After an action, re-fetch the detail and the list page.
  - **Timeline bar** (direction 3):
    - a full-width, 72 px high track;
    - orange `--ad` blocks from `timelineBlocks`;
    - a playhead tied to the original `<audio src=source_url>`;
    - tick labels at 0, ¼, ½, ¾ and the end;
    - a play/pause button and "current / total original".
    - Clicking the track seeks.
    - Duration: use `audio.duration` after `loadedmetadata`; before that, the last transcript segment's `end`.
  - **Two columns below the timeline:**
    - Transcript (rows `mm:ss` + text). Rows where `overlapsAd` is not null get `--ad-soft` and a sponsor tooltip. Clicking a row seeks the original audio to `start/1000`.
    - Ads list (sponsor · confidence, range in `--ad-text`), then the topic (show, hosts, topic).
  - A "Cut version" link to `url`, only when `pipeline_state === "complete"`, with no seeking (Review Focus 1).

- [ ] **Step 3: Manual checklist**

  - [ ] Search narrows the list and the total updates; paging shows the correct "of N".
  - [ ] The sidebar feed and Skipped filters work.
  - [ ] Opening an episode keeps the list's filter and page.
  - [ ] Disable → the row shows skipped and it appears under Skipped; Enable reverses it.
  - [ ] Delete → the detail shows no transcript or ads, and the row is skipped.
  - [ ] Processed episode: the ad blocks line up with the highlighted transcript rows; clicking a block, or a highlighted row, plays the original at the ad.
  - [ ] An episode whose GUID is a URL opens correctly.

- [ ] **Step 4: Commit**

```bash
git add gui/web
git commit -m "feat(gui): episode list and detail pane with ad timeline"
```

### Task 4: Settings + Feeds (full-width views)

**Files:**
- Modify: `gui/web/index.html`, `gui/web/app.js`

**Interfaces:**
- Consumes:
  - `GET /settings` (includes a `credentials` set/not-set map)
  - `PATCH /settings` (Merge Patch; 422 body is a Pydantic error array with `loc`, `msg`)
  - `GET /feeds`, `POST /feeds`, `PATCH /feeds/{slug}`, `DELETE /feeds/{slug}`, `POST /feeds/{slug}/run`
- Produces:
  - `function mergePatch(before: object, after: object): object`. Returns changed leaves, plus `null` for keys present in `before` but missing in `after`.

- [ ] **Step 1: Implement Settings**

  - For each of `transcription`, `context_extraction`, `ad_detection`:
    - provider `<select>` (groq/openai/openrouter), with the credential status next to it;
    - model;
    - `context_window` (empty → omitted);
    - params editor: key/value rows, value parsed as JSON with a raw-string fallback, plus an add-row button.
  - `ad_detection.min_duration` / `min_confidence`.
  - `output.file_type` / `bitrate`.
  - `log.*`.
  - `base_url`.
  - Save sends `mergePatch(loaded, edited)`. On 422, list `loc.join(".")`: `msg`.
  - Banner: "Changes apply at the next run."

- [ ] **Step 2: Implement Feeds**

  - Table: title, URL, enabled toggle (PATCH), `episodes_to_keep` (PATCH on blur), episode count, Run feed (idle only), Delete (confirm).
  - Add-feed form: title, URL.
  - Refresh `Alpine.store("feeds")` after each change, so the sidebar updates.

- [ ] **Step 3: Manual checklist**

  - [ ] Change the ad_detection model, save, reload → it persists, and `config/config.yaml` on the host shows it.
  - [ ] Add param `temperature: 0.1`, save; remove it, save → it is gone from `config.yaml`.
  - [ ] Set `min_confidence` to 2 → the error `ad_detection.min_confidence: …` is shown.
  - [ ] Add a feed → it appears in the table and the sidebar; Run feed → 202 (plan 01).
  - [ ] Toggle enabled → persists.

- [ ] **Step 4: Commit**

```bash
git add gui/web
git commit -m "feat(gui): settings with model params, and feed management"
```

### Task 5: Logs (full-width view)

**Files:**
- Modify: `gui/web/index.html`, `gui/web/app.js`

**Interfaces:**
- Consumes: `GET /logs` (`app_logs[]`, `episode_logs{feed: []}`) and SSE `GET /logs/{path}/tail?bytes=16384` (unnamed `data:` frames).

- [ ] **Step 1: Implement**

  - Left: file list grouped as App / per feed. Right: a `<pre>` tail.
  - Close the previous `EventSource` when switching files and when leaving the view.
  - Cap at 2,000 lines.
  - Auto-scroll unless the user has scrolled up.
  - Empty list → "Enable `log.to_file` in Settings to get app logs."

- [ ] **Step 2: Manual checklist**

  - [ ] With `log.to_file: true`, start a run → lines appear live.
  - [ ] Switching files leaves exactly one tail stream open (DevTools → Network).
  - [ ] Leaving the view closes it.

- [ ] **Step 3: Commit**

```bash
git add gui/web
git commit -m "feat(gui): live log viewer"
```
