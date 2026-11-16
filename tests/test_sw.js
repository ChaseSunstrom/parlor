// node tests/test_sw.js: the service worker (app/static/sw.js, filled in as pwa.py does) against stand-in caches and
// a stand-in network: it keeps the app's files at install, never touches /api, streams or music apps, serves the
// server's copy when there is one and the kept one when the server is down, erring or slow, drops old versions,
// keeps Google's fonts, and its off switch removes it and everything it kept.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vm = require("vm");

const TEMPLATE = fs.readFileSync(path.join(__dirname, "..", "app", "static", "sw.js"), "utf8");
const ORIGIN = "https://parlor.test";
const SHELL = ["./", "app.css", "js/core.js", "icon-192.png"];

function res(body, { status = 200, type = "basic" } = {}) {
  return { body, status, ok: status >= 200 && status < 300, type, clone() { return res(body, { status, type }); } };
}

function worker({ version = "v1", off = false, net = {}, caches: start = {} } = {}) {
  const src = TEMPLATE.replace('"__VERSION__"', JSON.stringify(version)).replace("__SHELL__", JSON.stringify(SHELL)).replace("__OFF__", JSON.stringify(off));
  const stores = new Map(Object.entries(start).map(([k, v]) => [k, new Map(Object.entries(v))]));
  const key = (r) => new URL(typeof r === "string" ? r : r.url, ORIGIN + "/sw.js").href;
  const store = (name) => ({
    async match(r) { return stores.get(name).get(key(r)); },
    async put(r, v) { stores.get(name).set(key(r), v); },
    async addAll(list) { for (const u of list) { const r = await network({ url: key(u) }, u); if (!r.ok) throw new Error(`can't keep ${u}`); stores.get(name).set(key(u), r); } },
  });
  const handlers = {}, fetched = [], asked = [], state = { skipped: false, claimed: false, unregistered: false, navigated: [] };
  // the network: url -> response, "down" (fails), or "hang" (never answers)
  const network = (req, init) => {
    fetched.push(req.url);
    if (new URL(req.url).origin === ORIGIN) asked.push(init?.cache); // how the browser's own kept copy is treated
    const r = net[req.url] ?? net["*"] ?? res("from the server");
    return r === "down" ? Promise.reject(new TypeError("Failed to fetch")) : r === "hang" ? new Promise(() => {}) : Promise.resolve(r);
  };
  const self = {
    addEventListener: (t, f) => { handlers[t] = f; },
    skipWaiting: () => { state.skipped = true; },
    registration: { unregister: async () => { state.unregistered = true; } },
    clients: { claim: async () => { state.claimed = true; }, matchAll: async () => [{ url: ORIGIN + "/#/music", navigate(u) { state.navigated.push(u); } }] },
  };
  const ctx = {
    self, URL, location: new URL(ORIGIN + "/sw.js"), fetch: network,
    Request: function (url, init) { return { url, ...init }; },
    setTimeout: (f, ms, v) => setTimeout(f, 20, v), // 3 s becomes 20 ms here
    Response: { error: () => ({ type: "error", ok: false }) },
    caches: {
      async open(n) { if (!stores.has(n)) stores.set(n, new Map()); return store(n); },
      async keys() { return [...stores.keys()]; },
      async delete(n) { return stores.delete(n); },
    },
  };
  vm.runInNewContext(src, ctx);
  const event = (type, extra = {}) => { const e = { ...extra, waits: [], waitUntil(p) { this.waits.push(p); } }; handlers[type](e); return Promise.all(e.waits); };
  // one request through the worker: what it answered with, or "network" when it let the browser go to the server itself
  const request = async (url, { method = "GET", mode = "cors" } = {}) => {
    let answer = "network";
    handlers.fetch({ request: { url: new URL(url, ORIGIN).href, method, mode }, respondWith(p) { answer = p; } });
    return answer === "network" ? answer : await answer;
  };
  return { event, request, stores, state, fetched, asked, net };
}

