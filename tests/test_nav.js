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
assert.deepStrictEqual(at("#/lidarr/abba", "media"), ["media", "#/lidarr"], "asking for music is under Request");
assert.deepStrictEqual(at("#/games", "games"), ["library", "#/games"], "Games is one page of the Library");
assert.deepStrictEqual(at("#/games/retro", "games"), ["library", "#/games"]);
assert.deepStrictEqual(at("#/sets", "media"), ["media", null], "Collections is reached from Films & shows, not the menu");

// six sections; Request has every way of asking, in one place
assert.deepStrictEqual(Array.from(NAV, (g) => g.label), ["Home", "Library", "Watch", "Music", "Request", "You"]);
assert.deepStrictEqual(Array.from(NAV.find((g) => g.key === "media").pages, ([label]) => label), ["Films & shows", "Music", "Games", "Books", "Your requests"]);
assert.deepStrictEqual(at("#/gamereq/game/620", "gamereq"), ["media", "#/gamereq"], "asking for a game is under Request");
assert.ok(!NAV.find((g) => g.key === "music").pages.some(([, href]) => href === "#/lidarr"));
// the sidebar lists the pages of the section you're in, not every section's
const onHome = sideHtml(navAt("#/", "home")), inLibrary = sideHtml(navAt("#/jf/shows", "library"));
assert.strictEqual((onHome.match(/<a /g) || []).length, NAV.length, onHome);
assert.ok(inLibrary.includes('href="#/jf/movies"') && inLibrary.includes('href="#/games"') && !inLibrary.includes('href="#/yt/subs"'), inLibrary);
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
assert.ok(/aria-label="Request"/.test(tabs), "tabs keep a name when their label is hidden");
assert.strictEqual((tabs.match(/<a /g) || []).length, 6, "every section has a tab on a phone");
for (const g of NAV) for (const [, href] of g.pages || []) assert.ok(youHtml().includes(`href="${href}"`), `the You page links ${href}`);
const row = subnavHtml(navAt("#/yt/subs", "watch"));
assert.ok(row.includes('href="#/yt/home"') && /href="#\/yt\/subs" aria-current="page"/.test(row), row);
assert.strictEqual(subnavHtml(navAt("#/", "home")), "", "Home has no page row");
assert.ok(!sideHtml(navAt("#/", "home")).includes("<script"), "labels are escaped");

// a new section is one entry: it shows up everywhere with no other wiring
const more = load('NAV.splice(5, 0, { key: "books", label: "Books", icon: "book", pages: [["Reading now", "#/books"], ["Library", "#/books/all"]] });');
const b = more.navAt("#/books/all", "books");
assert.deepStrictEqual([b.group, b.href], ["books", "#/books/all"]);
assert.ok(more.sideHtml(b).includes(">Books<") && more.subnavHtml(b).includes("Reading now"));
assert.ok(more.youHtml().includes('href="#/books/all"'), "a section without a tab is on the You page");
const moreTabs = more.tabsHtml(b);
assert.strictEqual((moreTabs.match(/<a /g) || []).length, 6, "the phone keeps six tabs");
assert.ok(/data-nav="you" aria-label="You" aria-current="page"/.test(moreTabs), "Books lights You on a phone");
// what an admin closed to someone leaves their menu (home.js's applyAccess), and nothing else does
const home = src("home.js");
const accessSrc = home.match(/^const closedTo = .*$/m)[0] + "\n" + home.match(/^function applyAccess\(\) \{[\s\S]*?^\}/m)[0];
const menuFor = (closed) => {
  const sections = Object.fromEntries(closed.map((k) => [k, false]));
  const got = vm.runInNewContext(`${src("nav.js")}\n${accessSrc}\napplyAccess();\nJSON.stringify(NAV.map((g) => [g.key, (g.pages || [[g.label, g.href]]).map(([, h]) => h)]))`,
    { ICON: {}, esc: String, ME: { sections }, document: { body: { classList: { toggle() {} } } } });
  return Object.fromEntries(JSON.parse(got));
};
const full = menuFor([]);
assert.deepStrictEqual(Object.keys(full), ["home", "library", "watch", "music", "media", "you"], "nothing closed: the whole menu");
assert.deepStrictEqual(full.media, ["#/media", "#/lidarr", "#/gamereq", "#/bookreq", "#/requests"]);
let m = menuFor(["library"]);
assert.deepStrictEqual([Object.keys(m), m.games], [["home", "games", "watch", "music", "media", "you"], ["#/games"]], "Library closed, Games open: Games takes its place");
m = menuFor(["library", "games"]);
assert.deepStrictEqual(Object.keys(m), ["home", "watch", "music", "media", "you"], "Library and Games closed: neither is there");
m = menuFor(["games"]);
assert.ok(!m.library.includes("#/games") && m.library.includes("#/jf/books"), "Games closed: the Library keeps its other pages");
assert.ok(!m.media.includes("#/gamereq"), "Games closed: nothing to ask for either");
m = menuFor(["music"]);
assert.deepStrictEqual([Object.keys(m), m.media], [["home", "library", "watch", "media", "you"], ["#/media", "#/gamereq", "#/bookreq", "#/requests"]], "Music closed: Request keeps films, shows, games and books");
m = menuFor(["media"]);
assert.deepStrictEqual(m.media, ["#/lidarr", "#/gamereq", "#/bookreq", "#/requests"], "Films & shows closed: Request keeps music, games and books");
m = menuFor(["media", "music"]);
assert.ok(!("music" in m) && String(m.media) === "#/gamereq,#/bookreq,#/requests", "films, shows and music closed: Request keeps games and books");
m = menuFor(["media", "music", "games"]);
assert.deepStrictEqual(m.media, ["#/bookreq", "#/requests"], "films, shows, music and games closed: Request keeps books (they go to the Library)");
m = menuFor(["library"]);
assert.ok(!m.media.includes("#/bookreq"), "Library closed: no books to ask for");
m = menuFor(["media", "music", "games", "library"]);
assert.ok(!("media" in m) && !("music" in m), "all four closed: no Request and no Music");
assert.deepStrictEqual(at("#/bookreq/book/OL1W", "bookreq"), ["media", "#/bookreq"], "asking for a book is under Request");
m = menuFor(["watch"]);
assert.ok(!("watch" in m) && !m.you.some((h) => h.startsWith("#/yt")), "Watch closed: its lists leave You too");
console.log("nav ok");
