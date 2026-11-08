/* The dock: music and audiobooks keep playing while you move around Parlor. A queue you add to, reorder,
   shuffle and repeat; the next song loaded in a second player before this one ends, so it starts without a
   gap; the queue kept across reloads; and the system's media controls (lock screen, headset, keyboard).
   Jellyfin items stream through /api/jf; an audiobook (and any track you stop part way) keeps its place in
   Jellyfin, so another device picks it up there. */

document.body.insertAdjacentHTML("beforeend", `<div class="dock" id="dock" hidden>
  <div class="dock-line" aria-hidden="true"><i id="dockLine"></i></div>
  <a class="dock-art" id="dockArt" tabindex="-1" aria-hidden="true"></a>
  <div class="dock-what"><span class="title"><span class="eq" aria-hidden="true"><i></i><i></i><i></i></span><a id="dockTitle"></a></span><span class="by" id="dockBy"></span></div>
  <div class="dock-ctl">
    <button class="icon-btn" id="dockPrev" aria-label="Previous">${ICON.prev}</button>
    <button class="icon-btn big" id="dockPlay" aria-label="Pause">${ICON.pause}</button>
    <button class="icon-btn" id="dockNext" aria-label="Next">${ICON.next}</button>
  </div>
  <div class="dock-seek"><span id="dockAt">0:00</span><input type="range" id="dockSeek" min="0" max="1000" value="0" step="1" aria-label="Seek"><span id="dockLen">0:00</span></div>
  <div class="dock-end">
    <button class="icon-btn" id="dockList" aria-label="Queue" aria-expanded="false" aria-controls="queuePanel">${ICON.queue}</button>
    <button class="icon-btn" id="dockClose" aria-label="Stop and close">${ICON.x}</button>
  </div>
  <section class="qpanel" id="queuePanel" hidden aria-label="Queue">
  <header><div><h2>Queue</h2><span id="queueSum"></span></div>
    <button class="icon-btn" id="queueShuffle" aria-pressed="false" aria-label="Shuffle">${ICON.shuffle}</button>
    <button class="icon-btn" id="queueRepeat" aria-pressed="false" aria-label="Repeat: off">${ICON.repeat}</button>
    <button class="ghost" id="queueClear">Clear</button>
    <button class="icon-btn" id="queueClose" aria-label="Close the queue">${ICON.x}</button></header>
  <ol class="qlist" id="queueList"></ol>
  <p class="vh" id="queueSaid" aria-live="polite"></p>
  </section>
</div>`);

const REPEATS = ["off", "all", "one"];
// two players: `audio` plays, `spare` loads the next song; they swap when a song ends
// `playing` is the item whose stream is in `audio`: positions are only ever saved against it
const dock = { queue: [], at: -1, audio: new Audio(), spare: new Audio(), playing: null, next: null, order: null, repeat: "off",
  saved: 0, kept: 0, resume: 0, played: false, seeking: false, token: 0 };
dock.audio.preload = dock.spare.preload = "auto";
const byId = (id) => document.getElementById(id);

/** listen to whichever player is playing (the spare's own events are ignored) */
function dockOn(type, fn) {
  for (const el of [dock.audio, dock.spare]) el.addEventListener(type, (e) => { if (e.target === dock.audio) fn(e); });
}

/** play a list of items from `at`; an item is { title, by, image, href, runtime, src } or { jf: Jellyfin id, keep: true for audiobooks } */
function dockPlay(queue, at = 0, shuffle = !!dock.order) {
  if (!queue.some(Boolean)) return;
  dockHalt();
  dock.queue = queue.filter(Boolean).map((x) => ({ ...x })); // copies: the same song can be queued twice, and each keeps its own place
  dock.order = null;
  dock.at = Math.max(0, Math.min(at, dock.queue.length - 1));
  if (shuffle) dockShuffle(true, false);
  dockGo(dock.at);
}

/** which song comes after this one: the next in the queue, the first again on repeat all, none at the end */
function dockAfter() {
  if (dock.at + 1 < dock.queue.length) return dock.at + 1;
  return dock.repeat === "all" && dock.queue.length ? 0 : -1;
}

