/* Parlor's home on a TV: a hero that follows the focused title, and rows of posters under it. The screen never scrolls:
   the rows slide under a fixed frame, so the focused tile is always in the same place. The TV's graphics chip is the
   slowest in the house and fills a 4K screen, so only transform and opacity ever animate, nothing blurs live (the
   colour wash behind is a 64-pixel picture stretched), and a picture is decoded before it fades in.
   Focus is two numbers (which row, which tile), not "whatever is nearest": it can't get lost, each row remembers its
   place, and Back from a title returns to the tile it was opened from. Keys come from tvDo (tv.js) through tvScreenKey. */

const TVH = { rows: [], r: 0, at: {}, ids: {}, shown: null, timer: 0 };
const onTv = () => document.body.classList.contains("tv");
const tvhSmall = (url) => (url || "").replace(/maxWidth=\d+/, "maxWidth=64"); // Jellyfin resizes it; stretched, it is the blur

/* ---------- what the rows hold ---------- */

const TVH_KIND = { Movie: "Film", Episode: "Episode", Series: "Show", Season: "Season", MusicAlbum: "Album", Audio: "Song", MusicArtist: "Artist", Book: "Book", AudioBook: "Audiobook" };
function tvhFromLibrary(x, wide) {
  const episode = x.type === "Episode";
  return {
    id: x.id, title: episode ? x.series || x.name : x.name, sub: wide && episode ? [epCode(x), x.name].filter(Boolean).join(" · ") : "",
    kind: TVH_KIND[x.type] || "", href: jhref(x, wide), img: wide ? x.backdrop || x.image : x.image, back: x.backdrop || x.image, amb: tvhSmall(x.backdrop || x.image),
    meta: [x.year, (x.artists || []).join(", "), left(x) || (x.runtime && VIDEO.has(x.type) ? runtimeText(x.runtime) : "")].filter(Boolean),
    over: x.overview || "", pct: x.runtime && x.position && !x.played ? Math.min(1, x.position / x.runtime) : 0, go: wide && VIDEO.has(x.type) ? (x.position ? "Resume" : "Play") : "Open",
  };
}
function tvhFromYoutube(v) {
  const thumb = (name) => artUrl(`https://i.ytimg.com/vi/${v.id}/${name}.jpg`);
  return {
    id: v.id, title: v.title, sub: v.channel || "", kind: "Video", href: `#/watch/${esc(v.id)}`, img: artUrl(v.thumb || ytThumb(v.id)),
    back: thumb("hq720"), back2: thumb("mqdefault"), amb: thumb("default"),
    meta: [v.channel, v.duration ? clock(v.duration) : "", since(v.published || v.watched || v.added)].filter(Boolean),
    over: "", pct: v.position && v.duration ? Math.min(1, v.position / v.duration) : 0, go: v.position ? "Resume" : "Play",
  };
}

async function tvhLoad() {
  const watch = !closedTo("watch"), quiet = (p) => Promise.race([p, new Promise((ok) => setTimeout(ok, 8000, null))]).catch(() => null); // one slow service never holds back the rest
  const [jf, cont, feed, later, mine] = await Promise.all([
    libraryOpen() ? quiet(jfHomeLoad()) : null,
    watch ? quiet(api("/api/me/history?unfinished=true&limit=12")) : null,
    watch ? quiet(api("/api/yt/feed?limit=24")) : null,
    watch ? quiet(api("/api/me/later")) : null,
    libraryOpen() ? quiet(api("/api/jf/items?filters=IsFavorite&types=Movie,Series&sort=SortName&limit=24")) : null, // the hearts: this person's list
  ]);
  return { jf, follows: feed ? !!feed.channels : null, cont: cont?.items || [], feed: feed?.channels ? hideShorts(feed.items).slice(0, 16) : [], later: (later?.items || []).slice(0, 16), mine: mine?.items || [] };
}

