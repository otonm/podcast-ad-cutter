import assert from "node:assert/strict";
import { test } from "node:test";

import {
  fmtDate, fmtTime, mergePatch, overlapsAd, parseHash, parseParamValue, persistentEventSource, timelineBlocks,
} from "../web/lib.js";

test("fmtTime formats ms as m:ss and h:mm:ss", () => {
  assert.equal(fmtTime(0), "0:00");
  assert.equal(fmtTime(65_000), "1:05");
  assert.equal(fmtTime(3_725_000), "1:02:05");
});

test("fmtDate formats ISO dates as DD.MM.YYYY and short DD.MM.", () => {
  assert.equal(fmtDate("2026-03-21T12:00:00+00:00"), "21.03.2026");
  assert.equal(fmtDate("2026-03-21T12:00:00+00:00", true), "21.03.");
  assert.equal(fmtDate(null), "");
});

test("overlapsAd returns the first overlapping ad or null", () => {
  const ads = [{ start_ms: 1000, end_ms: 2000, sponsor: "A" }, { start_ms: 5000, end_ms: 6000, sponsor: "B" }];
  assert.equal(overlapsAd({ start: 1500, end: 2500 }, ads).sponsor, "A");
  assert.equal(overlapsAd({ start: 2000, end: 3000 }, ads), null); // touching edge is not overlap
  assert.equal(overlapsAd({ start: 4000, end: 5001 }, ads).sponsor, "B");
});

test("timelineBlocks converts ad ranges to percentages", () => {
  assert.deepEqual(timelineBlocks([{ start_ms: 1000, end_ms: 2000 }], 10_000), [{ left: "10%", width: "10%" }]);
  assert.deepEqual(timelineBlocks([{ start_ms: 1000, end_ms: 2000 }], 0), []);
});

test("mergePatch returns changed leaves and null for removed keys", () => {
  const before = { a: 1, m: { p: { t: 0.2, top: 0.9 }, keep: "x" }, list: [1] };
  const after = { a: 1, m: { p: { t: 0.3 }, keep: "x" }, list: [1, 2] };
  assert.deepEqual(mergePatch(before, after), { m: { p: { t: 0.3, top: null } }, list: [1, 2] });
  assert.deepEqual(mergePatch(before, structuredClone(before)), {});
  assert.deepEqual(mergePatch({ a: { b: 1 } }, {}), { a: null });
  assert.deepEqual(mergePatch({}, { a: { b: 1 } }), { a: { b: 1 } });
});

test("parseHash maps hashes to views", () => {
  assert.deepEqual(parseHash(""), { view: "overview", param: null });
  assert.deepEqual(parseHash("#/"), { view: "overview", param: null });
  assert.deepEqual(parseHash("#/feed/tech-weekly"), { view: "feed", param: "tech-weekly" });
  assert.deepEqual(parseHash("#/skipped"), { view: "skipped", param: null });
  assert.deepEqual(parseHash("#/episodes/" + encodeURIComponent("http://x/ep/1")), { view: "episode", param: "http://x/ep/1" });
  assert.deepEqual(parseHash("#/settings"), { view: "settings", param: null });
  assert.deepEqual(parseHash("#/nonsense"), { view: "overview", param: null });
});

test("parseParamValue parses JSON with raw-string fallback", () => {
  assert.equal(parseParamValue("0.2"), 0.2);
  assert.equal(parseParamValue("true"), true);
  assert.deepEqual(parseParamValue('{"effort":"high"}'), { effort: "high" });
  assert.equal(parseParamValue("high"), "high");
});

test("persistentEventSource re-creates the stream only after the browser gives up", () => {
  const opened = [];
  const timers = [];
  const states = [];
  const open = () => { const es = { readyState: 0, close() { this.readyState = 2; } }; opened.push(es); return es; };
  persistentEventSource(open, {
    onOpen: () => states.push("up"), onDown: () => states.push("down"), setTimer: (fn) => timers.push(fn),
  });
  assert.equal(opened.length, 1);
  opened[0].onerror();                 // CONNECTING: the browser retries by itself
  assert.equal(timers.length, 0);
  opened[0].readyState = 2;            // CLOSED, e.g. a 502 from the proxy during a restart
  opened[0].onerror();
  assert.equal(timers.length, 1);
  timers[0]();
  assert.equal(opened.length, 2);
  opened[1].onopen();
  assert.deepEqual(states, ["down", "down", "up"]);
});
