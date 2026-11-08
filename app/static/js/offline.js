/* Saved for offline: songs, albums, downloaded videos and Jellyfin films and episodes kept on this device, in the browser's private storage
   for this site (OPFS), so they play with no network, from the Offline page and in the dock (which plays a saved
   copy even online). offline-worker.js does the saving, in pieces it can carry on from; the list of what's saved
   is in this device's localStorage, per person. Playing needs no server and no sign-in token: a saved file plays
   from a blob: address. */

const offWorks = SECURE && !!navigator.storage?.getDirectory && typeof Worker === "function";
const offBlobs = new Map(), offSent = new Set();
let offWorker = null, offChunk = 8 << 20; // the piece size: the browser test makes it small, to break a download part way

/** everything saved on this device: { file: entry }; an entry is { file, who (server and person), key, kind: "song" | "video" | "image" | "sub",
    title, by, album, image (another entry's file), runtime, url, have, size, type, done, error, added }; a Jellyfin film or episode
    also has jf, film (what the player needs to draw its page with no server) and subs ([{ file (a "sub" entry), title, language, default }]) */
function offList() { try { return JSON.parse(localStorage.getItem("parlor:offline")) || {}; } catch { return {}; } }
function offKeep(all) { try { localStorage.setItem("parlor:offline", JSON.stringify(all)); } catch { /* storage full */ } }
const offWho = () => `${SERVER || location.origin} ${ME?.id}`; // whose: the same id on another Parlor is someone else
const offSmall = (e) => e.kind === "image" || e.kind === "sub"; // covers and subtitle files: kept beside what they belong to, never listed
const offMine = () => Object.values(offList()).filter((e) => e.who === offWho() && !offSmall(e));
const offKey = (x) => (x.jf ? `jf:${x.jf}` : x.yt ? `yt:${x.yt}` : x.src);
const offDir = async () => (await navigator.storage.getDirectory()).getDirectoryHandle("offline", { create: true });

/** a file's name: a hash of whose it is and what it is */
async function offName(key) {
  const d = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${offWho()}:${key}`)));
  return [...d.slice(0, 12)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** save songs (dock items: Jellyfin's, or the music folder's) or videos ({ yt, src, ... }) on this device */
async function offSave(items, kind = "song") {
  if (!offWorks) return toast(SECURE ? "This browser can't save for offline" : "Saving for offline needs Parlor's https address: see Settings → This device");
  navigator.storage.persist?.().catch(() => {}); // ask the browser not to clear it when space runs low
  const all = offList(), fresh = [], notes = [];
  for (const x of items) {
    const file = await offName(offKey(x));
    if (all[file] && !all[file].error) continue; // saved, or on its way
    let url = x.src;
    const subs = []; // a film's text subtitles Jellyfin serves as files are saved beside it
    if (x.jf) {
      try {
        const p = await post(`/api/jf/playback/${x.jf}`, { start: 0 });
        if (p.method !== "direct") throw new Error("Jellyfin would have to convert it, so it can't be saved as it is");
        url = p.url;
        x.runtime ||= p.runtime;
        if (kind === "video") {
          for (const s of p.subtitles.filter((t) => t.url)) {
            const f = await offName(`sub:${x.jf}:${s.index}`);
            all[f] ||= { file: f, who: offWho(), kind: "sub", url: s.url, have: 0, size: 0, done: false };
            subs.push({ file: f, title: s.title, language: s.language, default: s.default });
          }
          const lost = p.subtitles.filter((t) => !t.url).map((t) => t.title || t.language).filter(Boolean);
          if (lost.length || p.subtitles.length > subs.length) notes.push(`Subtitles that are picture-based or inside the file aren't saved${lost.length ? ` (${lost.join(", ")})` : ""}`);
        }
      } catch (err) { notes.push(`“${x.title}”: ${err.message}`); continue; }
    }
    let image = null;
    if (x.image && !x.image.startsWith("blob:")) {
      image = await offName(`img:${x.image}`);
      all[image] ||= { file: image, who: offWho(), kind: "image", url: x.image, have: 0, size: 0, done: false };
    }
    all[file] = { file, who: offWho(), key: offKey(x), kind, title: x.title || "", by: x.by || "", album: x.album || "", image, runtime: x.runtime || 0,
      url, href: x.href || "", have: 0, size: 0, done: false, added: Date.now(), ...(x.jf ? { jf: x.jf } : {}), ...(x.film ? { film: x.film, subs } : {}) };
    fresh.push(all[file]);
  }
  offKeep(all);
  offRun();
  const said = fresh.length ? (fresh.length === 1 ? `Saving “${fresh[0].title}” on this device` : `Saving ${plural(fresh.length, kind)} on this device`) : notes.length ? "" : "Already saved on this device";
  toast([said, ...notes].filter(Boolean).join(". "), false);
  if (location.hash.startsWith("#/offline")) route();
}