function tvhRows({ jf, cont = [], feed = [], later = [], mine = [] }) {
  const rows = [], lib = "var(--sky)", yt = "var(--coral)";
  const add = (key, title, accent, shape, items, more) => { if (items.length) rows.push({ key, title, accent, shape, items, more }); }; // more: the row's own page, linked from its name off a TV
  add("continue", "Continue watching", "var(--amber)", "wide", [...(jf?.resume || []).map((x) => tvhFromLibrary(x, true)), ...cont.map(tvhFromYoutube)]);
  add("next", "Next up", lib, "wide", (jf?.next || []).map((x) => tvhFromLibrary(x, true)), "#/jf");
  add("mine", "My list", "var(--coral)", "tall", mine.map((x) => tvhFromLibrary(x, false)));
  jfShelves(jf?.views || []).forEach((v, n) => add(`new:${v.id}`, `New in ${v.name}`, v.collection === "music" ? "var(--amber)" : lib,
    v.collection === "music" ? "square" : "tall", (jf.latest[n] || []).map((x) => tvhFromLibrary(x, false)), `#/jf/view/${esc(v.id)}`));
  add("subs", "New from your subscriptions", yt, "wide", feed.map(tvhFromYoutube), "#/yt/subs");
  add("later", "Watch later", yt, "wide", later.map(tvhFromYoutube), "#/yt/later");
  add("libraries", "Your libraries", lib, "wide", jfShelves(jf?.views || []).map((v) => ({ ...tvhFromLibrary(v, false), img: v.image, kind: "Library", go: "Open" })));
  return rows;
}

/* ---------- drawing ---------- */

const tvhTile = (x, i) => `<a class="tvt" href="${x.href}" tabindex="${onTv() || i ? -1 : 0}" data-i="${i}" aria-label="${esc(x.title)}">
  <span class="tvt-art"><span class="tvt-name">${esc(x.title)}</span>${x.img ? `<img src="${esc(x.img)}" alt="" loading="${i < 7 && onTv() ? "eager" : "lazy"}" decoding="async" onerror="this.remove()">` : ""}
    ${x.pct > 0.004 ? `<span class="tvt-bar"><i style="width:${(100 * x.pct).toFixed(1)}%"></i></span>` : ""}</span>
  <span class="tvt-cap">${esc(x.title)}</span>${x.sub ? `<span class="tvt-sub">${esc(x.sub)}</span>` : ""}</a>`;

async function showTvHome(t) {
  paint(t, `<div class="tvh still${TVH.lite ? " lite" : ""}" id="tvh" tabindex="-1" aria-label="Home">
    <div class="tvh-amb"><img alt=""><img alt=""></div><div class="tvh-art"><img alt=""><img alt=""></div>
    <div class="tvh-hero" id="tvhHero"></div><div class="tvh-clock" id="tvhClock">${esc(tvTime())}</div>
    <div class="tvh-rows"><div class="tvh-stack" id="tvhStack"><section class="tvh-row on"><h2>&nbsp;</h2><div class="tvh-track">${'<span class="tvt wide"><span class="tvt-art skel"></span></span>'.repeat(5)}</div></section></div></div></div>`);
  $("#tvh")?.focus({ preventScroll: true }); // the focus is on this screen from the first frame, never on nothing
  TVH.rows = [];
  TVH.shown = null;
  const draw = (data) => { if (t === ticket && $("#tvh")) tvhDraw(tvhRows(data || {})); };
  await lastThen("tvhome", tvhLoad, draw).catch(() => draw({}));
}

function tvhDraw(rows) {
  const stack = $("#tvhStack"), was = TVH.key, inRail = $("#side").contains(document.activeElement);
  TVH.rows = rows;
  if (!rows.length) {
    $("#tvhHero").innerHTML = `<p class="tvh-kind">Parlor</p><h1>${esc(greeting())}, ${esc(ME?.name || "")}</h1><p class="tvh-over">Nothing to carry on with yet. Open the Library or Watch from the menu on the left, or play something from your phone.</p>`;
    stack.innerHTML = "";
    return tvhRail();
  }
  stack.innerHTML = rows.map((row, r) => `<section class="tvh-row" data-r="${r}" style="--accent:${row.accent}"><h2>${esc(row.title)}</h2>
    <div class="tvh-track ${row.shape}">${row.items.map(tvhTile).join("")}</div></section>`).join("");
  const r = Math.max(0, rows.findIndex((row) => row.key === was));
  const i = (key) => { const n = rows.find((row) => row.key === key).items.findIndex((x) => x.id === TVH.ids[key]); return n < 0 ? TVH.at[key] || 0 : n; }; // the same title, wherever it is now
  for (const row of rows) TVH.at[row.key] = i(row.key);
  $("#tvh").classList.add("still"); // placed, not slid there
  tvhFocus(r, TVH.at[rows[r].key], !inRail);
  requestAnimationFrame(() => requestAnimationFrame(() => $("#tvh")?.classList.remove("still")));
}