async function dockGo(i, { start: from } = {}) {
  if (i < 0 || i >= dock.queue.length) return;
  dockSave();
  const token = ++dock.token, x = dock.queue[i];
  const ready = dock.next?.item === x && dock.next.src ? dock.next : null;
  dock.next = null;
  dock.playing = null; // until the new song's address is in: the old song's time isn't the new one's
  dock.audio.pause();
  dock.at = i;
  dock.saved = 0;
  dock.played = false;
  dock.resume = 0;
  drawDock(x);
  dockChanged(false);
  document.querySelectorAll("video").forEach((v) => v.pause()); // one thing plays at a time
  if (ready) { // loaded already in the spare player: swap, and it starts at once
    const old = dock.audio;
    [dock.audio, dock.spare] = [dock.spare, old];
    dockEmpty(old);
    dock.playing = x;
    dock.audio.play().catch(() => {});
    return dockStarted(x);
  }
  dockEmpty(dock.spare);
  let src = x.src, start = from || 0;
  const saved = offFind(x); // a copy on this device plays from there, network or not (offline.js)
  if (saved) {
    src = (await offBlob(saved)) || src;
    if (token !== dock.token) return;
  }
  if (!src && x.jf) {
    try {
      const p = await post(`/api/jf/playback/${x.jf}`, x.keep ? {} : { start: from || 0 }); // an audiobook resumes; a song starts at the top
      src = p.url;
      start = p.position || 0;
      x.runtime ||= p.runtime;
    } catch (err) {
      if (token === dock.token) { toast(err.message); if (dockAfter() > i) dockGo(i + 1); }
      return;
    }
  }
  if (token !== dock.token) return;
  dock.audio.src = src;
  dock.playing = x;
  if (start) dock.audio.addEventListener("loadedmetadata", () => { dock.audio.currentTime = start; }, { once: true });
  dock.audio.play().catch(() => {}); // a browser that blocks autoplay leaves it paused; the button starts it
  dockStarted(x);
}

function dockEmpty(el) {
  el.pause();
  if (el.hasAttribute("src")) { el.removeAttribute("src"); el.load(); } // lets go of the stream
}

function dockStarted(x) {
  dockKeep();
  dockKeys();
  session.meta(x);
}

/* the lock screen, notification and headset: the browser's Media Session, or in the Android app its native one
   (a media notification that also keeps the app playing in the background), which fetches artwork itself */
const appSession = () => (NATIVE ? window.Capacitor?.Plugins?.MediaSession : null);
const session = {
  meta(x) {
    const app = appSession(), art = x?.image && !x.image.startsWith("blob:") ? new URL(x.image, app ? SERVER + "/" : location.href).href : null;
    const info = x && { title: x.title || "", artist: x.by || "", album: x.album || "", artwork: art ? [{ src: art, sizes: "600x600" }] : [] };
    if (app) return Promise.resolve(app.setMetadata(info || { title: "" })).catch(() => {});
    if ("mediaSession" in navigator) navigator.mediaSession.metadata = info ? new MediaMetadata(info) : null;
  },
  action(name, fn) {
    const app = appSession();
    if (app) Promise.resolve(app.setActionHandler({ action: name }, fn)).catch(() => {}); // the plugin answers with an id, not a promise
    else if ("mediaSession" in navigator) { try { navigator.mediaSession.setActionHandler(name, fn); } catch { /* an action this browser lacks */ } }
  },
  state(playing) {
    const app = appSession(), state = playing == null ? "none" : playing ? "playing" : "paused";
    if (app) Promise.resolve(app.setPlaybackState({ playbackState: state })).catch(() => {});
    else if ("mediaSession" in navigator) navigator.mediaSession.playbackState = state;
  },
  position(duration, position, playbackRate) {
    const app = appSession();
    if (app) return Promise.resolve(app.setPositionState({ duration, position, playbackRate })).catch(() => {});
    try { navigator.mediaSession?.setPositionState?.({ duration, position, playbackRate }); } catch { /* not every browser has it */ }
  },
};

