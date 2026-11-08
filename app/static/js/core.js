/* Shared helpers, the API client, the router and the search box. */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const main = $("#main"), input = $("#q"), drawer = $("#queue");
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const tint = (c) => (/^#[0-9a-f]{6}$/i.test(c || "") ? c : "#3a3550");
const plain = (html) => new DOMParser().parseFromString(html || "", "text/html").body.textContent;
const mmss = (ms) => (ms ? `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}` : "");
const minutes = (tracks) => Math.round(tracks.reduce((n, t) => n + (t.ms || 0), 0) / 60000);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 3725 -> "1:02:05" */
function clock(sec) {
  if (sec == null || isNaN(sec)) return "";
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(s).padStart(2, "0")}`;
}
/** unix seconds -> "3 days ago" */
function since(ts) {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, n] of units) if (s >= n) return `${plural(Math.floor(s / n), unit)} ago`;
  return s < 0 ? "soon" : "just now";
}
const views = (n) => (n == null ? "" : `${compact.format(n)} view${n === 1 ? "" : "s"}`);
const bytes = (n) => {
  if (!n) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
};

const ICON = {
  home: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  user: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8.5" r="3.8" fill="none" stroke="currentColor" stroke-width="2"/><path d="M4.5 20c.9-3.9 3.9-6 7.5-6s6.6 2.1 7.5 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  pin: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.5h10v17l-5-3.8-5 3.8z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  pinned: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3.5h10v17l-5-3.8-5 3.8z" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  dl: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19.5h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  ok: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  x: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  clock: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7.5V12l3 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  play: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>',
  note: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-2.5-2.5A2.5 2.5 0 0 1 9 18Zm10-2a2.5 2.5 0 1 1-2.5-2.5A2.5 2.5 0 0 1 19 16Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  link: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  out: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 5h5v5M19 5l-8 8M18 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  bell: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  plus: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  trash: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  gear: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  door: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 8l-4 4 4 4M6 12h10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  film: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4" width="17" height="16" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7.5 4v16M16.5 4v16" stroke="currentColor" stroke-width="2"/></svg>',
  tv: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5.5" width="18" height="13" rx="3.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="m10.2 9.4 4.6 2.6-4.6 2.6z" fill="currentColor"/></svg>',
  wide: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="6.5" width="19" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  full: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  disc: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg>',
};
const SPIN = '<svg class="spin" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="var(--accent)"/><circle cx="16" cy="16" r="9.5" fill="none" stroke="var(--accent-ink)" stroke-opacity=".3"/><circle cx="16" cy="16" r="4.5" fill="var(--accent-ink)"/><circle cx="20" cy="9" r="1.6" fill="var(--accent-ink)" fill-opacity=".5"/></svg>';

/* ---------- API ---------- */

async function api(path, options = {}) {
  // the server only accepts changes carrying this header (it blocks other websites)
  const res = await fetch(path, { ...options, headers: { ...options.headers, "X-Requested-With": "parlor" } });
  const body = await res.json().catch(() => ({}));
  if (res.status === 423) {
    showUnlock();
    throw new Error("Parlor is locked");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    showGate();
    throw new Error("Sign in first");
  }
  if (!res.ok) throw new Error(body.detail || `${res.status} ${res.statusText}`);
  return body;
}
const send = (method) => (path, body) => api(path, {
  method,
  ...(body === undefined ? {} : { headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
});
const post = send("POST"), put = send("PUT"), patch = send("PATCH"), del = send("DELETE");

/* ---------- per-device preferences ---------- */

const PREF_DEFAULTS = {
  quality: "1080", // player and download quality
  skip: ["sponsor", "selfpromo", "interaction"], // SponsorBlock categories skipped automatically
  autoplay: true, captions: "", hideShorts: false, theater: false, volume: 1, speed: 1, cutSponsors: false,
};
function pref(key) {
  try {
    const v = JSON.parse(localStorage.getItem("parlor:" + key) ?? localStorage.getItem("yeerr:" + key));  // compat: settings saved before the rename
    return v ?? PREF_DEFAULTS[key];
  } catch { return PREF_DEFAULTS[key]; }
}
function setPref(key, value) { try { localStorage.setItem("parlor:" + key, JSON.stringify(value)); } catch { /* private mode */ } }

/* ---------- building blocks ---------- */

const img = (src, cls = "") => (src ? `<img class="${cls}" src="${esc(src)}" alt="" loading="lazy">` : `<div class="blank ${cls}"></div>`);
const section = (title, body) => (body ? `<h2>${esc(title)}</h2>${body}` : "");
const grid = (html, cls = "grid") => (html ? `<div class="${cls}">${html}</div>` : "");
const shelf = (title, body, more) => (body ? `<div class="shelf-head"><h2>${esc(title)}</h2>${more ? `<a class="more" href="${more}">See all</a>` : ""}</div>${body}` : "");
const skeletons = (n, kind = "v", cls = "vgrid") =>
  `<div class="${cls}">${Array.from({ length: n }, () => `<div><div class="skel ${kind}"></div><div class="skel line"></div><div class="skel line short"></div></div>`).join("")}</div>`;
const aurora = '<div class="aurora" aria-hidden="true"><i></i><i></i><i></i></div>';
/** a CSS url() for an image address: quotes and brackets can't end it early */
const cssUrl = (u) => `url("${encodeURI(u || "").replace(/["'()\\]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase())}")`;

function hero({ kind, title, image, round, poster, tintColor, photo, backdrop, meta, blurb, tagline, action }) {
  const style = `--tint:${tint(tintColor)}` + (photo || backdrop ? `;--photo:${cssUrl(photo || backdrop)}` : "");
  const cls = photo ? " photo" : backdrop ? " backdrop" : "";
  return `<header class="hero${cls}" style="${esc(style)}">
    ${photo ? "" : img(image, `art${round ? " round" : ""}${poster ? " poster" : ""}`)}
    <div>
      <div class="kind">${esc(kind)}</div>
      <h1 class="${title.length > 26 ? "long" : ""}">${esc(title)}</h1>
      ${tagline ? `<p class="tagline">${esc(tagline)}</p>` : ""}
      <div class="meta">${meta}</div>
      ${blurb ? `<p class="blurb">${esc(blurb)}</p>` : ""}
      <div class="actions">${action}</div>
    </div>
  </header>`;
}

/* ---------- toast ---------- */

const toastEl = $("#toast");
let toastTimer;
function toast(message, withView = false) {
  $("span", toastEl).textContent = message;
  $("button", toastEl).hidden = !withView;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 4200);
}
$("button", toastEl).addEventListener("click", () => setDrawer(true));

/* ---------- router ---------- */
// #/<view>/<parts...>; each section registers its pages with page().

const PAGES = {};
const SECTIONS = {
  home: "Search videos, movies, shows and music",
  watch: "Search YouTube, or paste a link",
  media: "Search movies, shows and people",
  music: "Artists, albums, songs, playlists or a Spotify link",
  settings: "Search videos, movies, shows and music",
};
const SEARCH = { home: "#/search/", watch: "#/yt/search/", media: "#/media/search/", music: "#/music/search/", settings: "#/search/" };
let ticket = 0, current = "home";
const paint = (t, html) => { if (t === ticket) main.innerHTML = html; };
function page(name, fn, sectionName) { PAGES[name] = { fn, section: sectionName }; }
function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash; // fires hashchange -> route()
}