/** put the focus on tile i of row r: slide the rows and the row's track so that tile sits in the frame, light the hero */
function tvhFocus(r, i, take = true) {
  const rows = TVH.rows, stack = $("#tvhStack");
  if (!rows.length || !stack) return;
  r = Math.max(0, Math.min(rows.length - 1, r));
  i = Math.max(0, Math.min(rows[r].items.length - 1, i));
  const row = rows[r], el = stack.children[r], track = el.querySelector(".tvh-track"), tile = track.children[i];
  Object.assign(TVH, { r, key: row.key }); // the row by name: Home is drawn again when you come back to it
  TVH.at[row.key] = i;
  TVH.ids[row.key] = row.items[i].id;
  for (const other of stack.children) other.classList.toggle("on", other === el);
  stack.style.transform = `translate3d(0,${-el.offsetTop}px,0)`;
  track.style.transform = `translate3d(${-Math.min(tile.offsetLeft, Math.max(0, track.scrollWidth - el.clientWidth + 48))}px,0,0)`; // + the left margin at the end too: the last tile lifted and glowing stays whole
  if (take) tile.focus({ preventScroll: true });
  clearTimeout(TVH.timer); // the hero waits for the focus to rest: a held arrow doesn't load a picture per tile
  TVH.timer = setTimeout(() => tvhHero(row, row.items[i]), TVH.shown ? 240 : 0);
}

function tvhHero(row, x) {
  const hero = $("#tvhHero");
  if (!hero || TVH.shown === x) return;
  TVH.shown = x;
  hero.style.setProperty("--accent", row.accent);
  hero.innerHTML = `<p class="tvh-kind">${esc([row.title, x.kind].filter(Boolean).join(" · "))}</p><h1>${esc(x.title)}</h1>
    <p class="tvh-meta">${[x.sub && x.kind === "Episode" ? x.sub : "", ...x.meta].filter(Boolean).map((m) => `<span>${esc(m)}</span>`).join("")}</p>
    ${x.over ? `<p class="tvh-over">${esc(x.over)}</p>` : ""}
    <p class="tvh-go">${onTv() ? "<b>" : `<a href="${x.href}" tabindex="-1">`}${x.go === "Open" ? "" : ICON.play}${esc(x.go)}${onTv() ? "</b>" : "</a>"}${x.pct > 0.004 ? `<span class="tvh-prog"><i style="width:${(100 * x.pct).toFixed(1)}%"></i></span>` : ""}</p>`;
  hero.classList.remove("in");
  void hero.offsetWidth; // the entrance plays again
  hero.classList.add("in");
  tvhSwap(".tvh-art", x.back, x.back2);
  tvhSwap(".tvh-amb", x.amb);
}

/** cross-fade a layer's two pictures: the new one is fetched and decoded first, so the fade never stutters on it */
async function tvhSwap(sel, src = "", alt = "") {
  const box = $(sel); // the TV's Home or the site's (home.js): one of them is on the page
  if (!box || box.dataset.src === src) return;
  box.dataset.src = src;
  const [a, b] = box.querySelectorAll("img"), cur = a.classList.contains("on") ? a : b, next = cur === a ? b : a;
  if (src) {
    next.src = src;
    try { await next.decode(); } catch { if (box.dataset.src === src) { box.dataset.src = ""; return alt ? tvhSwap(sel, alt) : cur.classList.remove("on"); } }
    if (box.dataset.src !== src) return; // the focus moved on while it loaded
    next.classList.add("on");
    if (sel === ".tvh-amb" && cur.classList.contains("on")) tvhPace();
  }
  cur.classList.remove("on");
}

