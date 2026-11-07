// node tests/test_nav.js: one nav model drives the sidebar, the phone tabs and each section's page row.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vm = require("vm");

const dir = path.join(__dirname, "..", "app", "static", "js");
const src = (f) => fs.readFileSync(path.join(dir, f), "utf8");
const load = (extra = "") => vm.runInNewContext(`${src("nav.js")}\n${extra}\n({ NAV, navAt, sideHtml, tabsHtml, subnavHtml, youHtml })`,
  { ICON: new Proxy({}, { get: (_, k) => `<i data-icon="${String(k)}"></i>` }), esc: (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`) });
const { NAV, navAt, sideHtml, tabsHtml, subnavHtml, youHtml } = load();

// every link in the nav opens a real page
const pages = new Set();
for (const f of fs.readdirSync(dir)) for (const m of src(f).matchAll(/\bpage\("([^"]*)"/g)) pages.add(m[1]);
for (const g of NAV) for (const [, href] of g.pages || [[g.label, g.href]]) {
  const name = href.replace(/^#\/?/, "").split("/")[0];
  assert.ok(pages.has(name), `${href} has no page("${name}")`);
}

// the right group and page light up, wherever you are
const at = (hash, section) => { const a = navAt(hash, section); return [a.group, a.href]; };
assert.deepStrictEqual(at("#/", "home"), ["home", "#/"]);
assert.deepStrictEqual(at("", "home"), ["home", "#/"]);
assert.deepStrictEqual(at("#/yt", "watch"), ["watch", "#/yt/home"]);
assert.deepStrictEqual(at("#/yt/subs", "watch"), ["watch", "#/yt/subs"]);
assert.deepStrictEqual(at("#/yt/later", "watch"), ["you", "#/yt/later"], "personal lists live under You");
assert.deepStrictEqual(at("#/watch/aqz-KE-bpKQ", "watch"), ["watch", null], "a video lights Watch, no page");
assert.deepStrictEqual(at("#/media/search/dune", "media"), ["media", "#/media"]);
assert.deepStrictEqual(at("#/requests/mine/pending", "media"), ["media", "#/requests"]);
assert.deepStrictEqual(at("#/album/x", "music"), ["music", null]);
assert.deepStrictEqual(at("#/library/artists", "music"), ["music", "#/library"]);
assert.deepStrictEqual(at("#/settings", "settings"), ["you", "#/settings"]);
assert.deepStrictEqual(at("#/search/lofi", "home"), ["home", null]);
assert.deepStrictEqual(at("#/you", "home"), ["you", null]);
assert.ok(/class="side-head" href="#\/you" aria-current="page"/.test(sideHtml(navAt("#/you", "home"))), "the You page lights its heading");

// what's lit is marked in every view
const side = sideHtml(navAt("#/yt/history", "watch"));
assert.ok(/<a href="#\/yt\/history" aria-current="page">History<\/a>/.test(side), side);
assert.strictEqual((side.match(/aria-current/g) || []).length, 1);
const tabs = tabsHtml(navAt("#/yt/history", "watch"));
assert.ok(/data-nav="you" aria-label="You" aria-current="page"/.test(tabs), tabs);
assert.strictEqual(NAV.length, (tabs.match(/<a /g) || []).length, "one tab per section");
assert.ok(/aria-label="Movies &#38; TV"/.test(tabs), "tabs keep a name when their label is hidden");
for (const g of NAV) for (const [, href] of g.pages || []) assert.ok(youHtml().includes(`href="${href}"`), `the You page links ${href}`);
const row = subnavHtml(navAt("#/yt/subs", "watch"));
assert.ok(row.includes('href="#/yt/home"') && /href="#\/yt\/subs" aria-current="page"/.test(row), row);
assert.strictEqual(subnavHtml(navAt("#/", "home")), "", "Home has no page row");
assert.ok(!sideHtml(navAt("#/", "home")).includes("<script"), "labels are escaped");

// a new section is one entry: it shows up everywhere with no other wiring
const more = load('NAV.splice(4, 0, { key: "books", label: "Books", icon: "book", pages: [["Reading now", "#/books"], ["Library", "#/books/all"]] });');
const b = more.navAt("#/books/all", "books");
assert.deepStrictEqual([b.group, b.href], ["books", "#/books/all"]);
assert.ok(more.sideHtml(b).includes(">Books<") && more.subnavHtml(b).includes("Reading now"));
assert.ok(more.youHtml().includes('href="#/books/all"'), "a section without a tab is on the You page");
const moreTabs = more.tabsHtml(b);
assert.strictEqual((moreTabs.match(/<a /g) || []).length, 5, "the phone keeps five tabs");
assert.ok(/data-nav="you" aria-label="You" aria-current="page"/.test(moreTabs), "Books lights You on a phone");
console.log("nav ok");