async function route() {
  const t = ++ticket;
  const [, view = "", ...rest] = location.hash.split("/");
  const parts = rest.map((p) => { try { return decodeURIComponent(p); } catch { return p; } });
  const entry = PAGES[view] || PAGES[""];
  current = entry.section;
  document.body.dataset.section = current;
  drawNav();
  input.placeholder = SECTIONS[current] || SECTIONS.home;
  closeMenus();
  if (!(PAGES[view] && /search/.test(location.hash))) input.value = "";
  paint(t, `<div class="note">${SPIN}</div>`);
  try {
    await entry.fn(t, parts);
    if (!/\/search\//.test(location.hash) && !keepScroll) scrollTo(0, 0);
  } catch (e) {
    paint(t, `<div class="note"><strong>Couldn't load this</strong>${esc(e.message)}<br><button class="ghost" onclick="route()">Try again</button></div>`);
  }
  keepScroll = false;
}
let keepScroll = false;
let drawn = {};
function drawNav() {
  const at = navAt(location.hash, current);
  const put = (el, html) => { if (drawn[el] !== html) $(el).innerHTML = drawn[el] = html; }; // unchanged: keep focus where it is
  put(".nav", tabsHtml(at));
  put("#side", sideHtml(at));
  $("#back").hidden = !!(at.href || at.own || at.group === "home" && !location.hash.startsWith("#/search")); // pages inside a section get Back
}
$("#back").addEventListener("click", () => (history.length > 1 ? history.back() : go("#/")));
/** this section's pages as a row of chips; phones only (wide screens have the sidebar) */
const subnav = () => subnavHtml(navAt(location.hash, current));

/* ---------- search box: searches the section you're in ---------- */

let typing;
input.addEventListener("input", () => {
  clearTimeout(typing);
  const q = input.value.trim();
  if (q.length < 2) return;
  typing = setTimeout(() => {
    const hash = SEARCH[current] + encodeURIComponent(q);
    /\/search\//.test(location.hash) ? history.replaceState(null, "", hash) : history.pushState(null, "", hash);
    route();
  }, 450);
});
$("#searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  clearTimeout(typing);
  const q = input.value.trim();
  if (q) go(SEARCH[current] + encodeURIComponent(q));
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
    e.preventDefault();
    input.focus();
    input.select();
  }
});