/** a screen drawn with no graphics chip's help can't fade a whole 4K picture: count the frames of a fade of the wash,
    and when two fades running come slower than 24 a second, the wash changes at once from then on (.lite, app.css) */
function tvhPace() {
  if (TVH.lite || TVH.pacing || !onTv()) return;
  TVH.pacing = true;
  const t0 = performance.now();
  let n = 0;
  const tick = (now) => {
    if (now - t0 < 500) return void (n++, requestAnimationFrame(tick));
    TVH.pacing = false;
    TVH.slow = n < 12 ? (TVH.slow || 0) + 1 : 0;
    if (TVH.slow < 2) return;
    TVH.lite = true;
    $("#tvh")?.classList.add("lite");
  };
  requestAnimationFrame(tick);
}

const tvhRail = () => ($("#side a[aria-current]") || $("#side a"))?.focus({ preventScroll: true });

/* ---------- keys ---------- */

/** a key on the home screen; true when it was dealt with here */
function tvhKey(key) {
  if (!$("#tvh") || $("#tvAsk")) return false;
  const rows = TVH.rows, r = TVH.r, i = TVH.at[rows[r]?.key] || 0, rail = $("#side").contains(document.activeElement);
  if (rail) return key === "left" || ((key === "right" || key === "back") && rows.length ? (tvhFocus(r, i), true) : false); // nothing is left of the menu (a tile slid off the screen isn't); up and down walk the menu itself
  if (!rows.length) return key === "left" ? (tvhRail(), true) : false;
  if (!$("#tvhStack").contains(document.activeElement)) tvhFocus(r, i); // wherever the focus went, it is back on its tile
  if (key === "left") i > 0 ? tvhFocus(r, i - 1) : tvhRail();
  else if (key === "right") tvhFocus(r, i + 1);
  else if (key === "up" || key === "down") { const to = rows[r + (key === "up" ? -1 : 1)]; if (to) tvhFocus(rows.indexOf(to), TVH.at[to.key] || 0); } // at the first and last row it stays
  else if (key === "back") r || i ? tvhFocus(0, 0) : tvhRail();
  else return false;
  return true;
}

// a tile that takes the focus any other way (the browser's own Tab, a screen reader) is slid into the frame like any other
document.addEventListener("focusin", (e) => {
  const tile = e.target.closest?.("#tvhStack a.tvt"), r = +tile?.closest(".tvh-row").dataset.r;
  if (tile && (r !== TVH.r || +tile.dataset.i !== TVH.at[TVH.key])) tvhFocus(r, +tile.dataset.i, false);
});

/* ---------- a film or a video on a TV: the whole screen, with a strip that shows itself and goes ---------- */

/** the box of the video playing on this TV's page (app.css makes it the whole screen) */
const tvBox = () => (onTv() ? [...document.querySelectorAll("#view video")].find(tvShown)?.closest(".player") : null);
const tvOpts = () => document.body.classList.contains("tvopts");
let tvOsdTimer = 0;

/** show the strip (what's playing, where it is); it goes again by itself while the film plays */
function tvOsd(stay = false) {
  const box = tvBox(), v = box?.querySelector("video");
  if (!box) return;
  v.controls = false; // the browser's own bar wants a mouse
  if (!box.hasAttribute("tabindex")) tvLand([]); // a fresh player: the keys are the player's
  if (!$(".tvosd", box)) {
    box.insertAdjacentHTML("beforeend", `<div class="tvosd"><p class="tvosd-top"><b></b></p>
      <div class="tvosd-bot"><span class="tvosd-at"></span><span class="tvosd-bar"><i></i></span><span class="tvosd-left"></span></div>
      <p class="tvosd-hint">OK pause · ◀ ▶ 10 seconds · ▼ sound, subtitles and more · Back to leave</p></div>`);
  }
  $(".tvosd-top b", box).textContent = $("#view .vinfo h1")?.textContent || "";
  tvOsdTime(v);
  box.classList.add("osd");
  clearTimeout(tvOsdTimer);
  if (!stay) tvOsdTimer = setTimeout(() => { if (!v.paused && !tvOpts()) box.classList.remove("osd"); }, 4000);
}
function tvOsdTime(v) {
  const osd = v.closest(".player")?.querySelector(".tvosd"), d = v.duration;
  if (!osd) return;
  $(".tvosd-at", osd).textContent = clock(v.currentTime || 0);
  $(".tvosd-left", osd).textContent = isFinite(d) && d ? `-${clock(Math.max(0, d - v.currentTime))}` : "";
  $(".tvosd-bar i", osd).style.transform = `scaleX(${isFinite(d) && d ? Math.min(1, v.currentTime / d) : 0})`;
}
for (const k of ["loadedmetadata", "pause", "seeking", "waiting"]) document.addEventListener(k, (e) => { if (e.target === tvBox()?.querySelector("video")) tvOsd(k === "pause"); }, true);
document.addEventListener("play", (e) => { if (e.target === tvBox()?.querySelector("video")) tvOsd(); }, true);
document.addEventListener("timeupdate", (e) => { if (e.target.closest?.(".player.osd")) tvOsdTime(e.target); }, true);

