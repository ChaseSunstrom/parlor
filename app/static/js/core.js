/* Shared helpers, the API client, the router and the search box. */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const main = $("#main"), stage = $("#view"), input = $("#q"), drawer = $("#queue");
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
  lock: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  gear: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  door: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 8l-4 4 4 4M6 12h10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  film: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4" width="17" height="16" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M7.5 4v16M16.5 4v16" stroke="currentColor" stroke-width="2"/></svg>',
  tv: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5.5" width="18" height="13" rx="3.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="m10.2 9.4 4.6 2.6-4.6 2.6z" fill="currentColor"/></svg>',
  pad: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M7.5 7h9a5 5 0 0 1 4.9 6l-.8 3.6a2.6 2.6 0 0 1-4.4 1.2L13.9 16h-3.8l-2.3 1.8a2.6 2.6 0 0 1-4.4-1.2L2.6 13A5 5 0 0 1 7.5 7z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M7.5 10v4M5.5 12h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="15.5" cy="11" r="1.2" fill="currentColor"/><circle cx="17.8" cy="13.2" r="1.2" fill="currentColor"/></svg>',
  screen: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="4" width="19" height="13" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 21h8M12 17v4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  wide: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="2.5" y="6.5" width="19" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  full: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  disc: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="12" r="2.6" fill="currentColor"/></svg>',
  pause: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M7.5 5.5h3v13h-3zM13.5 5.5h3v13h-3z" fill="currentColor"/></svg>',
  next: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6v12l8.5-6zM16.5 6h2.2v12h-2.2z" fill="currentColor"/></svg>',
  prev: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6v12L9.5 12zM5.3 6h2.2v12H5.3z" fill="currentColor"/></svg>',
  shuffle: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h3.5c4.5 0 4.5 10 9 10H20M4 17h3.5c1.6 0 2.6-1.3 3.4-3M20 7h-3.5c-1.6 0-2.6 1.3-3.4 3M17.5 4.5 20 7l-2.5 2.5M17.5 14.5 20 17l-2.5 2.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  shelf: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4.5v15M9.5 4.5v15M14 6l4.3 13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M3.5 20h17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  book: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6.5C10 5 7.5 4.5 4 4.8v13.5c3.5-.3 6 .2 8 1.7 2-1.5 4.5-2 8-1.7V4.8c-3.5-.3-6 .2-8 1.7zM12 6.5V20" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  heart: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19.5s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7a4.3 4.3 0 0 1 7.5 2.5c0 5.4-7.5 10-7.5 10z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  queue: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6.5h11M4 11.5h11M4 16.5h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M17 13.5v5.2a2 2 0 1 1-1.4-1.9M17 13.5l3 1" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  repeat: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M17 3.5 19.5 6 17 8.5M4.5 11V9.5A3.5 3.5 0 0 1 8 6h11.5M7 20.5 4.5 18 7 15.5M19.5 13v1.5A3.5 3.5 0 0 1 16 18H4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  repeat1: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M17 3.5 19.5 6 17 8.5M4.5 11V9.5A3.5 3.5 0 0 1 8 6h11.5M7 20.5 4.5 18 7 15.5M19.5 13v1.5A3.5 3.5 0 0 1 16 18H4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M11 10.5 12.5 9.5v5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  more: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5.5" cy="12" r="1.9" fill="currentColor"/><circle cx="12" cy="12" r="1.9" fill="currentColor"/><circle cx="18.5" cy="12" r="1.9" fill="currentColor"/></svg>',
  grip: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 9h14M5 15h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  chev: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M9.5 5.5 16 12l-6.5 6.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  offline: '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 8.8a14 14 0 0 1 4.6-2.9M10.7 5.1A14 14 0 0 1 21.5 8.8M5.5 12.2a9.6 9.6 0 0 1 3.9-2.3M14.6 9.9a9.6 9.6 0 0 1 3.9 2.3M8.6 15.4a5 5 0 0 1 6.8 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="19" r="1.4" fill="currentColor"/><path d="M4 3.5 20 20.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};
const SPIN = '<svg class="spin" viewBox="0 0 32 32" aria-hidden="true"><circle cx="16" cy="16" r="14" fill="var(--accent)"/><circle cx="16" cy="16" r="9.5" fill="none" stroke="var(--accent-ink)" stroke-opacity=".3"/><circle cx="16" cy="16" r="4.5" fill="var(--accent-ink)"/><circle cx="20" cy="9" r="1.6" fill="var(--accent-ink)" fill-opacity=".5"/></svg>';

/* ---------- API ---------- */

/* Parlor's Android app bundles this page (it's the app's own, at https://localhost) and the server is elsewhere.
   The app passes the page's GETs on to the server itself (pictures and streams too, cookies kept by the app);
   anything with a body goes through native HTTP, since a page's request can't hand its body to the app. */
