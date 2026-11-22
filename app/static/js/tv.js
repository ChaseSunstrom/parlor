/* Parlor on a TV (at /tv, or the Android app on Android TV): a 10-foot layout driven by the remote's arrows, OK and
   Back; a pairing code instead of a password; and the event stream that brings casts and presses from the account's
   phones, with what it's playing sent back. Everything that changes the TV goes through tvDo: its own remote's
   keys, media keys, and a phone's commands. The phone's side (TVs, the remote, On TV) is remote.js; tv.py is the server's. */

const TV_MODE = location.pathname.replace(/\/+$/, "") === "/tv"
  || (NATIVE && navigator.maxTouchPoints === 0 && !matchMedia("(any-pointer: coarse)").matches); // the Android app on a TV
let tvMe = null; // this TV's pairing, on a TV
const tvFit = () => document.documentElement.style.setProperty("zoom", String(innerWidth >= 1500 ? innerWidth / 1280 : 1)); // 10-foot: a 1920 screen lays out like 1280
if (TV_MODE) { document.body.classList.add("tv"); tvFit(); addEventListener("resize", tvFit); }

/* ---------- moving with the arrows: to the nearest thing in that direction ---------- */

const TV_FOCUSABLE = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
const tvShown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden" && !el.closest("[hidden], [inert]");

/** on a TV the focus is never on nothing: when a page has just painted, or what held the focus is gone, it lands on
    the page's row of pages or the first thing in the page (the rail only when there is nothing else) */
function tvLand(all) {
  if (!all && (!document.body.classList.contains("tv") || $("#tvAsk") || (document.activeElement !== document.body && tvShown(document.activeElement)))) return;
  const box = tvBox(); // a film: the keys are the player's, not a button's under it
  if (box && !tvScope()) { box.tabIndex = -1; return box.focus({ preventScroll: true }); }
  all ||= [...(tvScope() || document).querySelectorAll(TV_FOCUSABLE)].filter(tvShown);
  (all.find((el) => el.matches("#pages [aria-current]") || el.closest("#view")) || all[0])?.focus({ preventScroll: true });
}