/** the player's options (sound, subtitles, quality): shown over the film, the arrows stay inside them */
function tvOptions(on) {
  const tools = $("#view .ptools");
  document.body.classList.toggle("tvopts", !!(on && tools));
  if (on && tools) { tvOsd(true); [...tools.querySelectorAll(TV_FOCUSABLE)].find(tvShown)?.focus({ preventScroll: true }); } else { tvBox()?.focus({ preventScroll: true }); tvOsd(); }
}
addEventListener("hashchange", () => document.body.classList.remove("tvopts"));

/** a question over the film (Up next, an error with Try again): the arrows and OK are its buttons' */
const tvOver = () => [...(tvBox()?.querySelectorAll(".overlay") || [])].find((el) => tvShown(el) && el.querySelector("button, a[href]"));

/** where the arrows may go: a question on the screen, a player's question or options, or (null) anywhere */
const tvScope = () => $("#tvAsk") || (tvOpts() ? $("#view .ptools") : tvOver() || null);

function tvPlayerKey(key) {
  const box = tvBox(), v = box?.querySelector("video"), over = tvOver();
  if (!box || $("#tvAsk")) return false;
  if (tvOpts()) { // in the options: the arrows walk them; Up off the top, or Back, returns to the film
    const was = document.activeElement;
    if (key === "back") return tvOptions(false), true;
    if (key === "enter") return false;
    tvMove(key);
    if (key === "up" && document.activeElement === was) tvOptions(false);
    return true;
  }
  if (over) {
    if (key === "back") return false;
    if (!over.contains(document.activeElement)) over.querySelector("button, a[href]").focus({ preventScroll: true }); // the first press lands on it
    else if (key !== "enter") tvMove(key);
    return key !== "enter"; // OK presses what the focus is on (tv.js)
  }
  const skip = [...box.querySelectorAll(".skip, .skip-toast button")].find(tvShown); // Skip intro, Undo a skip: OK takes it
  if (key === "left" || key === "right") v.currentTime = Math.max(0, v.currentTime + (key === "left" ? -10 : 10));
  else if (key === "enter") skip ? skip.click() : v.paused ? tvPlay(v) : v.pause();
  else if (key === "down") return tvOptions(true), true;
  else if (key !== "up") return false; // Back leaves the film
  tvOsd(v.paused);
  return true;
}

/** a TV decodes film without its graphics chip's help: when it drops frames, ask Jellyfin for a lighter stream, and
    start the next film at what this TV kept up with (its own choice in the options counts the same) */