const NATIVE = !!window.Capacitor?.isNativePlatform?.();
/* Parlor's desktop app (desktop/main.js) bundles this page too, and passes every request on itself, bodies included;
   it plays films and episodes in mpv when the computer has it (library.js). */
const DESKTOP = !!window.parlorDesktop;
const APPED = NATIVE || DESKTOP; // the page is the app's own: the server is elsewhere and chosen in the app
let SERVER = ""; // the app's server, once known (pwa.js)

async function nativeSend(path, { method, headers, body }) {
  let r;
  try {
    r = await Capacitor.Plugins.CapacitorHttp.request({ url: SERVER + path, method, headers, data: body, responseType: "text" });
  } catch (err) { throw new TypeError(err.message || "Can't reach Parlor"); } // like fetch: no network
  return new Response([204, 304].includes(r.status) ? null : typeof r.data === "string" ? r.data : JSON.stringify(r.data), { status: r.status, headers: r.headers });
}

async function api(path, options = {}) {
  // the server only accepts changes carrying this header (it blocks other websites)
  let res;
  try {
    const ask = { ...options, headers: { ...options.headers, "X-Requested-With": "parlor" } };
    res = NATIVE && (options.method || "GET") !== "GET" ? await nativeSend(path, ask) : await fetch(path, ask);
    if (res.headers.get("x-parlor-offline")) throw new TypeError("Can't reach Parlor"); // the app's way of saying the server didn't answer
  } catch (err) {
    serverGone(); // no network, or no server: pwa.js says so and watches for it to come back
    throw Object.assign(err, { offline: true });
  }
  serverBack();
  const body = await res.json().catch(() => ({}));
  if (res.status === 423) {
    showUnlock();
    throw new Error("Parlor is locked");
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    showGate();
    throw new Error("Sign in first");
  }
  if (!res.ok) throw Object.assign(new Error(body.detail || `${res.status} ${res.statusText}`), { status: res.status });
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
  spoilers: false, // episode lists hide the still and summary of what you haven't watched
};
function pref(key) {
  try {
    const v = JSON.parse(localStorage.getItem("parlor:" + key) ?? localStorage.getItem("yeerr:" + key));  // compat: settings saved before the rename
    return v ?? PREF_DEFAULTS[key];
  } catch { return PREF_DEFAULTS[key]; }
}
function setPref(key, value) { try { localStorage.setItem("parlor:" + key, JSON.stringify(value)); } catch { /* private mode */ } }

/* ---------- rows from the last visit ---------- */

// Draws a row from its last answer (this account, this device) at once, then loads the new one and draws again only if it
// differs. A server that can't answer leaves what was drawn; with nothing drawn the error goes on.
async function lastThen(key, load, draw) {
  const k = `parlor:last:${ME?.id}:${key}`;
  let old = null;
  try { old = JSON.parse(localStorage.getItem(k)); } catch { /* nothing kept, or damaged */ }
  if (old) draw(old);
  let fresh;
  try { fresh = await load(); } catch (err) {
    if (old && (!err.status || err.status >= 500)) return; // no network, or the server's own trouble: what was drawn stays
    try { localStorage.removeItem(k); } catch { /* private mode */ } // signed out, locked or no longer allowed: not theirs to keep
    throw err;
  }
  try { localStorage.setItem(k, JSON.stringify(fresh)); } catch { /* storage full */ }
  if (JSON.stringify(fresh) !== JSON.stringify(old)) draw(fresh);
}
// one row of this account (after one of its titles was removed), or with no key every account's (signing out)
function forgetLast(key) {
  try {
    if (key) localStorage.removeItem(`parlor:last:${ME?.id}:${key}`);
    else Object.keys(localStorage).filter((k) => k.startsWith("parlor:last:")).forEach((k) => localStorage.removeItem(k));
  } catch { /* private mode */ }
}

/* ---------- playing ---------- */

// the part to skip (intro, credits) that second t is inside, from what Jellyfin found; null when it found none
const segmentAt = (segs, t) => (segs || []).find((g) => t >= g.start && t < g.end - 1) || null;
const SKIP_LABEL = { intro: "Skip intro", outro: "Skip credits" };
// Touch: two taps within 300 ms. A side skips 10 s that way, the middle goes full screen, the bar at the bottom is the
// browser's own. A mouse's double-click is full screen, and the click a browser adds after a double-tap is ignored.
function doubleTap(el, skip, full) {
  let last = 0, touched = -1e9;
  el.addEventListener("pointerup", (e) => {
    if (e.pointerType !== "touch" || e.target?.closest?.("button, a, select")) return; // a tap on Skip intro is not half a double-tap
    touched = e.timeStamp;
    const r = el.getBoundingClientRect();
    if (e.timeStamp - last > 300 || e.clientY > r.bottom - 60) { last = e.timeStamp; return; }
    last = 0;
    const side = (e.clientX - r.left) / r.width;
    if (side < 0.33) skip(-10); else if (side > 0.67) skip(10); else full();
  });
  el.addEventListener("dblclick", (e) => { if (e.timeStamp - touched > 600) full(); });
}

