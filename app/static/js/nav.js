/* Navigation: this one list draws the sidebar (wide screens), the tab bar (phones) and each
   section's row of pages. A new section or page is one entry here. */

const NAV = [
  { key: "home", label: "Home", icon: "home", href: "#/" },
  { key: "library", label: "Library", icon: "shelf", pages: [["Home", "#/jf"], ["Films", "#/jf/movies"], ["Shows", "#/jf/shows"], ["Music", "#/jf/music"], ["Books", "#/jf/books"]] },
  { key: "watch", label: "Watch", icon: "tv", pages: [["For you", "#/yt/home"], ["Subscriptions", "#/yt/subs"], ["Recommended", "#/yt/rec"]] },
  { key: "media", label: "Movies & TV", icon: "film", pages: [["Discover", "#/media"], ["Requests", "#/requests"]] },
  { key: "music", label: "Music", icon: "disc", pages: [["Discover", "#/music"], ["Your library", "#/library"], ["Playlists", "#/playlists"], ["Request music", "#/lidarr"]] },
  { key: "you", label: "You", icon: "user", href: "#/you", pages: [["Continue watching", "#/yt/continue", "Right where you left off", "play"],
    ["Watch later", "#/yt/later", "Videos you saved", "clock"], ["History", "#/yt/history", "Everything you've watched", "clock"],
    ["Downloaded", "#/yt/downloads", "Videos on this server", "dl"], ["Saved for offline", "#/offline", "Music and videos on this device", "offline"],
    ["TVs", "#/tvs", "Pair a TV, play on it, the remote", "screen"], ["Channels", "#/yt/channels", "Follow, import, export", "tv"],
    ["Settings", "#/settings", "Playback, account and more", "gear"]] },
];
const TABS = 6; // the phone tab bar: the first sections, then You; the You page lists every page
const ALIASES = { "#/yt": "#/yt/home", "": "#/", "#": "#/" };
const SECTION_GROUP = { settings: "you" }; // a page's section, when no nav link matches the address

/** the nav group and page link for an address: the longest link that the address starts with */
function navAt(hash, section) {
  hash = ALIASES[hash] ?? hash;
  let best = null;
  for (const g of NAV) {
    for (const [, href] of [[g.label, g.href], ...(g.pages || [])]) {
      if (href && (hash === href || (href !== "#/" && hash.startsWith(href + "/"))) && (!best || href.length > best.href.length)) best = { group: g.key, href };
    }
  }
  const own = !!best && NAV.find((g) => g.key === best.group).href === best.href && best.href !== "#/"; // a group's own page, like You
  const group = best?.group || SECTION_GROUP[section] || (NAV.some((g) => g.key === section) ? section : "home");
  return { group, href: own ? null : best?.href ?? null, own };
}

const groupHref = (g) => g.href || g.pages[0][1];
const lit = (on) => (on ? ' aria-current="page"' : "");

function sideHtml(at) {
  return NAV.map((g) => `<div class="side-group${g.key === at.group ? " on" : ""}" data-g="${g.key}">
    <a class="side-head" href="${groupHref(g)}"${lit(g.key === at.group && (!g.pages || at.own))}>${ICON[g.icon]}<span>${esc(g.label)}</span></a>
    ${(g.pages || []).map(([label, href]) => `<a href="${href}"${lit(href === at.href)}>${esc(label)}</a>`).join("")}</div>`).join("");
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

/** the current section's pages as a row (shown on phones; the sidebar has them on wide screens) */
function subnavHtml(at) {
  const g = NAV.find((x) => x.key === at.group);
  return g?.pages ? `<nav class="chips subnav" aria-label="${esc(g.label)}">${g.pages.map(([label, href]) => `<a href="${href}"${lit(href === at.href)}>${esc(label)}</a>`).join("")}</nav>` : "";
}