(async () => {
  // install keeps the app's files; activate drops older versions and takes over
  let w = worker({ caches: { "parlor-shell-v0": { [ORIGIN + "/app.css"]: res("old") }, "parlor-fonts": {}, "someone-else": {} } });
  await w.event("install");
  assert.deepStrictEqual([...w.stores.get("parlor-shell-v1").keys()].sort(), SHELL.map((u) => new URL(u, ORIGIN + "/").href).sort());
  assert.ok(w.state.skipped);
  await w.request("/app.css");
  await w.request("/", { mode: "navigate" });
  assert.ok(w.asked.length === SHELL.length + 2 && w.asked.every((c) => c === "no-cache"), `the server is asked past the browser's kept copy, at install and on every load: ${w.asked}`);
  await w.event("activate");
  assert.deepStrictEqual([...w.stores.keys()].sort(), ["parlor-shell-v1", "someone-else"], "the old version and the old Google fonts go; others' caches stay");
  assert.ok(w.state.claimed);

  // your data, streams and music apps go to the server, always; so does anything but GET
  for (const u of ["/api/me", "/api/jf/stream/Audio/x/stream", "/music/api/library", "/rest/ping.view", "/hooks/lidarr/s", "/qbit/", "/sw.js", "https://i.ytimg.com/vi/x/hq.jpg"]) {
    assert.strictEqual(await w.request(u), "network", u);
  }
  assert.strictEqual(await w.request("/app.css", { method: "POST" }), "network");

  // online: the server's copy wins and is kept for next time
  w.net[ORIGIN + "/app.css"] = res("new css");
  assert.strictEqual((await w.request("/app.css")).body, "new css");
  assert.strictEqual(w.stores.get("parlor-shell-v1").get(ORIGIN + "/app.css").body, "new css");
  w.net[ORIGIN + "/?from=home"] = res("new page");
  assert.strictEqual((await w.request("/?from=home", { mode: "navigate" })).body, "new page");
  assert.strictEqual(w.stores.get("parlor-shell-v1").get(ORIGIN + "/").body, "new page", "any page address keeps the one page");

  // the server down, erring or slow: the kept copy
  w.net["*"] = "down";
  assert.strictEqual((await w.request("/app.css")).body, "new css");
  assert.strictEqual((await w.request("/", { mode: "navigate" })).body, "new page");
  w.net["*"] = res("Bad Gateway", { status: 502 });
  assert.strictEqual((await w.request("/", { mode: "navigate" })).body, "new page", "a proxy's 502 while Parlor restarts");
  w.net["*"] = "hang";
  assert.strictEqual((await w.request("/js/core.js")).body, "from the server", "slow: the kept one after 3 s");
  // nothing kept: the network's answer, however slow; or a plain failure
  w.net["*"] = "down";
  assert.strictEqual((await w.request("/vendor/hls.light.min.js")).type, "error");
  w.net["*"] = res("hls");
  assert.strictEqual((await w.request("/vendor/hls.light.min.js")).body, "hls", "a script loaded later is kept too");
  w.net["*"] = res("Not found", { status: 404 });
  assert.ok(!w.stores.get("parlor-shell-v1").has(ORIGIN + "/missing.js") && (await w.request("/missing.js")).status === 404, "errors aren't kept");
  assert.ok(!w.stores.get("parlor-shell-v1").has(ORIGIN + "/missing.js"));

  // another site's file isn't the worker's: the browser fetches it as it would
  w = worker();
  assert.strictEqual(await w.request("https://fonts.gstatic.com/s/bricolage/v1/a.woff2"), "network");

  // the off switch: nothing kept, nothing answered, the worker gone and open windows reloaded
  w = worker({ off: true, caches: { "parlor-shell-v1": { [ORIGIN + "/"]: res("page") }, "parlor-fonts": {}, "someone-else": {} } });
  await w.event("install");
  assert.ok(!w.stores.has("parlor-shell-v2"));
  await w.event("activate");
  assert.deepStrictEqual([...w.stores.keys()], ["someone-else"]);
  assert.ok(w.state.unregistered && !w.state.claimed);
  assert.deepStrictEqual(w.state.navigated, [ORIGIN + "/#/music"]);
  assert.strictEqual(await w.request("/"), "network");
  console.log("service worker ok");
})().catch((e) => { console.error(e); process.exit(1); });
