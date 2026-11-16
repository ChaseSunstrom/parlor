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

// On TV: from a player on the page, the film or video goes from where it is and this one pauses; from a details page,
// from where it was left
{
  const handler = (videos) => {
    let click, own;
    require("vm").runInNewContext(grab(js("tv.js"), "/* Play on TV buttons", "\n});\n"), {
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