/** the next song loads into the spare player while this one finishes (audiobooks resume where they were, so they aren't) */
async function dockPreload() {
  const x = dock.queue[dockAfter()];
  if (!x || x === dock.queue[dock.at] || x.keep || dock.next?.item === x) return;
  const token = dock.token, next = (dock.next = { item: x }), saved = offFind(x);
  let src = (saved && (await offBlob(saved))) || x.src;
  if (!src && x.jf) {
    try {
      const p = await post(`/api/jf/playback/${x.jf}`, { start: 0 });
      src = p.url;
      x.runtime ||= p.runtime;
    } catch { return; } // it'll be asked for again when it starts
  }
  if (token !== dock.token || dock.next !== next) return;
  next.src = src;
  dock.spare.src = src;
  dock.spare.load();
}

function drawDock(x) {
  byId("dock").hidden = false;
  document.body.classList.add("docked");
  byId("dockArt").innerHTML = x.image ? `<img src="${esc(x.image)}" alt="">` : `<span class="blank"></span>`;
  byId("dockArt").href = byId("dockTitle").href = x.href || "#/jf";
  byId("dockTitle").textContent = x.title || "";
  byId("dockBy").textContent = x.by || "";
  byId("dockLen").textContent = clock(x.runtime || 0);
}

/** where you are, saved to Jellyfin: audiobooks always, songs once they're played through */
function dockSave(ended = false) {
  const x = dock.playing, a = dock.audio;
  if (!x?.jf || !a.src) return;
  const at = a.currentTime || 0, len = a.duration || x.runtime || 0;
  if (!dock.played && (ended || (len && at / len > 0.95))) {
    dock.played = true;
    post("/api/jf/progress", { id: x.jf, played: true }).catch(() => {});
  } else if (x.keep && !dock.played && Math.abs(at - dock.saved) >= 5) {
    dock.saved = at;
    post("/api/jf/progress", { id: x.jf, position: Math.floor(at) }).catch(() => {});
  }
}

/** what's playing stops (its place saved); the queue stays */
function dockHalt() {
  dockSave();
  dock.token++;
  dock.playing = dock.next = null;
  dockEmpty(dock.audio);
  dockEmpty(dock.spare);
}

function dockStop() {
  dockHalt();
  dock.queue = [];
  dock.order = null;
  dock.at = -1;
  byId("dock").hidden = true;
  queueShow(false);
  document.body.classList.remove("docked", "dock-playing");
  session.state(null);
  session.meta(null);
  dockKeep();
}

/* ---------- the queue ---------- */

/** add songs after the one playing (`next`) or at the end; with nothing playing they start */
function dockAdd(items, next = false) {
  items = items.filter(Boolean).map((x) => ({ ...x }));
  if (!items.length) return;
  if (dock.at < 0) return dockPlay(items, 0, false);
  const now = dock.queue[dock.at];
  const put = (list, after) => list.splice(next ? after + 1 : list.length, 0, ...items);
  put(dock.queue, dock.at);
  if (dock.order) put(dock.order, dock.order.indexOf(now));
  dockChanged();
  toast(next ? (items.length === 1 ? `“${items[0].title}” plays next` : `${plural(items.length, "song")} play next`) : `Added ${plural(items.length, "song")} to the queue`);
}

/** move the song at `from` to `to`; the one playing carries on */
function dockMove(from, to) {
  const q = dock.queue;
  if (from === to || !(from >= 0 && to >= 0 && from < q.length && to < q.length)) return;
  const now = q[dock.at];
  q.splice(to, 0, ...q.splice(from, 1));
  dock.at = q.indexOf(now);
  dockChanged();
}

