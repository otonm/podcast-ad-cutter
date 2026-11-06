// Pure helpers for the GUI — no DOM, no fetch. Tested by gui/tests/lib.test.js (node --test).

const pad = (n) => String(n).padStart(2, "0");

/** Milliseconds → "m:ss", or "h:mm:ss" from one hour up. */
export function fmtTime(ms) {
  const total = Math.max(0, Math.floor((ms ?? 0) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** ISO date → "DD.MM.YYYY" (local time, like the output file names), or "DD.MM." when short. */
export function fmtDate(iso, short = false) {
  if (!iso) return "";
  const d = new Date(iso);
  const day = `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.`;
  return short ? day : `${day}${d.getFullYear()}`;
}

/** First ad segment overlapping a transcript segment, or null. Edges that only touch do not overlap. */
export function overlapsAd(seg, ads) {
  return ads.find((a) => seg.start < a.end_ms && a.start_ms < seg.end) ?? null;
}

/** Ad segments → CSS percentage boxes on a timeline of durationMs. */
export function timelineBlocks(ads, durationMs) {
  if (!durationMs) return [];
  const pct = (v) => `${+((v / durationMs) * 100).toFixed(3)}%`;
  return ads.map((a) => ({ left: pct(a.start_ms), width: pct(a.end_ms - a.start_ms) }));
}

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** Minimal JSON Merge Patch (RFC 7396) turning `before` into `after`; removed keys become null. */
export function mergePatch(before, after) {
  const patch = {};
  for (const key of Object.keys(before)) {
    if (!(key in after)) patch[key] = null;
  }
  for (const [key, val] of Object.entries(after)) {
    const old = before[key];
    if (isObj(old) && isObj(val)) {
      const sub = mergePatch(old, val);
      if (Object.keys(sub).length) patch[key] = sub;
    } else if (JSON.stringify(old) !== JSON.stringify(val)) {
      patch[key] = val;
    }
  }
  return patch;
}

const FULL_VIEWS = new Set(["feeds", "settings", "logs"]);

/** location.hash → { view, param }. Unknown hashes fall back to the overview. */
export function parseHash(hash) {
  const [, first = "", ...rest] = (hash || "").replace(/^#/, "").split("/");
  const param = rest.length ? decodeURIComponent(rest.join("/")) : null;
  if (first === "feed" && param) return { view: "feed", param };
  if (first === "episodes" && param) return { view: "episode", param };
  if (first === "skipped") return { view: "skipped", param: null };
  if (FULL_VIEWS.has(first)) return { view: first, param: null };
  return { view: "overview", param: null };
}

/** Settings params editor value: JSON when it parses, else the raw string. */
export function parseParamValue(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
