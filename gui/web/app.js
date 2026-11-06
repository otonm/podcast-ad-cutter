import { fmtDate, fmtTime, mergePatch, overlapsAd, parseHash, parseParamValue, timelineBlocks } from "./lib.js";

const EVENT_TYPES = [
  "run.started", "run.completed", "episode.stage_changed", "episode.download_progress",
  "episode.encode_progress", "episode.completed", "episode.failed",
];
const MAX_EVENTS = 50;

/** fetch wrapper: prefixes /api/v1, sends opts.json as a JSON body, toasts and throws on non-2xx. */
export async function api(path, opts = {}) {
  const init = { method: opts.method ?? "GET", headers: {} };
  if (opts.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  }
  const resp = await fetch(`/api/v1${path}`, init);
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!resp.ok) {
    const msg = data?.error ?? (typeof data === "string" ? data : JSON.stringify(data)) ?? resp.statusText;
    const err = new Error(msg);
    err.status = resp.status;
    err.data = data;
    if (!opts.quiet) Alpine.store("toasts").push(msg);
    throw err;
  }
  return { data, headers: resp.headers };
}

const enc = encodeURIComponent;

document.addEventListener("alpine:init", () => {
  Alpine.store("toasts", {
    items: [],
    push(msg, kind = "error") {
      const item = { id: Math.random(), msg, kind };
      this.items.push(item);
      setTimeout(() => { this.items = this.items.filter((t) => t !== item); }, 6000);
    },
  });

  Alpine.store("route", {
    view: "overview",
    param: null,
    get full() { return ["feeds", "settings", "logs"].includes(this.view); },
    sync() { Object.assign(this, parseHash(location.hash)); },
  });

  Alpine.store("feeds", {
    items: [],
    get total() { return this.items.reduce((n, f) => n + f.episode_count, 0); },
    async load() { this.items = (await api("/feeds", { quiet: true })).data; },
  });

  // GUID → title cache for event text and the status bar (filled by list and detail loads).
  Alpine.store("titles", {
    map: {},
    remember(eps) { for (const e of eps) this.map[e.guid] = e.title; },
    of(guid) { return this.map[guid] ?? guid; },
  });

  Alpine.store("status", {
    state: "idle", started_at: null, active_feed_slug: null, current_episode_guid: null, feeds: {},
    sse: "disconnected", stage: null, percent: null, failed: {}, events: [],
    get idle() { return this.state === "idle"; },
    get done() { return Object.values(this.feeds).reduce((n, f) => n + f.episodes_done, 0); },
    get total() { return Object.values(this.feeds).reduce((n, f) => n + f.episodes_total, 0); },
    get failedCount() { return Object.keys(this.failed).length; },
    async refresh() {
      try { Object.assign(this, (await api("/status", { quiet: true })).data); } catch { /* shown via sse flag */ }
      if (this.current_episode_guid && !(this.current_episode_guid in Alpine.store("titles").map)) {
        try {
          Alpine.store("titles").remember([(await api(`/db/episodes/${enc(this.current_episode_guid)}`, { quiet: true })).data]);
        } catch { /* title stays the guid */ }
      }
    },
    connect() {
      const es = new EventSource("/api/v1/events");
      es.onopen = () => { this.sse = "connected"; this.refresh(); };
      es.onerror = () => { this.sse = "disconnected"; };
      for (const type of EVENT_TYPES) {
        es.addEventListener(type, (e) => this.onEvent(type, JSON.parse(e.data)));
      }
    },
    log(text) {
      this.events.unshift({ t: new Date().toLocaleTimeString(), text });
      this.events.splice(MAX_EVENTS);
    },
    onEvent(type, p) {
      const title = () => Alpine.store("titles").of(p.guid);
      switch (type) {
        case "run.started":
          this.failed = {};
          this.log(`Run started · ${p.feeds.length} feed(s) · ${p.total_episodes} episode(s)`);
          break;
        case "run.completed":
          this.log("Run completed");
          break;
        case "episode.stage_changed":
          this.stage = p.stage;
          this.percent = null;
          this.log(`${title()} → ${p.stage} ${p.status}`);
          break;
        case "episode.download_progress":
        case "episode.encode_progress":
          this.percent = Math.round(p.percent * 100);
          return; // too frequent for the event list, no state refresh needed
        case "episode.completed":
          this.log(`${title()} complete (${p.outcome})`);
          break;
        case "episode.failed":
          this.failed[p.guid] = true;
          this.log(`${title()} failed — ${p.error}`);
          break;
      }
      this.refresh();
      window.dispatchEvent(new CustomEvent("pipeline-event", { detail: { type, payload: p } }));
    },
  });

  // ── Right pane: run overview ──────────────────────────────────────────────
  Alpine.data("overview", () => ({
    costs: { total: 0, by_model: [] },
    async init() {
      await this.loadCosts();
      window.addEventListener("pipeline-event", (e) => {
        if (e.detail.type === "run.completed") this.loadCosts();
      });
    },
    async loadCosts() {
      try { this.costs = (await api("/db/costs", { quiet: true })).data; } catch { /* keep last */ }
    },
    async run() { await api("/run", { method: "POST" }); Alpine.store("status").refresh(); },
    async stop(force) { await api(`/run/stop?force=${force}`, { method: "POST" }); Alpine.store("status").refresh(); },
    money(v) { return `$${(v ?? 0).toFixed(2)}`; },
  }));
});

document.addEventListener("alpine:initialized", () => {
  Alpine.store("route").sync();
  window.addEventListener("hashchange", () => Alpine.store("route").sync());
  Alpine.store("feeds").load();
  Alpine.store("status").connect();
});

// Helpers used directly in templates.
window.fmt = { date: fmtDate, time: fmtTime };