setInterval(() => {
  const v = $("#jfVideo"), q = $("#jfQuality"), now = v?.getVideoPlaybackQuality?.();
  if (!onTv() || !now || v.paused || v.ended || v.seeking || !q || q.disabled) return void (tvDrops = null);
  const was = tvDrops?.v === v ? tvDrops : null, lighter = (to) => { q.value = to; q.dispatchEvent(new Event("change", { bubbles: true })); tvDrops = null; };
  if (!was && pref("tvQuality") && !q.value && [...q.options].some((o) => o.value === pref("tvQuality"))) return lighter(pref("tvQuality"));
  const frames = now.totalVideoFrames - (was?.total ?? now.totalVideoFrames), dropped = now.droppedVideoFrames - (was?.dropped ?? now.droppedVideoFrames);
  tvDrops = { v, total: now.totalVideoFrames, dropped: now.droppedVideoFrames, bad: !was ? 0 : frames < 40 || dropped / frames > 0.15 ? was.bad + 1 : 0 }; // under 8 frames a second is not keeping up either
  if (tvDrops.bad < 2 || q.selectedIndex >= q.options.length - 1) return; // two bad stretches running, and something lighter to ask for
  lighter(q.options[Math.max(q.selectedIndex + 1, 2)].value); // 8 Mbps at first: 1080p, which this TV keeps up with
  toast(`This TV was dropping frames: now playing at ${q.options[q.selectedIndex].text}`);
}, 5000);
document.addEventListener("change", (e) => { if (e.target.id === "jfQuality" && onTv()) setPref("tvQuality", e.target.value); });
let tvDrops = null;

/* ---------- search on a TV: a keyboard on the screen (a phone's typing lands here too) ---------- */

const TV_KEYS = [..."abcdefghijklmnopqrstuvwxyz1234567890"];
let tvSearchTimer = 0;
async function showTvSearch(t, [q = ""]) {
  if (!onTv()) return go(`#/search/${encodeURIComponent(q)}`);
  paint(t, `<div class="tvsearch" id="tvSearch"><div class="tvkeys">
      <p class="tvq" id="tvq" aria-live="polite"></p>
      <div class="tvkeygrid">${TV_KEYS.map((k) => `<button data-k="${k}">${k}</button>`).join("")}
        <button data-k=" " class="wide">space</button><button data-k="del" class="wide" aria-label="Delete">⌫</button><button data-k="clear" class="wide">clear</button></div>
      <a class="ghost" id="tvSearchAll" href="#/search/">Search YouTube and everything else</a></div>
    <div class="tvfound" id="tvFound"></div></div>`);
  const set = (text) => {
    q = text.slice(0, 80);
    $("#tvq").textContent = q;
    $("#tvq").classList.toggle("empty", !q);
    $("#tvSearchAll").href = `#/search/${encodeURIComponent(q.trim())}`;
    $("#tvSearchAll").hidden = !q.trim();
    clearTimeout(tvSearchTimer);
    tvSearchTimer = setTimeout(async () => {
      const found = q.trim() && libraryOpen() ? await api(`/api/jf/items?search=${encodeURIComponent(q.trim())}&types=Movie,Series,Episode,MusicAlbum,MusicArtist&limit=18`).catch(() => null) : null;
      if (t !== ticket || !$("#tvFound")) return;
      const items = found?.items || found || [];
      $("#tvFound").innerHTML = !q.trim() ? `<p class="dim">Type a title with the arrows and OK, or type on your phone's remote.</p>`
        : items.length ? `<div class="grid jgrid">${items.map((x) => jcard(x)).join("")}</div>` : `<p class="dim">Nothing in the library for “${esc(q.trim())}”.</p>`;
    }, 350);
  };
  $("#tvSearch").addEventListener("click", (e) => {
    const k = e.target.closest("[data-k]")?.dataset.k;
    if (k) set(k === "del" ? q.slice(0, -1) : k === "clear" ? "" : q + k);
  });
  showTvSearch.set = set;
  set(q);
  $("#tvSearch [data-k]").focus({ preventScroll: true });
}
page("tvsearch", showTvSearch, "home");

/** text typed on a phone's remote: into the box the focus is in, else it is a search */
function tvType(text, enter) {
  const box = document.activeElement?.matches?.("input:not([type=range]):not([type=checkbox]), textarea") ? document.activeElement : null;
  if (box) {
    box.value = text;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    if (enter) box.form?.requestSubmit();
  } else if (enter) go(`#/search/${encodeURIComponent(text.trim())}`);
  else if ($("#tvSearch")) showTvSearch.set(text);
  else go(`#/tvsearch/${encodeURIComponent(text)}`);
}