function tvMove(dir) {
  const all = [...(tvScope() || document).querySelectorAll(TV_FOCUSABLE)].filter(tvShown); // a question or a player's options up: only those
  const cur = all.includes(document.activeElement) ? document.activeElement : null;
  if (!cur) return tvLand(all);
  const a = cur.getBoundingClientRect(), side = dir === "left" || dir === "right";
  let best = null, score = Infinity;
  for (const el of all) {
    if (el === cur || cur.contains(el) || el.contains(cur)) continue;
    const b = el.getBoundingClientRect();
    const ahead = dir === "left" ? b.right <= a.left + 2 : dir === "right" ? b.left >= a.right - 2 : dir === "up" ? b.bottom <= a.top + 2 : b.top >= a.bottom - 2;
    if (!ahead) continue;
    const gap = side ? Math.max(0, dir === "left" ? a.left - b.right : b.left - a.right) : Math.max(0, dir === "up" ? a.top - b.bottom : b.top - a.bottom);
    const off = side ? Math.max(0, b.top - a.bottom, a.top - b.bottom) : Math.max(0, b.left - a.right, a.left - b.right); // 0 when they line up
    const s = gap + off * 3; // in line first, then near
    if (s < score) { score = s; best = el; }
  }
  if (!best) return;
  best.focus({ preventScroll: true });
  best.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function tvBack() {
  if ($("#tvAsk")) { tvBlocked = null; $("#tvAsk").remove(); return tvReport(true); }
  if (byId("queueMenu")) return byId("queueMenu").close(true);
  if (!byId("queuePanel").hidden) return queueShow(false);
  if (history.length > 1 && location.hash && location.hash !== "#/") history.back();
  else go("#/");
}

const TV_COLOURS = { ColorF0Red: () => tvDo({ cmd: "key", key: "home" }), ColorF1Green: () => go("#/tvsearch"), ColorF2Yellow: () => go("#/jf"), ColorF3Blue: () => tvDo({ cmd: "toggle" }) };
if (TV_MODE) {
  document.addEventListener("keydown", (e) => {
    const typing = e.target.matches?.("input:not([type=range]):not([type=checkbox]), textarea"), dir = e.key.startsWith("Arrow") && e.key.slice(5).toLowerCase();
    const mine = () => { e.preventDefault(); e.stopPropagation(); }; // a page's own shortcuts (a player's arrows) stay out of it: one path
    if (dir && !(typing && (dir === "left" || dir === "right")) && !(e.target.type === "range" && (dir === "left" || dir === "right"))) {
      mine();
      tvDo({ cmd: "key", key: dir });
    } else if (["Escape", "BrowserBack", "GoBack"].includes(e.key) || (e.key === "Backspace" && !typing)) {
      mine();
      tvDo({ cmd: "key", key: "back" });
    } else if (["MediaPlayPause", "MediaPlay", "MediaPause"].includes(e.key)) {
      mine();
      tvDo({ cmd: e.key === "MediaPlay" ? "play" : e.key === "MediaPause" ? "pause" : "toggle" });
    } else if (e.key === "Enter" && (e.target.tagName === "SELECT" || e.target.matches?.(".player") || $("#tvIdle"))) { // OK on a film, or on a list of choices
      mine();
      tvDo({ cmd: "key", key: "enter" });
    } else if (TV_COLOURS[e.key]) { // the remote's four colour keys: Home, Search, Library, play or pause
      mine();
      TV_COLOURS[e.key]();
    } else if ($("#tvIdle")) { mine(); tvAwake(); } // any other key wakes a sleeping screen
  }, true);
  addEventListener("hashchange", () => setTimeout(() => { if (!document.activeElement || document.activeElement === document.body) tvMove("down"); }, 300)); // a new page: the focus lands on it
}

/* ---------- pairing: the TV shows a code, a phone signed in enters it ---------- */

async function showTvPair(error = "") {
  ME = null;
  let got;
  try { got = await post("/api/tv/code"); } catch (err) { error = err.message; }
  const code = got?.code || "";
  $("#gate").innerHTML = `<div class="gate tvpair">${aurora}<div class="gate-card">
      <div class="brand"><img src="logo.svg" alt=""><span>Parlor</span></div>
      <h1>Pair this TV</h1>
      <p>On your phone or computer, open Parlor, go to <b>You → TVs</b>, enter this under <b>Add a TV</b> and press <b>Pair</b>:</p>
      <div class="tvcode" id="tvCode" role="img" aria-label="Pairing code ${esc(code.split("").join(" "))}">${esc(code.slice(0, 4))}<i></i>${esc(code.slice(4))}</div>
      ${got?.qr ? `<div class="tvqr" role="img" aria-label="The same code as a square to scan">${got.qr}</div><p class="dim">Or point your phone's camera at the square: it opens Parlor with the code filled in.</p>` : ""}
      <p class="dim" id="tvCodeNote">${error ? esc(error) : "The code works once, for five minutes. A new one shows up by itself."}</p>
      <button class="ghost" id="tvSignIn">Sign in on the TV instead</button></div></div>`;
  $("#tvSignIn").addEventListener("click", () => { clearTimeout(tvPairTimer); showGate("login", true); });
  $("#tvSignIn").focus();
  clearTimeout(tvPairTimer);
  if (!got) { tvPairTimer = setTimeout(() => showTvPair(), 10000); return; }
  const until = Date.now() + got.expires * 1000;
  const check = async () => {
    if (!$("#tvCode")) return; // left the pairing screen
    if (Date.now() > until) return showTvPair();
    try {
      const r = await post("/api/tv/claim", { claim: got.claim });
      if (r.paired) { $("#gate").innerHTML = ""; return boot(); }
    } catch (err) { if (err.status === 404) return showTvPair(); } // run out: a fresh code; anything else, ask again
    tvPairTimer = setTimeout(check, 2000);
  };
  tvPairTimer = setTimeout(check, 2000);
}
let tvPairTimer = 0;

/* ---------- the TV: hearing casts and presses, saying what it plays ---------- */

/** after sign-in: a TV's session listens; anyone else learns their TVs, for Play on TV (remote.js) */
async function tvBoot() {
  tvMe = (await api("/api/tv/me").catch(() => ({}))).tv || null;
  if (!tvMe && TV_MODE && NATIVE && (tvMe = (await post("/api/tv/adopt", { name: "TV" }).catch(() => ({}))).tv || null)) ME = await api("/api/me"); // now a TV's, so never an admin's // the Android app on a TV, signed in with a password: a TV all the same, so the account's phones can work it (never a browser at /tv: an admin who looks at it keeps their own session)
  if (!tvMe) return remoteBoot();
  if (!TV_MODE) { document.body.classList.add("tv"); tvFit(); route(); tvAwake(); } // a TV's session opened at another address is still a TV
  tvListen();
  tvPeople = await api(`/api/tv/${tvMe.id}/people`).catch(() => null); // more than one person in the household: ask who is watching, once each time the TV starts
  if ((tvPeople?.people.length || 0) < 2) return;
  if (!$("#side [data-g=who]")) $("#side")?.insertAdjacentHTML("beforeend", tvWhoLink()); // the rail's link to it (later pages draw it themselves)
  let asked = true;
  try { asked = sessionStorage.getItem("parlor:who"); sessionStorage.setItem("parlor:who", "1"); } catch { /* no storage: it never asks by itself */ }
  if (!asked) go("#/who");
}

let tvPeople = null; // {people, viewer}: who this TV can act as (tv.py)
/** someone was added to this TV or taken off: Who's watching and the rail's picture follow, and Add a remote draws again */
async function tvNewPeople() {
  tvPeople = await api(`/api/tv/${tvMe.id}/people`).catch(() => tvPeople);
  if ((tvPeople?.people.length || 0) > 1 && !$("#side [data-g=who]")) $("#side")?.insertAdjacentHTML("beforeend", tvWhoLink());
  if (location.hash.startsWith("#/tvremote")) showTvRemote.people();
  else if (location.hash.startsWith("#/who")) route();
}
/** start again at Home: the TV acts as someone else now */
function tvRestart() {
  history.replaceState(null, "", `${location.pathname}#/`);
  location.reload();
}

let tvStream = null;
function tvListen() {
  tvStream?.close();
  const s = (tvStream = new EventSource("/api/tv/events")); // reconnects by itself when it drops
  s.onerror = () => { // refused, it stops for good. Unpaired or signed out: pair again. Anything else (Parlor restarting, a proxy's 502 in its place): try again
    if (s.readyState !== EventSource.CLOSED || tvStream !== s) return;
    api("/api/tv/me").then((r) => (r.tv ? setTimeout(tvListen, 5000) : showGate()), (err) => (err.offline || err.status) && setTimeout(tvListen, 5000)); // an answer with a status is an error page; signed out or locked has its own screen (core.js)
  };
  tvStream.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "do") tvDo(m);
    else if (m.type === "viewer" && m.tv === tvMe?.id) tvRestart(); // someone else is watching now: their rows, their place, their limits
    else if (m.type === "people" && m.tv === tvMe?.id) tvNewPeople(); // a phone added itself, or someone was taken off
  };
  tvStream.onopen = () => { tvReport(true); tvFresh(); }; // it opens again after Parlor restarts: an update, perhaps
}

