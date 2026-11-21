// node tests/test_ui_helpers.js: checks the UI's escaping helpers (no browser needed).
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const js = (f) => fs.readFileSync(path.join(__dirname, "..", "app", "static", "js", f), "utf8");
const grab = (src, start, end) => src.slice(src.indexOf(start), src.indexOf(end, src.indexOf(start)) + end.length);
const core = js("core.js"), watch = js("watch.js");
// run just those helpers, from the app's own source, in a sandbox
const { esc, cssUrl, linkify } = require("vm").runInNewContext(`(() => { ${grab(core, "const ESC =", ";\n")} ${grab(core, "const esc =", ";\n")}
  ${grab(core, "const cssUrl =", ";\n")} ${grab(watch, "function linkify(", "\n}\n")} return { esc, cssUrl, linkify }; })()`);

// an image address can't break out of url("...")
const u = cssUrl("https://x.test/a'b\"c(d)e\\.jpg");
assert.ok(!/["'()\\]/.test(u.slice(5, -2)), u);
assert.ok(u.startsWith('url("https://x.test/'), u);

// descriptions: text is escaped, links stay links, timestamps seek, a timestamp inside a link doesn't break it
const html = linkify('<b>hi</b> see https://ex.test/v?a=(1:23) then 2:05 and (10:00).', true);
assert.ok(html.includes("&lt;b&gt;hi&lt;/b&gt;"), html);
assert.ok(html.includes('<a href="https://ex.test/v?a=(1:23)"'), html);
assert.ok(html.includes('data-seek="125"') && html.includes('data-seek="600"'), html);
assert.ok(!html.includes('data-seek="83"'), "no button inside the link");
assert.ok(!linkify("javascript:alert(1) 1:00", false).includes("<a"), "only http(s) links");
console.log("ui helpers ok");

// the page's scripts share one global scope: the same const in two files would stop the later one
const order = [...fs.readFileSync(path.join(__dirname, "..", "app", "static", "index.html"), "utf8").matchAll(/<script src="js\/([\w-]+\.js)"/g)].map((m) => m[1]);
assert.ok(order.length >= 6, order);
new (require("vm").Script)(order.map(js).join("\n;\n"), { filename: "all-scripts.js" }); // throws on a clash
console.log("scripts load together ok");

// nothing from another site: a stylesheet in the head holds every script after it, so a TV with no internet sat on a
// blank page while it waited for Google's font
const page = fs.readFileSync(path.join(__dirname, "..", "app", "static", "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "app", "static", "app.css"), "utf8");
assert.deepStrictEqual([...page.matchAll(/(?:src|href)="((?:https?:)?\/\/[^"]*)"/g)].map((m) => m[1]), [], "index.html loads only Parlor's own files");
assert.deepStrictEqual([...css.matchAll(/(?:@import|url\()\s*["']?((?:https?:)?\/\/[^"')]*)/g)].map((m) => m[1]), [], "app.css loads only Parlor's own files");
for (const m of css.matchAll(/url\("(vendor\/[^"]+)"\)/g)) assert.ok(fs.existsSync(path.join(__dirname, "..", "app", "static", m[1])), m[1]);
console.log("only Parlor's own files ok");

// Skip intro: the button shows inside a segment the server found, with its own words, and nowhere else
{
  const { segmentAt, SKIP_LABEL } = require("vm").runInNewContext(`(() => { ${grab(core, "const segmentAt =", ";\n")} ${grab(core, "const SKIP_LABEL =", ";\n")} return { segmentAt, SKIP_LABEL }; })()`);
  const segs = [{ type: "intro", start: 30, end: 90 }, { type: "outro", start: 600, end: 700 }];
  assert.strictEqual(segmentAt(segs, 10), null, "before the intro");
  assert.strictEqual(segmentAt(segs, 30).type, "intro");
  assert.strictEqual(segmentAt(segs, 89.5), null, "the last second isn't worth a button");
  assert.strictEqual(segmentAt(segs, 650).type, "outro");
  assert.strictEqual(segmentAt([], 40), null, "a server with none: no button");
  assert.strictEqual(segmentAt(undefined, 40), null);
  assert.strictEqual(SKIP_LABEL.intro, "Skip intro");
  assert.strictEqual(SKIP_LABEL.outro, "Skip credits");
  assert.ok(grab(js("library.js"), 'id="jfSkip"', ">").includes("hidden"), "the film player has the button, hidden until a segment");
  console.log("skip intro ok");
}

// Double-tap: on a touch screen two quick taps skip 10 s on a side or take full screen in the middle; a mouse keeps its double-click
{
  const { doubleTap } = require("vm").runInNewContext(`(() => { ${grab(core, "function doubleTap(", "\n}\n")} return { doubleTap }; })()`);
  const rig = () => {
    const on = {}, calls = [];
    const el = { addEventListener: (k, f) => { on[k] = f; }, getBoundingClientRect: () => ({ left: 100, width: 300, bottom: 400 }) };
    doubleTap(el, (d) => calls.push(d), () => calls.push("full"));
    const tap = (x, at, o = {}) => on.pointerup({ pointerType: "touch", clientX: x, clientY: 100, timeStamp: at, ...o });
    return { on, calls, tap };
  };
  let r = rig();
  r.tap(120, 1000); assert.deepStrictEqual(r.calls, [], "one tap does nothing");
  r.tap(120, 1200); assert.deepStrictEqual(r.calls, [-10], "left: back");
  r.tap(380, 5000); r.tap(380, 5100); assert.deepStrictEqual(r.calls, [-10, 10], "right: forward");
  r.tap(250, 8000); r.tap(250, 8100); assert.deepStrictEqual(r.calls, [-10, 10, "full"], "middle: full screen");
  r.tap(120, 9000); r.tap(120, 9200); r.tap(120, 9300);
  assert.deepStrictEqual(r.calls.slice(3), [-10], "a third tap starts over");
  r = rig(); r.tap(120, 1000); r.tap(120, 1500); assert.deepStrictEqual(r.calls, [], "too slow");
  r = rig(); r.tap(120, 1000, { pointerType: "mouse" }); r.tap(120, 1100, { pointerType: "mouse" }); assert.deepStrictEqual(r.calls, [], "a mouse isn't a touch");
  r = rig(); r.tap(120, 1000, { clientY: 390 }); r.tap(120, 1100, { clientY: 390 }); assert.deepStrictEqual(r.calls, [], "the control bar is the browser's");
  r = rig(); r.tap(250, 1000); r.tap(250, 1100); r.on.dblclick({ timeStamp: 1150 });
  assert.deepStrictEqual(r.calls, ["full"], "the click the browser adds after a double-tap doesn't toggle it back");
  r.on.dblclick({ timeStamp: 9000 }); assert.deepStrictEqual(r.calls, ["full", "full"], "a mouse double-click: full screen");
  assert.ok(grab(js("library.js"), "doubleTap(", ")").includes("box"), "the film player uses it");
  console.log("double tap ok");
}

// Spoiler shield: with it on, an episode you haven't watched shows no still and no summary; with it off, or once watched, it does
{
  const rows = (spoilers) => require("vm").runInNewContext(`(() => { ${grab(js("library.js"), "function episodeRows(", "\n}\n")} return episodeRows; })()`,
    { esc, ICON: { play: "", ok: "" }, runtimeText: () => "", left: () => "", pref: (k) => (k === "spoilers" ? spoilers : undefined) });
  const eps = [{ id: "a", name: "One", image: "/a.jpg", overview: "Plot A", played: true }, { id: "b", name: "Two", image: "/b.jpg", overview: "Plot B" },
    { id: "c", name: "Three", image: "/c.jpg", overview: "Plot C", position: 60, runtime: 600 }, { id: "d", name: "Four", image: "/d.jpg", overview: "Plot D" }];
  const off = rows(false)(eps, "d"), on = rows(true)(eps, "d");
  for (const f of ["/a.jpg", "/b.jpg", "/c.jpg", "/d.jpg", "Plot A", "Plot B", "Plot C", "Plot D"]) assert.ok(off.includes(f), `off shows ${f}`);
  for (const f of ["/a.jpg", "/c.jpg", "/d.jpg", "Plot A", "Plot C", "Plot D"]) assert.ok(on.includes(f), `on still shows ${f}: watched, started, or the one playing`);
  for (const f of ["/b.jpg", "Plot B"]) assert.ok(!on.includes(f), `on hides ${f}`);
  assert.ok(on.includes("Two"), "the name stays");
  assert.ok(grab(js("home.js"), 'check("pSpoilers"', ")").length > 10 && /keep\("#pSpoilers", "spoilers"/.test(js("home.js")), "a setting turns it on");
  assert.strictEqual(require("vm").runInNewContext(`(() => { ${grab(core, "const PREF_DEFAULTS =", "};\n")} return PREF_DEFAULTS.spoilers; })()`), false, "off until asked");
  console.log("spoiler shield ok");
}

// Continue watching: one title goes without leaving the library. A film or episode in Jellyfin goes back to the start, a video loses its history row
{
  const lib = js("library.js");
  const jcard = require("vm").runInNewContext(`(() => { ${grab(lib, "function jcard(", "\n}\n")} return jcard; })()`,
    { esc, ICON: { ok: "", play: "", x: "x" }, SQUARE: new Set(), VIDEO: new Set(["Movie"]), jhref: () => "#/x", jsub: () => "" });
  const film = { id: "ab12", type: "Movie", name: "Heat", position: 60, runtime: 600 };
  assert.ok(jcard(film, { wide: true, drop: true }).includes('data-jf-drop="ab12"'), "a Continue watching card has the button");
  assert.ok(!jcard(film, { wide: true }).includes("data-jf-drop"), "other cards don't");
  assert.ok(grab(lib, 'shelf("Continue watching"', ")").includes("drop: true"), "the library's Continue watching asks for it");
  const sent = [];
  let click, removed = 0, toasted = "";
  require("vm").runInNewContext(grab(lib, "/* Continue watching: the x", "\n});\n"), {
    document: { addEventListener: (_, f) => { click = f; } }, post: (url, body) => { sent.push([url, body]); return Promise.resolve({}); }, forgetLast: (k) => sent.push(k), toast: (m) => { toasted = m; } });
  const b = { dataset: { jfDrop: "ab12" }, closest: () => ({ remove: () => { removed++; } }) };
  click({ preventDefault() {}, target: { closest: (sel) => (sel === "[data-jf-drop]" ? b : null) } });
  click({ preventDefault() {}, target: { closest: () => null } });
  Promise.resolve().then(() => Promise.resolve()).then(() => {
    assert.strictEqual(JSON.stringify(sent), JSON.stringify([["/api/jf/progress", { id: "ab12", position: 0 }], "jfhome"]),"position 0, not 'watched', and the kept row is dropped");
    assert.strictEqual(removed, 1, "the card goes");
  }).catch((err) => { console.error(err); process.exit(1); });
  assert.ok(grab(js("home.js"), 'shelf("Continue watching"', '"#/yt/continue"').includes("forget: true"), "a video's Continue watching on the home page");
  assert.ok(grab(watch, "async function ytContinue", "\n}\n").includes("forget: true"), "and its own page");
  assert.ok(/forgetLast\("homeContinue"\)/.test(watch), "a forgotten video leaves the kept row too");
  console.log("continue watching ok");
}

// Rows from the last visit: drawn at once from the last answer, again only if the new one differs, the old one stays if the server can't answer
(async () => {
  const store = {};
  const methods = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; }, removeItem: (k) => { delete store[k]; } };
  const ls = new Proxy(store, { get: (t, p) => methods[p] || t[p] }); // like the browser's: its keys are the stored ones, the methods aren't keys
  const mk = (me) => require("vm").runInNewContext(`(() => { ${grab(core, "async function lastThen(", "\n}\n")} ${grab(core, "function forgetLast(", "\n}\n")} return { lastThen, forgetLast }; })()`,
    { localStorage: ls, ME: { id: me }, JSON, Object });
  const { lastThen, forgetLast } = mk(1);
  const drawn = [];
  const draw = (x) => drawn.push(x);
  await lastThen("row", async () => ["a"], draw);
  assert.deepStrictEqual(drawn, [["a"]], "first visit: once, when it arrives");
  drawn.length = 0;
  await lastThen("row", async () => ["a"], draw);
  assert.deepStrictEqual(drawn, [["a"]], "the same answer isn't drawn twice");
  drawn.length = 0;
  let release; const slow = new Promise((r) => { release = r; });
  const p = lastThen("row", () => slow, draw);
  assert.deepStrictEqual(drawn, [["a"]], "the last answer is on the page before the new one arrives");
  release(["a", "b"]); await p;
  assert.deepStrictEqual(drawn, [["a"], ["a", "b"]], "and the new one replaces it");
  drawn.length = 0;
  await lastThen("row", async () => { throw new Error("down"); }, draw);
  assert.deepStrictEqual(drawn, [["a", "b"]], "a server that can't answer leaves what was drawn");
  await assert.rejects(lastThen("other", async () => { throw new Error("down"); }, draw), /down/, "nothing to show: the error goes on");
  drawn.length = 0;
  await mk(2).lastThen("row", async () => ["z"], draw);
  assert.deepStrictEqual(drawn, [["z"]], "another account on this device doesn't see the first one's rows");
  store["parlor:last:1:bad"] = "{not json";
  await lastThen("bad", async () => [1], draw);
  forgetLast("row"); assert.ok(!("parlor:last:1:row" in store) && "parlor:last:2:row" in store, "one row of this account");
  forgetLast(); assert.deepStrictEqual(Object.keys(store), [], "signing out: every account's");
  assert.ok(grab(js("pwa.js"), "function forgetMe()", "\n}\n").includes("forgetLast()"), "signing out forgets them");
  assert.ok(/lastThen\("jfhome"/.test(js("library.js")) && /lastThen\("homeContinue"/.test(js("home.js")), "the Library and Home draw with it");
  console.log("rows from the last visit ok");
})().catch((err) => { console.error(err); process.exit(1); });

// On TV: from a player on the page, the film or video goes from where it is and this one pauses; from a details page,
// from where it was left
{
  const handler = (videos) => {
    let click, own;
    require("vm").runInNewContext(grab(js("remote.js"), "/* Play on TV buttons", "\n});\n"), {
      document: { addEventListener: (_, f) => { click = f; } }, byId: (id) => videos[id] || null, tvChoose: (_a, _c, o) => { own = o; } });
    return (data) => {
      const b = { dataset: data };
      click({ preventDefault() {}, target: { closest: (sel) => ((sel === "[data-cast-jf]" && data.castJf) || (sel === "[data-cast-yt]" && data.castYt) ? b : null) } });
      return own;
    };
  };
  const video = (readyState = 4) => ({ readyState, currentTime: readyState ? 1234.5 : 0, paused: false, pause() { this.paused = true; } });
  const jfVideo = video(), ytVideo = video();
  assert.deepStrictEqual({ ...handler({ jfVideo })({ castJf: "ab12" }) }, { kind: "jf", id: "ab12", start: 1234.5 }, "the film player: from where it is");
  assert.ok(jfVideo.paused, "and it pauses here");
  assert.deepStrictEqual({ ...handler({})({ castJf: "ab12" }) }, { kind: "jf", id: "ab12" }, "a details page: where it was left");
  assert.deepStrictEqual({ ...handler({ video: ytVideo })({ castYt: "dQw4w9WgXcQ" }) }, { kind: "yt", id: "dQw4w9WgXcQ", start: 1234.5 });
  assert.ok(ytVideo.paused);
  assert.deepStrictEqual({ ...handler({ jfVideo: video(0) })({ castJf: "ab12" }) }, { kind: "jf", id: "ab12" }, "a player still loading: where it was left, not 0:00");
  assert.ok(grab(js("library.js"), '<div class="ptools">', "</div>").includes('tvButton("data-cast-jf", x.id)'), "the film player has the button");
  console.log("on TV from a player ok");
}