/** start (or carry on with) whatever isn't saved yet: covers first, they're small */
function offRun() {
  if (!offWorks || !ME) return;
  const todo = Object.values(offList()).filter((e) => e.who === offWho() && !e.done && (!e.error || offSmall(e)) && !offSent.has(e.file))
    .sort((a, b) => offSmall(b) - offSmall(a));
  if (!todo.length) return;
  offWorker ||= Object.assign(new Worker("js/offline-worker.js"), { onmessage: offHeard });
  for (const e of todo) {
    offSent.add(e.file);
    offWorker.postMessage({ file: e.file, url: new URL(e.url, location.href).href, chunk: offChunk, size: e.size || 0 });
  }
}

function offHeard({ data: m }) {
  const all = offList(), e = all[m.file];
  offSent.delete(m.done || m.error || m.dropped ? m.file : null);
  if (!e || m.dropped || m.busy) return; // busy: another window is saving it, and keeps the list up to date
  if (m.error) e.error = m.error;
  else {
    e.have = m.have ?? m.size ?? e.have;
    e.size = m.size || e.size;
    e.type = m.type || e.type;
    if (m.done) Object.assign(e, { done: true, have: m.size, size: m.size, error: null });
  }
  offKeep(all);
  const row = document.querySelector(`[data-off="${e.file}"] .offstate`);
  if (row) row.outerHTML = offState(e);
  if (m.done && !offSmall(e) && !Object.values(all).some((x) => x.who === offWho() && !x.done && !x.error)) toast("Saved on this device");
}

/** this person's saved copy of a dock item, if it's all here: the dock plays it instead of asking the server */
function offFind(x) {
  if (!offWorks || !ME) return null;
  const key = offKey(x);
  return Object.values(offList()).find((e) => e.who === offWho() && e.key === key && e.done) || null;
}
async function offBlob(e) {
  if (offBlobs.has(e.file)) return offBlobs.get(e.file);
  try {
    const f = await (await (await offDir()).getFileHandle(e.file)).getFile();
    const url = URL.createObjectURL(new Blob([f], { type: e.type || "" }));
    offBlobs.set(e.file, url);
    return url;
  } catch { return null; } // the browser cleared it: it plays from the server instead
}

async function offRemove(files) {
  const all = offList(), dir = await offDir();
  for (const f of files) {
    offWorker?.postMessage({ drop: f }); // still saving: the worker stops and deletes it
    offSent.delete(f);
    delete all[f];
    URL.revokeObjectURL(offBlobs.get(f));
    offBlobs.delete(f);
    await dir.removeEntry(f).catch(() => {});
  }
  const used = new Set(Object.values(all).flatMap((e) => [e.image, ...(e.subs || []).map((s) => s.file)]));
  for (const e of Object.values(all)) {
    if (offSmall(e) && !used.has(e.file)) {
      delete all[e.file];
      URL.revokeObjectURL(offBlobs.get(e.file));
      offBlobs.delete(e.file);
      await dir.removeEntry(e.file).catch(() => {});
    }
  }
  offKeep(all);
}

/* ---------- the Offline page ---------- */

page("offline", showOffline, "settings");