/* ---------- account ---------- */

let ME = null;
const menuEl = $("#accountMenu");
function closeMenus() {
  menuEl.hidden = true;
  $("#account").setAttribute("aria-expanded", "false");
  $$(".popover").forEach((p) => p.remove());
}
$("#account").addEventListener("click", (e) => {
  e.stopPropagation();
  if (!menuEl.hidden) return closeMenus();
  menuEl.innerHTML = `<div class="who"><strong>${esc(ME?.name)}</strong><span class="dim">${ME?.admin ? "Admin" : "Member"}${ME?.jellyfin ? " · Jellyfin account" : ""}</span></div>
    <a href="#/settings" role="menuitem">${ICON.gear}Settings</a>
    <a href="#/yt/history" role="menuitem">${ICON.clock}Watch history</a>
    <a href="#/requests" role="menuitem">${ICON.film}Your requests</a>
    <button data-logout role="menuitem">${ICON.door}Sign out</button>`;
  menuEl.hidden = false;
  $("#account").setAttribute("aria-expanded", "true");
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#accountMenu, .popover, [data-pop]")) closeMenus();
  if (e.target.closest("[data-logout]")) post("/api/auth/logout").finally(() => location.reload());
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });

/** A small menu next to a button: items are [label, note, fn]. */
function popover(anchor, title, items) {
  closeMenus();
  const pop = document.createElement("div");
  pop.className = "popover";
  pop.innerHTML = `<strong>${esc(title)}</strong>` + items.map(([label, note], i) => `<button data-i="${i}">${esc(label)}<small>${esc(note || "")}</small></button>`).join("");
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  const left = Math.min(r.left + scrollX, scrollX + innerWidth - pop.offsetWidth - 12);
  pop.style.left = `${Math.max(12, left)}px`;
  pop.style.top = `${r.bottom + scrollY + 6}px`;
  pop.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (b) { pop.remove(); items[+b.dataset.i][2](); }
  });
  pop.querySelector("button")?.focus();
}