/* ---------- a TV nobody is using: the picture goes dark, with a clock that moves (an OLED keeps what stands still) ---------- */

let tvIdleAfter = 5 * 60e3, tvIdleTimer = 0;
const tvTime = () => new Date().toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
function tvAwake() {
  clearTimeout(tvIdleTimer);
  if (!onTv()) return false;
  tvIdleTimer = setTimeout(tvIdle, tvIdleAfter);
  const idle = $("#tvIdle");
  idle?.remove();
  return !!idle; // the key that woke it does nothing else
}
function tvIdle() {
  const v = [...document.querySelectorAll("#view video")].find(tvShown); // a film, a video, a saved or private one
  if (!onTv() || $("#gate")?.childElementCount || (v && !v.paused)) return tvAwake(); // a film playing is not idle
  if (!$("#tvIdle")) document.body.insertAdjacentHTML("beforeend", `<div class="tvidle" id="tvIdle"><div><b></b><span></span></div></div>`);
  const card = $("#tvIdle div"), song = dock.queue.length && !dock.audio.paused ? dock.queue[dock.at] : null;
  $("b", card).textContent = tvTime();
  $("span", card).textContent = song ? [song.title, song.by].filter(Boolean).join(" · ") : new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  card.style.transform = `translate3d(${Math.round((Math.random() - 0.5) * 560)}px,${Math.round((Math.random() - 0.5) * 300)}px,0)`; // somewhere else each minute
  tvIdleTimer = setTimeout(tvIdle, 60e3);
}
if (TV_MODE) tvAwake();
setInterval(() => { const c = $("#tvhClock"), now = tvTime(); if (c && c.textContent !== now) c.textContent = now; }, 15e3);
document.addEventListener("playing", () => { if (onTv()) tvAwake(); }, true); // a cast wakes the screen

/** in the rail, Up and Down walk its links and stop at the ends: the nearest thing under the last one is never what was meant */
function tvRailKey(key) {
  const links = [...document.querySelectorAll("#side a")].filter(tvShown), at = links.indexOf(document.activeElement);
  if (at < 0 || (key !== "up" && key !== "down")) return false;
  links[at + (key === "up" ? -1 : 1)]?.focus({ preventScroll: true });
  return true;
}

/** every key on a TV asks the screens here first (tv.js tvDo): true means the generic arrows stay out of it */
function tvScreenKey(key) {
  return tvAwake() || tvPlayerKey(key) || tvRailKey(key) || tvhKey(key);
}

/* ---------- Who's watching: the people this TV can act as (tv.py); picked, the TV starts again as them ---------- */