const offState = (e) => `<span class="offstate${e.error ? " bad" : ""}">${e.error ? `${esc(e.error)} <button class="linkish" data-off-retry="${esc(e.file)}">Try again</button>`
  : e.done ? esc(bytes(e.size)) : `<span class="offbar-mini"><i style="width:${e.size ? Math.round((100 * e.have) / e.size) : 3}%"></i></span>Saving${e.size ? ` ${Math.round((100 * e.have) / e.size)}%` : "…"}`}</span>`;

/** where a saved video plays: a Jellyfin film or episode in the Library's player, any other in a plain one */
const offWatch = (e) => (e.film ? `#/jf/play/${esc(e.jf)}` : `#/offline/v/${esc(e.file)}`);

async function showOffline(t, [sub, file] = []) {
  if (sub === "v" && file) return showOfflineVideo(t, file);
  const gone = document.body.classList.contains("offline");
  const head = (note) => `<section class="home">${aurora}<h1>${gone ? "You're offline" : "Saved for offline"}</h1><p>${note}</p></section>`;
  if (!offWorks) {
    return paint(t, head(SECURE ? "This browser can't keep music and videos for offline." : "Saving music and videos on this device needs Parlor's https address.")
      + `<div class="wrap"><div class="empty-state"><strong>Nothing can be saved here</strong>${SECURE ? "Try Chrome, Edge, Firefox or Safari 16 and later." : `See <a href="#/settings">Settings → This device</a>.`}</div></div>`);
  }
  const [est, kept] = await Promise.all([navigator.storage.estimate?.().catch(() => null), navigator.storage.persisted?.().catch(() => false)]);
  if (t !== ticket) return;
  const mine = offMine(), all = offList(), covers = {};
  for (const e of mine) if (e.image && all[e.image]?.done) covers[e.image] ||= await offBlob(all[e.image]);
  if (t !== ticket) return;
  const used = mine.reduce((n, e) => n + (e.have || 0), 0);
  const note = `${gone ? "Parlor can't reach its server right now; what's saved here still plays. " : ""}${bytes(used)} on this device${est?.quota ? `, of the ${bytes(est.quota)} this browser allows` : ""}.
    ${kept ? "Kept until you remove it." : "The browser may clear it if the device runs low on space."}`;
  if (!mine.length) {
    return paint(t, head(note) + `<div class="wrap"><div class="empty-state"><strong>Nothing saved on this device yet</strong>
      Use <b>⋯ → Save on this device</b> on an album or a song, the save button on a downloaded video, or <b>Save on this device</b> on a film or an episode, while online.</div></div>`);
  }
  const art = (e, cls = "") => (covers[e.image] ? `<img class="${cls}" src="${esc(covers[e.image])}" alt="">` : `<span class="blank ${cls}"></span>`);
  const albums = new Map();
  for (const e of mine.filter((x) => x.kind === "song")) albums.set(`${e.album}\n${e.by}`, [...(albums.get(`${e.album}\n${e.by}`) || []), e]);
  const videos = mine.filter((e) => e.kind === "video");
  const songRow = (e) => `<li class="row" data-off="${esc(e.file)}"><span class="num">${ICON.note}</span>
    <span class="what"><button class="title linkish" data-off-play="${esc(e.file)}">${esc(e.title)}</button><span class="by">${esc(e.by)}</span></span>
    <span class="dur">${e.runtime ? clock(e.runtime) : ""}</span>${offState(e)}
    <button class="icon-btn" data-off-drop="${esc(e.file)}" aria-label="Remove ${esc(e.title)} from this device">${ICON.trash}</button></li>`;
  paint(t, head(note) + `<div class="wrap offline-page">
    ${albums.size ? `<h2>Music</h2>${[...albums.values()].map((list) => `<section class="offalbum">
      <div class="offhead">${art(list[0], "cover")}<div><strong>${esc(list[0].album || "Songs")}</strong><span class="dim">${esc(list[0].by)} · ${plural(list.length, "song")} · ${bytes(list.reduce((n, e) => n + (e.have || 0), 0))}</span>
        <div class="actions"><button class="primary small" data-off-album="${esc(list[0].file)}">${ICON.play}Play</button>
        <button class="ghost" data-off-drop="${esc(list.map((e) => e.file).join(","))}">${ICON.trash}Remove</button></div></div></div>
      <ol class="rows offsongs">${list.map(songRow).join("")}</ol></section>`).join("")}` : ""}
    ${videos.length ? `<h2>Videos</h2><div class="vgrid">${videos.map((e) => `<article class="vcard" data-off="${esc(e.file)}">
      <a class="vthumb" href="${offWatch(e)}" tabindex="-1" aria-hidden="true">${art(e)}${e.runtime ? `<span class="len">${clock(e.runtime)}</span>` : ""}</a>
      <div class="vact"><button data-off-drop="${esc(e.file)}" aria-label="Remove ${esc(e.title)} from this device">${ICON.trash}</button></div>
      <div class="vmeta"><a class="t" href="${offWatch(e)}">${esc(e.title)}</a><span class="c">${esc(e.by)}</span>${offState(e)}</div></article>`).join("")}</div>` : ""}
    <div class="actions"><button class="ghost danger" data-off-drop="${esc(mine.map((e) => e.file).join(","))}" data-off-all>${ICON.trash}Remove everything from this device</button></div>
  </div>`);
}