/** a TV is left on for weeks and nobody presses Reload on it: when a newer Parlor answers, it reloads itself as soon
    as nothing is playing */
let tvVersion = null, tvStale = false;
async function tvFresh() {
  const v = (await api("/api/tv/me").catch(() => ({}))).version;
  if (v && tvVersion && v !== tvVersion) tvStale = true;
  tvVersion ??= v || null;
  if (tvStale && !tvBusy()) location.reload();
}

/** what's playing on this TV, if anything: a video on the page, else the dock */
function tvPlayer() {
  const v = [...document.querySelectorAll("#view video")].find(tvShown); // a film, a video, a saved or private one
  return v || (dock.queue.length ? dock.audio : null);
}
/** not to be reloaded now: a song playing, or a film or video open, paused or not (it keeps its place until it is left) */
const tvBusy = (p = tvPlayer()) => !!p && (p.tagName === "VIDEO" || !p.paused);

/** OK: press what the focus is on. A list of choices (sound, subtitles, quality) moves to its next one: its own
    menu wants a mouse, and a phone's press can't open it */
function tvOk(el) {
  if (el?.tagName !== "SELECT") return el?.click();
  el.selectedIndex = (el.selectedIndex + 1) % el.options.length;
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

/** the one way anything changes this TV: a key (its own remote's, or a phone's), play and pause, seeking, volume,
    typing, a cast */
function tvDo(m) {
  const p = tvPlayer(), t0 = performance.now();
  switch (m.cmd) {
    case "key":
      if (m.key !== "home" && tvScreenKey(m.key)) break; // a screen with its own keys: Home's rows, the player (tvhome.js)
      if (m.key === "back") tvBack();
      else if (m.key === "home") go("#/");
      else if (m.key === "enter") tvOk(document.activeElement);
      else tvMove(m.key);
      break;
    case "toggle": if (p) p.paused ? tvPlay(p) : p.pause(); break;
    case "play": if (p) tvPlay(p); break;
    case "pause": p?.pause(); break;
    case "seek": if (p) p.currentTime = Math.max(0, m.to ?? p.currentTime + (m.by || 0)); break;
    case "skip": (m.by || 1) > 0 ? dockSkip() : dockBack(); break;
    case "volume": {
      const v = Math.min(1, Math.max(0, m.to ?? (p?.volume ?? dock.audio.volume) + (m.by || 0)));
      for (const el of new Set([p, dock.audio, dock.spare].filter(Boolean))) { el.volume = v; if (v > 0 && (m.by || 0) >= 0) el.muted = false; } // the dock's two players keep the same level; turning it up takes a mute off
      break;
    }
    case "mute": { const on = !(p || dock.audio).muted; for (const el of new Set([p, dock.audio, dock.spare].filter(Boolean))) el.muted = on; break; }
    case "set": { // one of the player's lists, by the number tvReport gave it
      const el = tvLists()[m.opt];
      if (el && [...el.options].some((o) => o.value === m.value)) { el.value = m.value; el.dispatchEvent(new Event("change", { bubbles: true })); }
      break;
    }
    case "sleep": tvSleepEnd = m.by < 0; tvSleep = m.by > 0 ? Date.now() + m.by * 60e3 : 0; if (tvSleepEnd) tvDoze(tvPlayer()); break; // in so many minutes; under 0: when what plays ends
    case "type": tvAwake(); tvType(m.text || "", m.key === "enter"); break;
    case "cast": tvCast(m.cast || {}); break;
    case "stop": p?.pause(); dockStop(); break;
  }
  tvActed(m, t0);
}

/** after the frame that shows what a command changed, say so: with the command's number (the server times the way
    there and back), how long acting on it took, and how long until that frame and the one after it */
let tvAck = null;
function tvActed({ n, cmd }, t0) {
  const took = performance.now() - t0;
  let said = false;
  if (!n) return tvReport(); // the TV's own remote: no phone waits for an answer (what the focus is on now goes out at the usual pace)
  const say = (t1, t2) => {
    if (said) return;
    said = true;
    tvAck = { ack: n, took: Math.round(took), ...(t2 ? { paint: Math.round(t2 - t0), frame: Math.round(t2 - t1) } : {}) };
    tvReport(true);
  };
  requestAnimationFrame((t1) => requestAnimationFrame((t2) => say(t1, t2)));
  setTimeout(say, 1000); // a page nobody sees draws no frames
}

/** play, or on a TV that blocks playing without a press, ask for one OK */
function tvPlay(p) {
  p.play().catch(() => tvRefused(p));
}

/** The browser won't start sound that nobody pressed a key for. A TV's own remote can press OK; a press that comes
    from a phone doesn't count as one, so the remotes are told too (a kiosk's browser needs
    --autoplay-policy=no-user-gesture-required). Anything on this page that tries to play comes through here. */
let tvBlocked = null; // the player that was refused
function tvRefused(p) {
  if (!p.paused) return;
  tvBlocked = p;
  tvReport(true);
  if ($("#tvAsk")) return;
  document.body.insertAdjacentHTML("beforeend", `<div class="tvask" id="tvAsk"><button class="primary" id="tvAskGo">${ICON.play}Press OK to play</button></div>`);
  $("#tvAskGo").addEventListener("click", () => { $("#tvAsk").remove(); p.play().catch(() => {}); });
  $("#tvAskGo").focus();
}
{
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () { // every player on the page: a film, a video, the music dock
    const r = play.call(this);
    r?.catch((err) => { if (err.name === "NotAllowedError" && tvMe) tvRefused(this); }); // only on a TV (at /tv, or its session at any address)
    return r;
  };
  document.addEventListener("playing", (e) => { if (e.target === tvBlocked) { tvBlocked = null; $("#tvAsk")?.remove(); } }, true);
}

const tvHex = (s) => /^[0-9a-f]{32}$/.test(s || "");
/** only what a dock item needs, from addresses Parlor serves */
const tvItem = (x) => (x && (tvHex(x.jf) || /^\/api\/jf\/music\//.test(x.src || "")) ? {
  ...(tvHex(x.jf) ? { jf: x.jf } : { src: x.src }), title: String(x.title || "").slice(0, 200), by: String(x.by || "").slice(0, 200),
  album: String(x.album || "").slice(0, 200), runtime: +x.runtime || 0, image: /^\/(?!\/)/.test(x.image || "") ? x.image : null,
  keep: !!x.keep, href: /^#\//.test(x.href || "") ? x.href : "#/" } : null);

function tvCast(c) {
  const at = Math.floor(+c.at || 0);
  if (c.kind === "jf" && tvHex(c.id)) return go(`#/jf/play/${c.id}${at ? `/${at}` : ""}`);
  if (c.kind === "yt" && /^[\w-]{11}$/.test(c.id || "")) return go(`#/watch/${c.id}${at ? `/${at}` : ""}`);
  if (c.kind === "item" && tvHex(c.id)) return go(`#/jf/item/${c.id}`); // a show's page: the episode is picked there
  if (c.kind === "queue") {
    const items = (c.items || []).map(tvItem).filter(Boolean);
    if (items.length) dockPlay(items, Math.max(0, Math.min(+c.id || 0, items.length - 1)), false);
  }
}

/** the lists a player offers (sound, subtitles, quality), in the order a remote numbers them */
const tvLists = () => [...document.querySelectorAll("#view .ptools select")].filter((el) => !el.hidden && !el.disabled && el.options.length > 1);
const tvCut = (s, n = 60) => String(s || "").trim().replace(/\s+/g, " ").slice(0, n);
/** what the focus is on, for a phone that mirrors this screen: Home's tile with its picture, else the name of the button or link */
function tvFocused() {
  const x = $("#tvhStack") && TVH.rows[TVH.r]?.items[TVH.at[TVH.key]], el = document.activeElement;
  if (x && el?.closest("#tvhStack")) return { title: tvCut(x.title), image: x.img || "", kind: x.kind };
  const title = el && el !== document.body ? tvCut(el.getAttribute("aria-label") || el.title || el.textContent) : "";
  return title ? { title } : null;
}
let tvSleep = 0, tvSleepVolume = null, tvSleepEnd = false; // when this TV pauses and goes dark by itself (a remote's sleep timer); the volume it had before the fade; whether that moment is the end of what plays

/** what this TV is doing, for its remotes: at most twice a second, and on every change */
let tvSaid = 0, tvSayTimer = 0;
function tvReport(now = false) {
  if (!tvMe) return;
  clearTimeout(tvSayTimer);
  if (!now && Date.now() - tvSaid < 500) { tvSayTimer = setTimeout(() => tvReport(true), 500); return; }
  tvSaid = Date.now();
  if (tvBlocked && tvBlocked.paused === false) { tvBlocked = null; $("#tvAsk")?.remove(); } // the music dock's player isn't on the page: its "playing" isn't heard there
  const p = tvPlayer(), x = p === dock.audio ? dock.queue[dock.at] : null;
  const state = {
    page: location.hash || "#/", title: x?.title || (p ? $(".vinfo h1, .jplay h1, .offline-video h1, .hero h1")?.textContent || "" : ""),
    by: x?.by || "", kind: p ? (p === dock.audio ? "audio" : "video") : null, playing: !!p && !p.paused, at: p ? Math.floor(p.currentTime || 0) : 0,
    length: p && isFinite(p.duration) ? Math.floor(p.duration) : 0, volume: p ? Math.round(p.volume * 100) / 100 : 1,
    ...(p?.muted ? { muted: true } : {}), ...(tvBlocked ? { blocked: true } : {}), ...tvAck,
    image: x?.image || (p && p !== dock.audio ? p.getAttribute("poster") : "") || "", // its picture, and below: is it still loading, does the screen want text, when it sleeps
    ...(p && !p.paused && p.readyState < 3 ? { waiting: true } : {}), ...(document.activeElement?.matches?.("input:not([type=range]):not([type=checkbox]), textarea") || $("#tvSearch") ? { input: true } : {}),
    ...(tvSleep ? { sleep: Math.round(tvSleep / 1000) } : {}), ...(tvSleepEnd ? { sleepEnd: true } : {}),
  };
  const focus = p && !p.paused ? null : tvFocused(); // while something plays, that is what the screen shows
  if (focus) state.focus = focus;
  const lists = p && p !== dock.audio ? tvLists().map((el) => ({ name: tvCut(el.getAttribute("aria-label"), 30), value: el.value, all: [...el.options].slice(0, 30).map((o) => [o.value, tvCut(o.textContent, 40)]) })) : [];
  if (lists.length && JSON.stringify(lists).length < 2600) state.lists = lists; // the server takes 4000 characters in all
  tvAck = null;
  post("/api/tv/state", { state }).catch(() => {});
}
for (const k of ["play", "pause", "seeked", "volumechange", "loadedmetadata", "waiting", "playing"]) document.addEventListener(k, () => tvReport(), true);
addEventListener("hashchange", () => tvReport());
setInterval(() => { // idle, every 10 s: still on
  if (!tvMe) return;
  const playing = tvPlayer() && !tvPlayer().paused;
  if (tvStale && !tvBusy()) return location.reload();
  if (tvSleep || tvSleepEnd) tvDoze(tvPlayer());
  if (playing || Date.now() - tvSaid > 10000) tvReport();
}, 2000);

/** a remote's sleep timer: the sound fades over the last half minute, then whatever plays pauses (its place is kept)
    and the screen goes dark */
function tvDoze(p) {
  if (tvSleepEnd) { // the end of what plays: that moment moves with every pause and jump
    if (!p || !isFinite(p.duration)) { tvSleepEnd = false; tvSleep = 0; return tvReport(); } // nothing with an end is playing: there is nothing to wait for
    tvSleep = Date.now() + Math.max(0, (p.duration - p.currentTime) / (p.playbackRate || 1) - 2.5) * 1000; // a look early (they come every 2 s): before the next episode starts itself
    if (p.paused) return;
  }
  const left = tvSleep - Date.now();
  if (left > 30e3) return;
  if (left > 0) { if (p) { tvSleepVolume ??= p.volume; p.volume = tvSleepVolume * (left / 30e3); } return; }
  tvSleep = 0;
  tvSleepEnd = false;
  if (p) { p.pause(); if (tvSleepVolume != null) p.volume = tvSleepVolume; }
  tvSleepVolume = null;
  tvIdle();
  tvReport(true);
}