/** take a song out; taking the one playing moves on to the next (and closes the dock with nothing left) */
function dockRemove(i) {
  const x = dock.queue[i];
  if (!x) return;
  if (dock.queue.length === 1) return dockStop();
  const playing = i === dock.at, going = playing && !dock.audio.paused;
  if (playing) { dockHalt(); dock.at = -1; }
  dock.queue.splice(i, 1);
  dock.order?.splice(dock.order.indexOf(x), 1);
  if (!playing) {
    if (i < dock.at) dock.at--;
    return dockChanged();
  }
  const end = i >= dock.queue.length, at = end ? (dock.repeat === "all" ? 0 : dock.queue.length - 1) : i;
  if (going && !(end && dock.repeat !== "all")) return dockGo(at);
  dock.at = at; // paused, or the end of the queue: it waits for the play button
  drawDock(dock.queue[at]);
  dockChanged();
}

/** everything but the song playing goes */
function dockClear() {
  const now = dock.queue[dock.at];
  if (!now) return;
  dock.queue = [now];
  dock.order &&= [now];
  dock.at = 0;
  dockChanged();
}

function shuffled(list) {
  const a = list.slice();
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}

/** shuffle: the song playing comes first and the rest follow in a random order; off puts them back as they were */
function dockShuffle(on = !dock.order, redraw = true) {
  const now = dock.queue[dock.at];
  if (on) {
    dock.order ||= dock.queue.slice();
    dock.queue = now ? [now, ...shuffled(dock.queue.filter((x) => x !== now))] : shuffled(dock.queue);
    dock.at = now ? 0 : -1;
  } else if (dock.order) {
    dock.queue = dock.order;
    dock.order = null;
    dock.at = dock.queue.indexOf(now);
  }
  if (redraw) dockChanged();
}

/** off → all → one */
function dockRepeat(mode = REPEATS[(REPEATS.indexOf(dock.repeat) + 1) % REPEATS.length]) {
  dock.repeat = mode;
  dockChanged();
}

/** after any change to the queue: a spare loaded for a song that's no longer next is dropped; redraw and keep */
function dockChanged(keep = true) {
  if (dock.next && dock.next.item !== dock.queue[dockAfter()]) { dock.next = null; dockEmpty(dock.spare); }
  byId("dockNext").disabled = dockAfter() < 0;
  const sh = byId("queueShuffle"), rp = byId("queueRepeat");
  sh.setAttribute("aria-pressed", String(!!dock.order));
  rp.setAttribute("aria-pressed", String(dock.repeat !== "off"));
  rp.setAttribute("aria-label", `Repeat: ${dock.repeat}`);
  rp.innerHTML = dock.repeat === "one" ? ICON.repeat1 : ICON.repeat;
  drawQueue();
  if (keep) dockKeep();
}

function drawQueue() {
  if (byId("queuePanel").hidden) return;
  // the list is drawn anew: keep keyboard focus on the same kind of control, in the same row (or the one that took its place)
  const had = document.activeElement?.closest?.("#queueList [data-q-grip], #queueList [data-q-play], #queueList [data-q-drop]");
  const kind = had && ["qGrip", "qPlay", "qDrop"].find((k) => k in had.dataset), row = had && +had.dataset[kind];
  const left = dock.queue.slice(dock.at + 1).reduce((n, x) => n + (x.runtime || 0), 0);
  byId("queueSum").textContent = `${plural(dock.queue.length, "song")}${left ? ` · ${clock(left)} to go` : ""}`;
  byId("queueList").innerHTML = dock.queue.map((x, i) => `<li class="qrow${i === dock.at ? " now" : i < dock.at ? " past" : ""}" data-q="${i}">
    <button class="qgrip" data-q-grip="${i}" aria-label="Move ${esc(x.title)}: drag, or up and down arrows">${ICON.grip}</button>
    ${x.image ? `<img src="${esc(x.image)}" alt="" loading="lazy">` : `<span class="blank"></span>`}
    <button class="what linkish" data-q-play="${i}"><span class="title"><span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>${esc(x.title)}</span><span class="by">${esc(x.by)}</span></button>
    <span class="dur">${x.runtime ? clock(x.runtime) : ""}</span>
    <button class="icon-btn" data-q-drop="${i}" aria-label="Remove ${esc(x.title)} from the queue">${ICON.x}</button></li>`).join("");
  if (had && dock.queue.length) {
    const attr = { qGrip: "data-q-grip", qPlay: "data-q-play", qDrop: "data-q-drop" }[kind];
    $(`[${attr}="${Math.min(row, dock.queue.length - 1)}"]`, byId("queueList"))?.focus();
  }
}