/* ---------- building blocks ---------- */

/** YouTube's and TMDB's pictures through Parlor (/api/art keeps them on disk: a screen with no internet still shows them); any other address as it is */
const artUrl = (src) => (/^https:\/\/(i\.ytimg\.com|yt3\.ggpht\.com|image\.tmdb\.org)\//.test(src || "") ? `/api/art?u=${encodeURIComponent(src)}` : src);
const img = (src, cls = "") => (src ? `<img class="${cls}" src="${esc(artUrl(src))}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<div class="blank ${cls}"></div>`);
const section = (title, body) => (body ? `<h2>${esc(title)}</h2>${body}` : "");
const grid = (html, cls = "grid") => (html ? `<div class="${cls}">${html}</div>` : "");
const shelf = (title, body, more) => (body ? `<div class="shelf-head"><h2>${esc(title)}</h2>${more ? `<a class="more" href="${more}">See all</a>` : ""}</div>${body}` : "");
const skeletons = (n, kind = "v", cls = "vgrid") =>
  `<div class="${cls}">${Array.from({ length: n }, () => `<div><div class="skel ${kind}"></div><div class="skel line"></div><div class="skel line short"></div></div>`).join("")}</div>`;
/** a vendored script, loaded once, when a page first needs it */
const scripts = {};
const loadScript = (src) => (scripts[src] ??= new Promise((ok, fail) => {
  const el = Object.assign(document.createElement("script"), { src, onload: ok, onerror: () => { delete scripts[src]; fail(new Error(`Couldn't load ${src}`)); } });
  document.head.append(el);
}));
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
  media: "A film, a show, an album, a game or a book to ask for",
  music: "Artists, albums, songs, playlists or a Spotify link",
  library: "Search your films, shows, music and books",
  settings: "Search videos, movies, shows and music",
  games: "Search videos, movies, shows and music",
  musicreq: "A film, a show, an album, a game or a book to ask for",
  gamereq: "A film, a show, an album, a game or a book to ask for",
  bookreq: "A film, a show, an album, a game or a book to ask for",
};
const SEARCH = { home: "#/search/", watch: "#/yt/search/", media: "#/request/search/", music: "#/music/search/", library: "#/jf/search/", settings: "#/search/", games: "#/search/", musicreq: "#/request/search/", gamereq: "#/request/search/", bookreq: "#/request/search/" };  // one box for everything you can ask for
let ticket = 0, current = "home";
/* a section's row of pages sits under the header, outside what a page paints (drawNav fills it): a page that wants it
   puts subnav() in its paint, and one that doesn't (a film, a player) hides it */
const ROW = "<!--pages-->";
const subnav = () => ROW;
const paint = (t, html) => { if (t === ticket) { stage.innerHTML = html; $("#pages").hidden = !html.includes(ROW); } };
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
  document.querySelector("dialog[open]")?.close();  // Back with a question open: it goes with its page, unanswered
  if (!(PAGES[view] && /search/.test(location.hash))) input.value = "";
  stage.innerHTML = `<div class="note">${SPIN}</div>`;  // the row of pages stays as it was until the page itself paints
  try {
    await entry.fn(t, parts);
    if (t === ticket) tvLand(); // on a TV the focus is never left on nothing (tv.js)
    if (!/\/search\//.test(location.hash) && !keepScroll) scrollTo(0, 0);
  } catch (e) {
    if (t === ticket) {
      stage.innerHTML = `<div class="note"><strong>Couldn't load this</strong>${esc(e.message)}<br><button class="ghost" onclick="route()">Try again</button></div>`;
      $("#pages").hidden = !navAt(location.hash, current).href;  // the page never said: its section's row shows on the section's own pages
    }
  }
  keepScroll = false;
}
let keepScroll = false;
let drawn = {};
function drawNav() {
  const at = navAt(location.hash, current);
  const put = (el, html) => { if (drawn[el] !== html) $(el).innerHTML = drawn[el] = html; }; // unchanged: keep focus where it is
  put(".nav", tabsHtml(at));
  put("#side", sideHtml(at, document.body.classList.contains("tv") ? (location.hash.startsWith("#/tvsearch") ? "search" : "tv") : ""));
  put("#pages", subnavHtml(at));
  $("#back").hidden = !!(at.href && !at.under || at.own || at.group === "home" && !location.hash.startsWith("#/search")); // pages inside a section get Back
}
$("#back").addEventListener("click", () => (history.length > 1 ? history.back() : go("#/")));

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