async function showOfflineVideo(t, file) {
  const e = offList()[file];
  const src = e?.done && e.who === offWho() ? await offBlob(e) : null;
  if (t !== ticket) return;
  if (!src) return paint(t, `<div class="note"><strong>Not on this device</strong><a href="#/offline">Saved for offline</a></div>`);
  paint(t, `<div class="wrap offline-video"><video id="offVideo" controls autoplay playsinline src="${esc(src)}"></video>
    <h1>${esc(e.title)}</h1><p class="dim">${esc(e.by)} · ${bytes(e.size)} on this device</p><a class="ghost" href="#/offline">${ICON.prev}Saved for offline</a></div>`);
}

/* songs from the Offline page play in the dock, the album's saved songs in order */
const offItem = (e) => ({ ...(e.jf ? { jf: e.jf } : { src: e.key }), title: e.title, by: e.by, album: e.album, runtime: e.runtime, href: "#/offline",
  image: offBlobs.get(e.image) || null });
document.addEventListener("click", async (ev) => {
  const play = ev.target.closest("[data-off-play]"), album = ev.target.closest("[data-off-album]"), drop = ev.target.closest("[data-off-drop]"), retry = ev.target.closest("[data-off-retry]");
  if (play || album) {
    const one = offList()[(play || album).dataset.offPlay || album.dataset.offAlbum];
    const list = offMine().filter((e) => e.kind === "song" && e.done && e.album === one.album && e.by === one.by);
    if (play && !one.done) return toast("Still saving: it plays once it's all here");
    if (!list.length) return toast("Nothing here is saved yet");
    dockPlay(list.map(offItem), play ? list.findIndex((e) => e.file === one.file) : 0, false);
  } else if (drop) {
    const files = drop.dataset.offDrop.split(",").filter(Boolean);
    if (drop.hasAttribute("data-off-all") ? !confirm("Remove everything saved on this device?") : files.length > 1 && !confirm(`Remove these ${files.length} songs from this device?`)) return;
    await offRemove(files);
    toast(files.length === 1 ? "Removed from this device" : `Removed ${plural(files.length, "item")} from this device`);
    route();
  } else if (retry) {
    const all = offList(), e = all[retry.dataset.offRetry];
    if (e) { e.error = null; offKeep(all); offRun(); route(); }
  }
});

/* a downloaded video's save button (Watch → Downloaded) */
document.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-voffline]"), card = b?.closest("[data-video]");
  if (!card) return;
  ev.preventDefault();
  const v = JSON.parse(card.dataset.video);
  offSave([{ yt: v.id, src: `/api/yt/file/${v.id}`, title: v.title, by: v.channel, image: `/api/yt/file/${v.id}/thumb`, runtime: v.duration }], "video");
});