function queueShow(open = byId("queuePanel").hidden) {
  byId("queuePanel").hidden = !open;
  byId("dockList").setAttribute("aria-expanded", String(open));
  if (!open) return;
  drawQueue();
  $(".qrow.now", byId("queueList"))?.scrollIntoView({ block: "center" });
}

/* the queue kept on this device, per person: after a reload it's back, paused where you were */
const dockKey = () => (ME ? `parlor:queue:${ME.id}` : null);
function dockKeep() {
  const k = dockKey();
  if (!k) return;
  dock.kept = dock.audio.currentTime || 0;
  try {
    if (!dock.queue.length) return localStorage.removeItem(k);
    const queue = dock.queue.map((x) => (x.image?.startsWith("blob:") ? { ...x, image: null } : x)); // a blob: address dies with the page
    localStorage.setItem(k, JSON.stringify({ queue, at: dock.at, position: Math.floor(dock.audio.src ? dock.kept : dock.resume),
      order: dock.order?.map((x) => dock.queue.indexOf(x)), repeat: dock.repeat }));
  } catch { /* private mode or storage full: the queue just isn't kept */ }
}

function dockRestore() {
  if (dock.queue.length) return;
  let s;
  try { s = JSON.parse(localStorage.getItem(dockKey())); } catch { return; }
  const q = Array.isArray(s?.queue) ? s.queue.filter((x) => x && typeof x === "object" && (typeof x.src === "string" || typeof x.jf === "string")) : [];
  if (!q.length || q.length !== s.queue.length || !(s.at >= 0 && s.at < q.length)) return;
  const order = Array.isArray(s.order) && s.order.length === q.length && new Set(s.order).size === q.length && s.order.every((i) => q[i]) ? s.order.map((i) => q[i]) : null;
  Object.assign(dock, { queue: q, order, at: s.at, repeat: REPEATS.includes(s.repeat) ? s.repeat : "off", resume: Math.max(0, +s.position || 0) });
  drawDock(q[s.at]);
  byId("dockAt").textContent = clock(dock.resume);
  document.body.classList.remove("dock-playing");
  byId("dockPlay").innerHTML = ICON.play;
  byId("dockPlay").setAttribute("aria-label", "Play");
  dockChanged(false);
}

const dockToggle = () => {
  if (!dock.audio.src) return dockGo(dock.at, { start: dock.resume }); // restored after a reload: start it where it was
  dock.audio.paused ? dock.audio.play().catch((err) => toast(err.message)) : dock.audio.pause();
};
const dockBack = () => (dock.audio.currentTime > 4 || dock.at <= 0 ? (dock.audio.currentTime = 0) : dockGo(dock.at - 1));
const dockSkip = () => { const n = dockAfter(); if (n >= 0) dockGo(n); };
const dockLength = () => (dock.audio.duration && isFinite(dock.audio.duration) ? dock.audio.duration : dock.queue[dock.at]?.runtime || 0);

