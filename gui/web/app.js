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
const clone = (v) => JSON.parse(JSON.stringify(v)); // structuredClone rejects Alpine's reactive proxies

/** Mixin: window listeners that are removed when the Alpine component is destroyed. */
const listeners = () => ({
  _off: [],
  on(type, fn) {
    window.addEventListener(type, fn);
    this._off.push(() => window.removeEventListener(type, fn));
  },
  destroy() { for (const off of this._off) off(); },
});

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
    ...listeners(),
    costs: { total: 0, by_model: [] },
    async init() {
      this.on("pipeline-event", (e) => {
        if (e.detail.type === "run.completed") this.loadCosts();
      });
      await this.loadCosts();
    },
    async loadCosts() {
      try { this.costs = (await api("/db/costs", { quiet: true })).data; } catch { /* keep last */ }
    },
    async run() { await api("/run", { method: "POST" }); Alpine.store("status").refresh(); },
    async stop(force) { await api(`/run/stop?force=${force}`, { method: "POST" }); Alpine.store("status").refresh(); },
    money(v) { return `$${(v ?? 0).toFixed(2)}`; },
  }));

  // ── Middle pane: episode list (always mounted, keeps filter/search/page across views) ──
  Alpine.data("episodeList", () => ({
    items: [], total: 0, offset: 0, limit: 50, q: "", feed: null, skipped: null,
    init() {
      let loaded = false;
      const apply = () => {
        const r = parseHash(location.hash);
        const keepFilter = r.view === "episode" || ["feeds", "settings", "logs"].includes(r.view);
        if (keepFilter && loaded) return; // opening a detail keeps the list's place
        loaded = true;
        if (keepFilter) { this.load(); return; } // first paint on a deep link: unfiltered list
        const feed = r.view === "feed" ? r.param : null;
        const skipped = r.view === "skipped" ? "true" : null;
        if (feed !== this.feed || skipped !== this.skipped) {
          this.feed = feed;
          this.skipped = skipped;
          this.offset = 0;
        }
        this.load();
      };
      window.addEventListener("hashchange", apply);
      window.addEventListener("episodes-changed", () => this.load());
      window.addEventListener("pipeline-event", (e) => {
        if (["episode.completed", "episode.failed"].includes(e.detail.type)) this.load();
      });
      apply();
    },
    async load() {
      const qs = new URLSearchParams({ limit: this.limit, offset: this.offset });
      if (this.feed) qs.set("feed", this.feed);
      if (this.skipped) qs.set("skipped", this.skipped);
      if (this.q) qs.set("q", this.q);
      try {
        const { data, headers } = await api(`/db/episodes?${qs}`, { quiet: true });
        this.items = data;
        this.total = Number(headers.get("X-Total-Count") ?? data.length);
        Alpine.store("titles").remember(data);
      } catch (err) {
        Alpine.store("toasts").push(`Episode list: ${err.message}`);
      }
    },
    search() { this.offset = 0; this.load(); },
    page(dir) { this.offset = Math.max(0, this.offset + dir * this.limit); this.load(); },
    get range() {
      return this.total ? `${this.offset + 1}–${Math.min(this.offset + this.limit, this.total)} of ${this.total}` : "0 of 0";
    },
    href(e) { return `#/episodes/${enc(e.guid)}`; },
    selected(e) { return Alpine.store("route").view === "episode" && Alpine.store("route").param === e.guid; },
    stateOf(e) {
      const st = Alpine.store("status");
      if (!st.idle && st.current_episode_guid === e.guid) {
        return { color: "var(--accent)", label: `${st.stage ?? "processing"}${st.percent !== null ? ` ${st.percent}%` : ""}` };
      }
      if (st.failed[e.guid]) return { color: "var(--fail)", label: "failed this run" };
      const colors = { complete: "var(--ok)", skipped: "var(--muted)" };
      return { color: colors[e.pipeline_state] ?? "var(--pending)", label: e.pipeline_state };
    },
  }));

  // ── Right pane: episode detail ──────────────────────────────────────────
  Alpine.data("episodeDetail", () => ({
    ...listeners(),
    guid: null, ep: null, segs: [], ads: [], topic: null, missing: {},
    duration: 0, current: 0, playing: false, stage: "",
    init() {
      this.on("hashchange", () => this.load());
      this.on("pipeline-event", (e) => {
        if (e.detail.payload?.guid === this.guid && ["episode.completed", "episode.failed"].includes(e.detail.type)) {
          this.load();
        }
      });
      this.load();
    },
    async load() {
      const r = parseHash(location.hash);
      if (r.view !== "episode") return;
      const guidChanged = r.param !== this.guid;
      this.guid = r.param;
      if (guidChanged) Object.assign(this, { ep: null, segs: [], ads: [], topic: null, duration: 0, current: 0, playing: false });
      const g = enc(this.guid);
      try {
        this.ep = (await api(`/db/episodes/${g}`)).data;
      } catch {
        return;
      }
      Alpine.store("titles").remember([this.ep]);
      const [t, a, tp] = await Promise.allSettled([
        api(`/db/transcriptions/${g}`, { quiet: true }),
        api(`/db/ads/${g}`, { quiet: true }),
        api(`/db/topics/${g}`, { quiet: true }),
      ]);
      this.segs = t.status === "fulfilled" ? t.value.data.segments : [];
      this.ads = a.status === "fulfilled" ? a.value.data.segments : [];
      this.topic = tp.status === "fulfilled" ? tp.value.data : null;
      this.missing = { transcript: t.status !== "fulfilled", ads: a.status !== "fulfilled", topic: tp.status !== "fulfilled" };
      // Until the audio reports its real length, the last transcript segment end stands in.
      if (guidChanged || !this.$refs.audio?.duration) this.duration = this.segs.at(-1)?.end ?? 0;
    },
    get blocks() { return timelineBlocks(this.ads, this.duration); },
    get ticks() { return [0, 0.25, 0.5, 0.75, 1].map((f) => ({ pct: `${f * 100}%`, label: fmtTime(f * this.duration) })); },
    get headPct() { return this.duration ? `${(100 * this.current) / this.duration}%` : "0%"; },
    get adTotal() { return this.ads.reduce((n, a) => n + (a.end_ms - a.start_ms), 0); },
    get skippedEp() { return this.ep?.skipped === 1 || this.ep?.pipeline_state === "skipped"; },
    adOf(seg) { return overlapsAd(seg, this.ads); },
    onMeta() { this.duration = this.$refs.audio.duration * 1000; },
    seek(ms) {
      const a = this.$refs.audio;
      if (!a) return;
      a.currentTime = ms / 1000;
      a.play();
    },
    seekClick(ev) {
      const rect = ev.currentTarget.getBoundingClientRect();
      this.seek(((ev.clientX - rect.left) / rect.width) * this.duration);
    },
    toggle() { const a = this.$refs.audio; if (a) (a.paused ? a.play() : a.pause()); },
    async act(fn) {
      try { await fn(); } catch { return; } // api() already toasted
      await this.load();
      window.dispatchEvent(new Event("episodes-changed"));
    },
    toggleSkip() {
      const g = enc(this.guid);
      return this.act(() => api(`/episodes/${g}/skip`, { method: this.skippedEp ? "DELETE" : "POST" }));
    },
    reprocess() {
      const qs = this.stage ? `?stage=${this.stage}` : "";
      return this.act(() => api(`/episodes/${enc(this.guid)}/reprocess${qs}`, { method: "POST" }));
    },
    remove() {
      if (!confirm(`Delete "${this.ep.title}"? Output file and processed data are removed.`)) return;
      return this.act(() => api(`/episodes/${enc(this.guid)}`, { method: "DELETE" }));
    },
  }));
  // ── Full view: settings ─────────────────────────────────────────────────
  Alpine.data("settingsView", () => ({
    stages: ["transcription", "context_extraction", "ad_detection"],
    loaded: null, form: null, params: {}, creds: {}, errors: [],
    async init() { await this.load(); },
    async load() {
      const { data } = await api("/settings");
      this.creds = data.credentials;
      delete data.credentials;
      this.loaded = data;
      this.form = clone(data);
      this.params = Object.fromEntries(this.stages.map((st) => [
        st,
        Object.entries(data.models[st].params ?? {}).map(([k, v]) => ({ k, v: typeof v === "string" ? v : JSON.stringify(v) })),
      ]));
      this.errors = [];
    },
    edited() {
      const e = clone(this.form);
      for (const st of this.stages) {
        const m = e.models[st];
        m.params = Object.fromEntries(this.params[st].filter((r) => r.k.trim()).map((r) => [r.k.trim(), parseParamValue(r.v)]));
        if (m.context_window === "" || m.context_window === undefined) m.context_window = null;
      }
      return e;
    },
    async save() {
      const patch = mergePatch(this.loaded, this.edited());
      if (!Object.keys(patch).length) {
        Alpine.store("toasts").push("No changes to save", "ok");
        return;
      }
      try {
        await api("/settings", { method: "PATCH", json: patch, quiet: true });
      } catch (err) {
        this.errors = Array.isArray(err.data) ? err.data.map((x) => `${x.loc.join(".")}: ${x.msg}`) : [err.message];
        return;
      }
      Alpine.store("toasts").push("Saved — applies at the next run", "ok");
      await this.load();
    },
    credFor(provider) { return this.creds[`${provider}_api_key`] ?? "not set"; },
  }));

  // ── Full view: feeds ────────────────────────────────────────────────────
  Alpine.data("feedsView", () => ({
    title: "", url: "",
    get items() { return Alpine.store("feeds").items; },
    async reload() { await Alpine.store("feeds").load(); },
    async patch(f, body) {
      try { await api(`/feeds/${f.slug}`, { method: "PATCH", json: body }); } finally { await this.reload(); }
    },
    async add() {
      try { await api("/feeds", { method: "POST", json: { title: this.title.trim(), url: this.url.trim() } }); } catch { return; }
      this.title = "";
      this.url = "";
      await this.reload();
    },
    async remove(f) {
      if (!confirm(`Remove feed "${f.title}" from the config? Its episodes and files stay on disk.`)) return;
      try { await api(`/feeds/${f.slug}`, { method: "DELETE" }); } finally { await this.reload(); }
    },
    async run(f) {
      await api(`/feeds/${f.slug}/run`, { method: "POST" });
      Alpine.store("status").refresh();
    },
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
