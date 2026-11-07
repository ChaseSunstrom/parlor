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