dockOn("play", () => { session.state(true); document.body.classList.add("dock-playing"); byId("dockPlay").innerHTML = ICON.pause; byId("dockPlay").setAttribute("aria-label", "Pause"); });
dockOn("pause", () => { session.state(false); document.body.classList.remove("dock-playing"); byId("dockPlay").innerHTML = ICON.play; byId("dockPlay").setAttribute("aria-label", "Play"); dockSave(); dockKeep(); });
dockOn("ended", () => {
  dockSave(true);
  if (dock.repeat === "one") { dock.played = false; dock.audio.currentTime = 0; return dock.audio.play().catch(() => {}); }
  const n = dockAfter();
  n >= 0 ? dockGo(n) : document.body.classList.remove("dock-playing");
});
dockOn("error", () => {
  if (!dock.audio.src || dock.at < 0) return;
  toast("This one won't play here; skipping");
  if (dockAfter() > dock.at) dockGo(dock.at + 1); // forward only: with repeat all and nothing playable, it mustn't go round forever
});
dockOn("timeupdate", () => {
  const a = dock.audio, len = dockLength();
  if (!dock.seeking) byId("dockSeek").value = len ? Math.round((1000 * a.currentTime) / len) : 0;
  byId("dockLine").style.width = len ? `${(100 * a.currentTime) / len}%` : "0";
  byId("dockAt").textContent = clock(a.currentTime);
  byId("dockLen").textContent = clock(len);
  if (Math.abs(a.currentTime - dock.saved) >= 15) dockSave();
  if (Math.abs(a.currentTime - dock.kept) >= 10) dockKeep();
  if (len && len - a.currentTime < 30) dockPreload();
  if (len && a.currentTime <= len && Math.abs(a.currentTime - (dock.told || 0)) >= 1) { // once a second is plenty
    dock.told = a.currentTime;
    session.position(len, a.currentTime, a.playbackRate);
  }
});
byId("dockPlay").addEventListener("click", dockToggle);
byId("dockNext").addEventListener("click", dockSkip);
byId("dockPrev").addEventListener("click", dockBack);
byId("dockClose").addEventListener("click", dockStop);
byId("dockList").addEventListener("click", () => queueShow());
byId("dockSeek").addEventListener("input", () => { dock.seeking = true; });
byId("dockSeek").addEventListener("change", (e) => {
  dock.seeking = false;
  const len = dockLength();
  if (!len) return;
  if (dock.audio.src) dock.audio.currentTime = (len * e.target.value) / 1000;
  else dock.resume = (len * e.target.value) / 1000;
});
addEventListener("pagehide", () => { dockSave(); dockKeep(); });
document.addEventListener("play", (e) => { if (e.target.tagName === "VIDEO") dock.audio.pause(); }, true); // a video starting pauses the dock