async function showWho(t) {
  const p = tvMe && await api(`/api/tv/${tvMe.id}/people`).catch(() => null);
  if (t !== ticket) return;
  if (!p || p.people.length < 2) return go("#/");
  tvPeople = p;
  try { sessionStorage.setItem("parlor:who", "1"); } catch { /* no storage */ } // asked: the restart after a pick doesn't ask again
  const now = p.viewer ?? p.people.find((x) => x.owner)?.id;
  paint(t, `<div class="tvwho" id="tvWho"><h1>Who's watching?</h1>
    <div class="tvwho-people" id="tvWhoPeople">${p.people.map((x) => `<button data-who="${x.id}"${x.id === now ? ' aria-current="true"' : ""}>${avatar(x.name)}<b>${esc(x.name)}</b><small>${x.pin ? `${ICON.lock}PIN` : "&nbsp;"}</small></button>`).join("")}
      <a class="tvwho-add" href="#/tvremote"><span class="avatar">+</span><b>Add someone</b><small>with their phone</small></a></div>
    <div class="tvwho-pin" id="tvWhoPin" hidden><p id="tvWhoFor"></p><div class="tvwho-dots" id="tvWhoDots" aria-live="polite"></div>
      <div class="tvwho-keys">${[1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => `<button data-d="${d}">${d}</button>`).join("")}<button data-d="del" aria-label="Delete">⌫</button><button data-d="0">0</button><button data-d="ok" class="primary">OK</button></div>
      <p class="tvwho-said" id="tvWhoSaid" role="alert"></p></div></div>`);
  let chosen = null, pin = "";
  const dots = () => { $("#tvWhoDots").textContent = "•".repeat(pin.length) || "····"; $("#tvWhoDots").classList.toggle("empty", !pin); };
  const pick = async (id, withPin) => {
    try {
      await post(`/api/tv/${tvMe.id}/viewer`, { user_id: id, ...(withPin ? { pin: withPin } : {}) });
      tvRestart(); // as them, at Home
    } catch (err) {
      pin = "";
      if (err.status === 403 && !withPin) { // they have a PIN: its pad
        chosen = id;
        $("#tvWhoPeople").hidden = true;
        $("#tvWhoPin").hidden = false;
        $("#tvWhoFor").textContent = `${p.people.find((x) => x.id === id).name}'s PIN`;
        $("#tvWhoPin [data-d='5']").focus({ preventScroll: true });
      } else $("#tvWhoSaid").textContent = err.message;
      dots();
    }
  };
  const key = (d) => {
    if (d === "ok") return pin.length >= 4 ? pick(chosen, pin) : ($("#tvWhoSaid").textContent = "A PIN is 4 to 8 digits");
    pin = d === "del" ? pin.slice(0, -1) : (pin + d).slice(0, 8);
    $("#tvWhoSaid").textContent = "";
    dots();
  };
  $("#tvWho").addEventListener("click", (e) => {
    const who = e.target.closest("[data-who]"), d = e.target.closest("[data-d]");
    if (who) pick(+who.dataset.who);
    else if (d) key(d.dataset.d);
  });
  $("#tvWho").addEventListener("keydown", (e) => { if (!$("#tvWhoPin").hidden && /^[0-9]$/.test(e.key)) key(e.key); }); // a keyboard's digits, or a remote with number keys
  ($("#tvWho [aria-current]") || $("#tvWho [data-who]")).focus({ preventScroll: true });
}
page("who", showWho, "home");

/* ---------- Add a remote: a code and a square that any account's phone opens, to work this TV and be one of its people ---------- */

let tvRemoteTimer = 0;
async function showTvRemote(t) {
  if (!tvMe) return go("#/");
  clearTimeout(tvRemoteTimer);
  let got = null, error = "";
  try { got = await post("/api/tv/remote-code"); } catch (err) { error = err.message; }
  if (t !== ticket) return;
  const code = got?.code || "";
  paint(t, `<div class="tvlink" id="tvLink">
    <div class="tvlink-text"><p class="tvh-kind">Add a remote</p><h1>Use your phone as the remote</h1>
      <p>Point your phone's camera at the square, or open Parlor on your phone, go to <b>You → TVs</b> and enter this code:</p>
      <div class="tvcode" id="tvLinkCode" role="img" aria-label="Code ${esc(code.split("").join(" "))}">${esc(code.slice(0, 4))}<i></i>${esc(code.slice(4))}</div>
      <p class="dim">${error ? esc(error) : "Any account in Parlor can add itself: it works this TV from its phone and shows in Who's watching. The code works once, for five minutes; a new one shows up by itself."}</p>
      <div class="tvlink-people" id="tvLinkPeople"></div>
      <button class="primary" id="tvLinkNew">New code</button></div>
    ${got?.qr ? `<div class="tvqr tvlink-qr" role="img" aria-label="The same code as a square to scan">${got.qr}</div>` : ""}</div>`);
  showTvRemote.people();
  $("#tvLinkNew").addEventListener("click", () => route());
  tvRemoteTimer = setTimeout(() => location.hash.startsWith("#/tvremote") && route(), Math.max(10, (got?.expires || 10) - 5) * 1000);
}
/** who can work this TV now: a new face shows the moment a phone adds itself (and the used code is replaced) */
showTvRemote.people = () => {
  const box = $("#tvLinkPeople");
  if (!box) return;
  const was = box.dataset.n, now = String(tvPeople?.people.length || 0);
  box.innerHTML = (tvPeople?.people || []).map((x) => `<span>${avatar(x.name)}<b>${esc(x.name)}</b></span>`).join("");
  box.dataset.n = now;
  if (was && was !== now) route(); // that code was used: show a fresh one
};
page("tvremote", showTvRemote, "you");
