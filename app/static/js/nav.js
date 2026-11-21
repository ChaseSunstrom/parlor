/* Navigation: this one list draws the sidebar (wide screens), the tab bar (phones) and each
   section's row of pages. A new section or page is one entry here. */

const NAV = [
  { key: "home", label: "Home", icon: "home", href: "#/" },
  { key: "library", label: "Library", icon: "shelf", pages: [["Home", "#/jf"], ["Films", "#/jf/movies"], ["Shows", "#/jf/shows"], ["Music", "#/jf/music"], ["Books", "#/jf/books"], ["Games", "#/games"]] },
  { key: "watch", label: "Watch", icon: "tv", pages: [["For you", "#/yt/home"], ["Subscriptions", "#/yt/subs"], ["Live", "#/live"]] },
  { key: "music", label: "Music", icon: "disc", pages: [["Discover", "#/music"], ["Your library", "#/library"]] },
  // everything you can ask for on one page (films, shows, music, games, books), and one list of what was asked for
  { key: "media", label: "Request", icon: "plus", pages: [["Browse", "#/request"], ["Your requests", "#/requests"]] },
  { key: "you", label: "You", icon: "user", href: "#/you", pages: [["Watch later", "#/yt/later", "Videos you saved", "clock"], ["History", "#/yt/history", "Everything you've watched", "clock"],
    ["Downloaded", "#/yt/downloads", "Videos on this server", "dl"], ["Saved for offline", "#/offline", "Music and videos on this device", "offline"],
    ["TVs", "#/tvs", "Pair a TV, play on it, the remote", "screen"], ["Channels", "#/yt/channels", "Follow, import, export", "tv"],
    ["Settings", "#/settings", "Playback, account and more", "gear"]] },
];
const TABS = 6; // the phone tab bar: the first sections, then You (all six today); the You page lists every page
const ALIASES = { "#/yt": "#/yt/home", "": "#/", "#": "#/" };
// pages opened from a menu page and not listed themselves: that menu page lights up for them, and they get Back
const UNDER = { "#/media": "#/request", "#/lidarr": "#/request", "#/gamereq": "#/request", "#/bookreq": "#/request", "#/sets": "#/request", "#/set": "#/request",
  "#/playlists": "#/library", "#/yt/rec": "#/yt/home", "#/yt/continue": "#/yt/home", "#/jf/artists": "#/jf/music" };
const SECTION_GROUP = { settings: "you", games: "library", musicreq: "media", gamereq: "media", bookreq: "media" }; // a page's section, when no nav link matches the address

/** the nav group and page link for an address: the longest link that the address starts with */
function navAt(hash, section) {
  hash = ALIASES[hash] ?? hash;
  const within = (href) => hash === href || (href !== "#/" && hash.startsWith(href + "/"));
  let best = null;
  for (const g of NAV) {
    for (const [, href] of [[g.label, g.href], ...(g.pages || [])]) {
      if (href && within(href) && (!best || href.length > best.href.length)) best = { group: g.key, href };
    }
  }
  const up = Object.keys(UNDER).filter(within).sort((a, b) => b.length - a.length)[0];
  const over = up && (!best || up.length > best.href.length) && NAV.find((g) => (g.pages || []).some(([, href]) => href === UNDER[up]));
  if (over) best = { group: over.key, href: UNDER[up], under: true };
  const own = !!best && NAV.find((g) => g.key === best.group).href === best.href && best.href !== "#/"; // a group's own page, like You
  const group = best?.group || SECTION_GROUP[section] || (NAV.some((g) => g.key === section) ? section : "home");
  return { group, href: own ? null : best?.href ?? null, own, under: !!best?.under };
}

const groupHref = (g) => g.href || g.pages[0][1];
const lit = (on) => (on ? ' aria-current="page"' : "");

/** the sidebar: every section, and the pages of the one you're in (the others' pages are a click on their name away) */
const tvSearchLink = (on) => `<div class="side-group${on ? " on" : ""}" data-g="search"><a class="side-head" href="#/tvsearch"${lit(on)}><svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="m15.5 15.5 5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg><span>Search</span></a></div>`;
function sideHtml(at, tv = "") { // a TV has no top bar: its search is in the rail (tv is "search" on that page, else "tv")
  if (tv === "search") at = { ...at, group: "" };
  return (tv ? tvSearchLink(tv === "search") : "") + NAV.map((g) => `<div class="side-group${g.key === at.group ? " on" : ""}" data-g="${g.key}">
    <a class="side-head" href="${groupHref(g)}"${lit(g.key === at.group && (!g.pages || at.own))}>${ICON[g.icon]}<span>${esc(g.label)}</span></a>
    ${(g.key === at.group ? g.pages || [] : []).map(([label, href]) => `<a href="${href}"${lit(href === at.href)}>${esc(label)}</a>`).join("")}</div>`).join("") + (tv ? tvWhoLink() : "");
}
/** on a TV whose household has more than one person: who it is acting as, and the way to change that */
function tvWhoLink() {
  const p = tvPeople, now = p && (p.people.find((x) => x.id === p.viewer) || p.people.find((x) => x.owner));
  return now && p.people.length > 1 ? `<div class="side-group" data-g="who"><a class="side-head who" href="#/who"${lit(location.hash.startsWith("#/who"))}>${avatar(now.name)}<span>${esc(now.name)}</span></a></div>` : "";
}

function tabsHtml(at) {
  const you = NAV.find((g) => g.key === "you");
  const shown = [...NAV.filter((g) => g !== you).slice(0, TABS - 1), you];
  const on = (g) => g.key === at.group || (g === you && !shown.some((x) => x.key === at.group)); // sections without a tab live under You
  return shown.map((g) => `<a href="${groupHref(g)}" data-nav="${g.key}" aria-label="${esc(g.label)}"${lit(on(g))}>${ICON[g.icon]}<span>${esc(g.label)}</span></a>`).join("");
}

/** the You page: your pages as tiles, then every section's pages, so everything is two taps away on a phone */
function youHtml() {
  const you = NAV.find((g) => g.key === "you");
  const tile = ([label, href, note, icon]) => `<a class="you-tile" href="${href}"><span class="ic">${ICON[icon || you.icon]}</span><span><b>${esc(label)}</b><small>${esc(note || "")}</small></span></a>`;
  return `<div class="you-grid">${you.pages.map(tile).join("")}
      <button class="you-tile" data-logout><span class="ic">${ICON.door}</span><span><b>Sign out</b><small>On this device</small></span></button></div>
    <div class="you-more">${NAV.filter((g) => g.pages && g !== you).map((g) => `<section data-g="${g.key}"><h3>${ICON[g.icon]}${esc(g.label)}</h3>
      ${g.pages.map(([label, href]) => `<a href="${href}">${esc(label)}</a>`).join("")}</section>`).join("")}</div>`;
}

/** the current section's pages as a row (phones and tablets, under the header; the sidebar has them on wide screens) */
function subnavHtml(at) {
  const g = NAV.find((x) => x.key === at.group);
  return g?.pages ? `<nav class="chips subnav" aria-label="${esc(g.label)}">${g.pages.map(([label, href]) => `<a href="${href}"${lit(href === at.href)}>${esc(label)}</a>`).join("")}</nav>` : "";
}