/* the queue panel: play, remove, and reorder by dragging the grip or with the arrow keys on it */
byId("queueClose").addEventListener("click", () => queueShow(false));
byId("queueShuffle").addEventListener("click", () => dockShuffle());
byId("queueRepeat").addEventListener("click", () => dockRepeat());
byId("queueClear").addEventListener("click", dockClear);
byId("queuePanel").addEventListener("keydown", (e) => { if (e.key === "Escape") { queueShow(false); byId("dockList").focus(); } });
byId("queueList").addEventListener("click", (e) => {
  const play = e.target.closest("[data-q-play]"), drop = e.target.closest("[data-q-drop]");
  if (play) dockGo(+play.dataset.qPlay);
  else if (drop) dockRemove(+drop.dataset.qDrop);
});
byId("queueList").addEventListener("keydown", (e) => {
  const g = e.target.closest("[data-q-grip]");
  if (!g || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
  e.preventDefault();
  const from = +g.dataset.qGrip, to = Math.max(0, Math.min(from + (e.key === "ArrowUp" ? -1 : 1), dock.queue.length - 1));
  dockMove(from, to);
  $(`[data-q-grip="${to}"]`, byId("queueList"))?.focus();
  byId("queueSaid").textContent = `${dock.queue[to]?.title || ""}: ${to + 1} of ${dock.queue.length}`;
});
byId("queueList").addEventListener("pointerdown", (e) => {
  const g = e.target.closest("[data-q-grip]");
  if (!g || e.button > 0) return;
  e.preventDefault();
  const list = byId("queueList"), row = g.closest("li"), from = +g.dataset.qGrip, h = row.getBoundingClientRect().height, y0 = e.clientY;
  let to = from;
  g.setPointerCapture(e.pointerId);
  row.classList.add("dragging");
  const move = (ev) => {
    const dy = ev.clientY - y0;
    to = Math.max(0, Math.min(dock.queue.length - 1, from + Math.round(dy / h)));
    row.style.transform = `translateY(${dy}px)`;
    [...list.children].forEach((r, i) => { // the rows it passes make room
      if (r !== row) r.style.transform = i > from && i <= to ? `translateY(${-h}px)` : i < from && i >= to ? `translateY(${h}px)` : "";
    });
  };
  const up = () => {
    for (const [k, f] of [["pointermove", move], ["pointerup", up], ["pointercancel", up]]) g.removeEventListener(k, f);
    from === to ? drawQueue() : dockMove(from, to);
  };
  for (const [k, f] of [["pointermove", move], ["pointerup", up], ["pointercancel", up]]) g.addEventListener(k, f);
});

/** a ⋯ menu by `anchor` to play songs next, add them to the queue or save them for offline; `items()` gives them when one is picked */
function queueMenu(anchor, items) {
  const open = byId("queueMenu");
  open?.close(false);
  if (open?.anchor === anchor) return; // a second press closes it
  const m = document.createElement("div");
  m.className = "popover qmenu";
  m.id = "queueMenu";
  m.anchor = anchor;
  m.setAttribute("role", "menu");
  m.innerHTML = `<button role="menuitem" data-qm="next">${ICON.next}<span>Play next</span></button><button role="menuitem" data-qm="end">${ICON.queue}<span>Add to queue</span></button>`
    + (offWorks ? `<button role="menuitem" data-qm="save">${ICON.dl}<span>Save on this device</span></button>` : "")
    + (tvKnown.length && !tvMe ? `<button role="menuitem" data-qm="tv">${ICON.tv}<span>Play on TV</span></button>` : "");
  document.body.append(m);
  const r = anchor.getBoundingClientRect(), mh = m.offsetHeight;
  m.style.top = `${r.bottom + 4 + mh > innerHeight - 90 ? Math.max(8, r.top - mh - 4) : r.bottom + 4}px`;
  m.style.left = `${Math.max(8, Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 8))}px`;
  anchor.setAttribute("aria-expanded", "true");
  const close = (focus) => { m.remove(); anchor.setAttribute("aria-expanded", "false"); document.removeEventListener("pointerdown", outside, true); if (focus) anchor.focus(); };
  const outside = (e) => { if (!m.contains(e.target) && !anchor.contains(e.target)) close(false); };
  m.close = close;
  document.addEventListener("pointerdown", outside, true);
  m.addEventListener("keydown", (e) => {
    const b = [...m.querySelectorAll("button")], i = b.indexOf(document.activeElement);
    if (e.key === "Escape") close(true);
    else if (["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); b[(i + (e.key === "ArrowDown" ? 1 : b.length - 1)) % b.length].focus(); }
  });
  m.addEventListener("click", (e) => {
    const b = e.target.closest("[data-qm]");
    if (!b) return;
    close(true);
    if (b.dataset.qm === "save") offSave(items());
    else if (b.dataset.qm === "tv") tvChoose(anchor, async () => ({ kind: "queue", id: "0", items: items().map((x) => ({ ...x, image: x.image?.startsWith("blob:") ? null : x.image })) }));
    else dockAdd(items(), b.dataset.qm === "next");
  });
  m.querySelector("button").focus();
}
addEventListener("hashchange", () => byId("queueMenu")?.close(false));

/** the lock screen's and keyboard's media keys drive the dock (a video page takes them while it plays) */
function dockKeys() {
  session.action("play", () => dock.audio.play());
  session.action("pause", () => dock.audio.pause());
  session.action("nexttrack", dockSkip);
  session.action("previoustrack", dockBack);
  session.action("seekbackward", (d) => { dock.audio.currentTime = Math.max(0, dock.audio.currentTime - (d?.seekOffset || 10)); });
  session.action("seekforward", (d) => { dock.audio.currentTime = Math.min(dockLength() || Infinity, dock.audio.currentTime + (d?.seekOffset || 30)); });
  session.action("seekto", (d) => { if (d?.seekTime != null) dock.audio.currentTime = d.seekTime; });
}
