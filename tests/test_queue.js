// node tests/test_queue.js: the dock's queue, run from the app's own player.js against a stand-in page (no browser):
// play next and add to queue, reorder, remove, clear, shuffle and back, repeat, the next song loaded in the spare
// player and swapped in when this one ends, saved copies played from the device, the queue kept across reloads, and no position ever saved against a song
// that's still loading.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vm = require("vm");

const SRC = fs.readFileSync(path.join(__dirname, "..", "app", "static", "js", "player.js"), "utf8");

function page(storage = new Map(), me = { id: 7 }) {
  const events = []; // media events arrive later, as in a browser
  class El {
    constructor(id) { Object.assign(this, { id, attrs: {}, style: {}, listeners: {}, innerHTML: "", textContent: "", hidden: true, value: 0, disabled: false }); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k] ?? null; }
    hasAttribute(k) { return k in this.attrs; }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(t, f, o) { const w = o?.once ? (e) => { this.listeners[t] = this.listeners[t].filter((g) => g !== w); f(e); } : f; (this.listeners[t] ||= []).push(w); }
    removeEventListener() {}
    emit(type) { for (const f of [...(this.listeners[type] || [])]) f({ target: this }); }
    focus() {}
    scrollIntoView() {}
  }
  class Audio extends El {
    constructor() { super(); Object.assign(this, { paused: true, currentTime: 0, duration: NaN, playbackRate: 1, plays: 0 }); }
    get src() { return this.attrs.src || ""; }
    set src(v) { this.attrs.src = v; this.currentTime = 0; this.duration = NaN; }
    play() { this.plays++; if (this.paused) { this.paused = false; events.push([this, "play"]); } return Promise.resolve(); }
    pause() { if (!this.paused) { this.paused = true; events.push([this, "pause"]); } }
    load() {}
  }
  const els = {}, body = new Set(), posts = [], toasts = [];
  const ctx = {
    Audio, ME: me, URL, console,
    ICON: new Proxy({}, { get: (_, k) => `<i data-icon="${String(k)}"></i>` }),
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
    clock: (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`,
    plural: (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`,
    $: () => null, toast: (m) => toasts.push(m), addEventListener() {}, innerWidth: 1000, innerHeight: 800,
    // what's saved on this device (offline.js): key -> blob: address
    saved: {}, offWorks: true, offSave() {},
    offFind: (x) => (ctx.saved[x.jf ? `jf:${x.jf}` : x.src] ? { key: x.jf ? `jf:${x.jf}` : x.src } : null),
    offBlob: async (e) => ctx.saved[e.key],
    navigator: {}, location: { href: "http://parlor.test/" }, NATIVE: false, SERVER: "",
    localStorage: { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)), removeItem: (k) => storage.delete(k) },
    // Jellyfin's playback answer by default; a test can hold one back
    playback: (id, body) => Promise.resolve({ url: `/api/jf/stream/${id}`, position: body.start ?? 0, runtime: 200 }),
    post(p, b) { posts.push([p, b]); const m = p.match(/^\/api\/jf\/playback\/(.+)$/); return m ? ctx.playback(m[1], b) : Promise.resolve({}); },
    document: {
      body: { insertAdjacentHTML() {}, classList: { add: (...c) => c.forEach((x) => body.add(x)), remove: (...c) => c.forEach((x) => body.delete(x)), toggle: (c, on) => (on ? body.add(c) : body.delete(c)) }, append() {} },
      getElementById: (id) => (els[id] ||= new El(id)), querySelectorAll: () => [], addEventListener() {}, createElement: () => new El(),
    },
  };
  const api = vm.runInNewContext(`${SRC}\n;({ dock, dockPlay, dockGo, dockAdd, dockMove, dockRemove, dockClear, dockShuffle, dockRepeat, dockAfter, dockKeep, dockRestore, dockToggle, dockSkip, dockStop })`, ctx);
  // let queued media events and promise callbacks run
  api.settle = async () => { for (let n = 0; n < 5; n++) { await new Promise((r) => setImmediate(r)); for (const [el, t] of events.splice(0)) el.emit(t); } };
  return Object.assign(api, { ctx, els, body, posts, toasts, storage });
}
const songs = (n, from = 0) => Array.from({ length: n }, (_, i) => ({ src: `/api/jf/music/s${from + i}.mp3`, title: `S${from + i}`, runtime: 100 }));
const plain = (v) => JSON.parse(JSON.stringify(v)); // objects from the page's own world compare as plain ones
const titles = (q) => Array.from(q, (x) => x.title);
const progress = (p) => plain(p.posts.filter(([u]) => u === "/api/jf/progress").map(([, b]) => b));

(async () => {
  // play next goes right after the song playing, add to queue at the end; nothing playing: they start
  let p = page();
  p.dockAdd(songs(1));
  assert.strictEqual(p.dock.audio.src, "/api/jf/music/s0.mp3", "adding to an empty dock starts it");
  p.dockPlay(songs(4), 1);
  assert.strictEqual(p.dock.audio.src, "/api/jf/music/s1.mp3");
  const extra = songs(1, 50);
  p.dockAdd(extra, true);
  p.dockAdd(songs(2, 60));
  assert.deepStrictEqual(titles(p.dock.queue), ["S0", "S1", "S50", "S2", "S3", "S60", "S61"]);
  extra[0].title = "changed";
  assert.strictEqual(p.dock.queue[2].title, "S50", "the queue keeps its own copies");
  assert.ok(p.toasts.includes("“S50” plays next") && p.toasts.includes("Added 2 songs to the queue"), p.toasts);

  // reorder: the song playing carries on wherever it moves; the others shift round it
  p.dockMove(1, 4);
  assert.deepStrictEqual(titles(p.dock.queue), ["S0", "S50", "S2", "S3", "S1", "S60", "S61"]);
  assert.strictEqual(p.dock.at, 4);
  assert.strictEqual(p.dock.audio.src, "/api/jf/music/s1.mp3", "moving the song playing doesn't restart it");
  p.dockMove(6, 0);
  assert.strictEqual(p.dock.at, 5);
  assert.strictEqual(p.dock.queue[p.dock.at].title, "S1");
  p.dockMove(0, 99);
  assert.strictEqual(p.dock.queue[0].title, "S61", "a move off the end does nothing");

  // remove: before it shifts the place; the one playing moves on to the next
  p.dockRemove(0);
  assert.strictEqual(p.dock.queue[p.dock.at].title, "S1");
  assert.ok(!p.dock.audio.paused);
  p.dockRemove(p.dock.at);
  assert.strictEqual(p.dock.queue[p.dock.at].title, "S60");
  assert.strictEqual(p.dock.audio.src, "/api/jf/music/s60.mp3", "removing the song playing plays the next");
  p.dockPlay(songs(3), 1);
  p.dock.audio.pause();
  p.dockRemove(1);
  assert.strictEqual(p.dock.queue[p.dock.at].title, "S2");
  assert.strictEqual(p.dock.audio.src, "", "paused: the next one waits for the play button");
  p.dockToggle();
  assert.strictEqual(p.dock.audio.src, "/api/jf/music/s2.mp3");
  p.dockRemove(p.dock.at);
  assert.strictEqual(p.dock.queue[p.dock.at].title, "S0", "the last one taken out: the dock rests on what's left");
  assert.strictEqual(p.dock.audio.src, "", "and doesn't play a song already heard");
  p.dockAdd(songs(2, 70));
  p.dockClear();
  assert.deepStrictEqual(titles(p.dock.queue), ["S0"]);
  p.dockRemove(0);
  assert.strictEqual(p.dock.queue.length, 0, "the last one out closes the dock");
  assert.ok(p.els.dock.hidden);

  // shuffle: the song playing comes first, the rest in a new order; songs added meanwhile have a place in both orders; off puts it back
  p = page();
  p.dockPlay(songs(20), 5);
  p.dockShuffle(true);
  assert.strictEqual(p.dock.queue[0].title, "S5");
  assert.strictEqual(p.dock.at, 0);
  assert.strictEqual(p.dock.audio.src, "/api/jf/music/s5.mp3", "shuffling doesn't restart the song");
  assert.deepStrictEqual(titles(p.dock.queue).sort(), titles(songs(20)).sort());
  assert.notDeepStrictEqual(titles(p.dock.queue), ["S5", ...titles(songs(20)).filter((t) => t !== "S5")]);
  p.dockAdd(songs(1, 90), true);
  assert.strictEqual(p.dock.queue[1].title, "S90");
  const gone = p.dock.queue[5].title;
  p.dockRemove(5);
  p.dockShuffle(false);
  const want = titles(songs(20)).filter((t) => t !== gone);
  want.splice(want.indexOf("S5") + 1, 0, "S90");
  assert.deepStrictEqual(titles(p.dock.queue), want, "off: the order as it was, less the one removed, with play next after its song");
  assert.strictEqual(p.dock.queue[p.dock.at].title, "S5");
  p.dockPlay(songs(10), 3, true);
  assert.strictEqual(p.dock.queue[0].title, "S3", "Shuffle on an album starts from the song asked for");
  assert.ok(p.dock.order, "and leaves shuffle on");

  // repeat: off stops at the end, all goes round, one plays it again
  p = page();
  p.dockPlay(songs(2), 1);
  await p.settle();
  p.dock.audio.emit("ended");
  assert.strictEqual(p.dock.at, 1);
  assert.strictEqual(p.dockAfter(), -1);
  p.dockRepeat();
  assert.strictEqual(p.dock.repeat, "all");
  assert.strictEqual(p.els.queueRepeat.attrs["aria-pressed"], "true");
  p.dock.audio.emit("ended");
  assert.strictEqual(p.dock.at, 0, "repeat all goes back to the first");
  p.dockRepeat();
  assert.strictEqual(p.dock.repeat, "one");
  const plays = p.dock.audio.plays;
  p.dock.audio.currentTime = 99;
  p.dock.audio.emit("ended");
  assert.strictEqual(p.dock.at, 0);
  assert.strictEqual(p.dock.audio.currentTime, 0);
  assert.strictEqual(p.dock.audio.plays, plays + 1, "repeat one plays it again");
  p.dockRepeat();
  assert.strictEqual(p.dock.repeat, "off");

  // a song that won't play is skipped, forward only: at the end, or with repeat all and nothing playable, it stops
  p = page();
  p.dockPlay(songs(3), 0);
  await p.settle();
  p.dock.audio.emit("error");
  assert.strictEqual(p.dock.at, 1, "skips to the next");
  p.dockGo(2);
  await p.settle();
  p.dockRepeat("all");
  p.dock.audio.emit("error");
  assert.strictEqual(p.dock.at, 2, "the last one failing doesn't go round again, even on repeat all");
  p = page();
  p.ctx.playback = () => Promise.reject(new Error("Jellyfin is down"));
  p.dockRepeat("all");
  p.dockPlay([{ jf: "a", title: "A" }, { jf: "b", title: "B" }], 0);
  await p.settle();
  assert.strictEqual(p.dock.at, 1);
  assert.strictEqual(p.posts.filter(([u]) => u.startsWith("/api/jf/playback/")).length, 2, "each asked for once: no loop");

  // the next song loads in the spare player in this one's last 30 s, and is swapped in when it ends
  p = page();
  p.dockPlay(songs(3), 0);
  await p.settle();
  const first = p.dock.audio, spare = p.dock.spare;
  first.duration = 100;
  first.currentTime = 50;
  first.emit("timeupdate");
  assert.strictEqual(spare.src, "", "not yet");
  first.currentTime = 75;
  first.emit("timeupdate");
  await p.settle();
  assert.strictEqual(spare.src, "/api/jf/music/s1.mp3", "loaded ahead");
  first.emit("ended");
  assert.strictEqual(p.dock.audio, spare, "the spare plays");
  assert.ok(!spare.paused);
  assert.strictEqual(p.dock.spare, first);
  assert.strictEqual(first.src, "", "the old one lets go of its stream");
  assert.strictEqual(p.dock.at, 1);
  first.emit("ended");
  assert.strictEqual(p.dock.at, 1, "the spare's own events don't move the dock");
  // the queue changes after a song loaded: the spare is dropped, and the right one loads instead
  spare.duration = 100;
  spare.currentTime = 80;
  spare.emit("timeupdate");
  await p.settle();
  assert.strictEqual(p.dock.spare.src, "/api/jf/music/s2.mp3");
  p.dockAdd(songs(1, 40), true);
  assert.strictEqual(p.dock.spare.src, "", "a song that's no longer next isn't kept loaded");
  spare.emit("timeupdate");
  await p.settle();
  assert.strictEqual(p.dock.spare.src, "/api/jf/music/s40.mp3");
  // Jellyfin songs are asked for at the top; audiobooks aren't loaded ahead (they resume where they were)
  p = page();
  p.dockPlay([{ jf: "a1", title: "A" }, { jf: "a2", title: "B" }, { jf: "a3", title: "Book", keep: true }], 0);
  await p.settle();
  p.dock.audio.duration = 200;
  p.dock.audio.currentTime = 190;
  p.dock.audio.emit("timeupdate");
  await p.settle();
  assert.deepStrictEqual(plain(p.posts.filter(([u]) => u.startsWith("/api/jf/playback/"))), [["/api/jf/playback/a1", { start: 0 }], ["/api/jf/playback/a2", { start: 0 }]]);
  p.dock.audio.emit("ended");
  await p.settle();
  p.dock.audio.duration = 200;
  p.dock.audio.currentTime = 190;
  p.dock.audio.emit("timeupdate");
  await p.settle();
  assert.strictEqual(p.posts.filter(([u]) => u === "/api/jf/playback/a3").length, 0, "an audiobook isn't loaded ahead");

  // the loading race: while the next song's address is on its way, the last one's time is never saved against it
  p = page();
  let release;
  p.dockPlay([{ jf: "book", title: "Book", keep: true, runtime: 3600 }, { jf: "song", title: "Song", runtime: 200 }], 0);
  await p.settle();
  p.dock.audio.duration = 3600;
  p.dock.audio.currentTime = 1000;
  p.ctx.playback = () => new Promise((r) => { release = r; });
  p.dockGo(1);
  await p.settle(); // the old song's pause arrives while Song loads
  assert.deepStrictEqual(progress(p), [{ id: "book", position: 1000 }], "the book's place is saved, nothing for Song");
  release({ url: "/api/jf/stream/song", position: 0, runtime: 200 });
  await p.settle();
  assert.strictEqual(p.dock.audio.src, "/api/jf/stream/song");
  p.dock.audio.pause();
  await p.settle();
  assert.deepStrictEqual(progress(p), [{ id: "book", position: 1000 }]);
  // and playing a new album over a long book doesn't mark its first song played
  p = page();
  p.dockPlay([{ jf: "book", title: "Book", keep: true, runtime: 3600 }], 0);
  await p.settle();
  p.dock.audio.duration = 3600;
  p.dock.audio.currentTime = 3500;
  p.dockPlay([{ jf: "t1", title: "T1", runtime: 180 }, { jf: "t2", title: "T2", runtime: 180 }], 0);
  await p.settle();
  assert.ok(!progress(p).some((b) => b.id === "t1"), progress(p));

  // a song saved on this device plays from there: Jellyfin isn't asked, here or when it's loaded ahead
  p = page();
  p.ctx.saved = { "jf:a1": "blob:parlor.test/one", "jf:a2": "blob:parlor.test/two" };
  p.dockPlay([{ jf: "a1", title: "A" }, { jf: "a2", title: "B" }], 0);
  await p.settle();
  assert.strictEqual(p.dock.audio.src, "blob:parlor.test/one");
  p.dock.audio.duration = 100;
  p.dock.audio.currentTime = 90;
  p.dock.audio.emit("timeupdate");
  await p.settle();
  assert.strictEqual(p.dock.spare.src, "blob:parlor.test/two");
  assert.strictEqual(p.posts.filter(([u]) => u.startsWith("/api/jf/playback/")).length, 0, "no network needed");

  // kept across a reload, per person: the queue, where you were, shuffle's order and repeat; it waits, paused
  const store = new Map();
  p = page(store);
  p.dockPlay(songs(6), 2);
  await p.settle();
  p.dockShuffle(true);
  p.dockRepeat();
  p.dock.audio.currentTime = 42;
  p.dockKeep();
  const before = titles(p.dock.queue);
  assert.ok(store.has("parlor:queue:7"));
  const q = page(store);
  q.dockRestore();
  assert.deepStrictEqual(titles(q.dock.queue), before);
  assert.strictEqual(q.dock.at, 0);
  assert.strictEqual(q.dock.repeat, "all");
  assert.strictEqual(q.dock.resume, 42);
  assert.strictEqual(q.dock.audio.src, "", "it waits for the play button");
  assert.strictEqual(q.els.dockTitle.textContent, "S2");
  q.dockToggle();
  assert.strictEqual(q.dock.audio.src, "/api/jf/music/s2.mp3");
  q.dock.audio.emit("loadedmetadata");
  assert.strictEqual(q.dock.audio.currentTime, 42, "and starts where it was");
  q.dockShuffle(false);
  assert.deepStrictEqual(titles(q.dock.queue), titles(songs(6)), "shuffle's original order came back too");
  const other = page(store, { id: 8 });
  other.dockRestore();
  assert.strictEqual(other.dock.queue.length, 0, "someone else on this device doesn't get it");
  // a tampered or broken copy is ignored, not half-used
  for (const bad of ["{", JSON.stringify({ queue: [{ title: "x" }], at: 0 }), JSON.stringify({ queue: songs(2), at: 5 }), JSON.stringify({ queue: "x", at: 0 })]) {
    const s = new Map([["parlor:queue:7", bad]]), r = page(s);
    r.dockRestore();
    assert.strictEqual(r.dock.queue.length, 0, bad);
  }
  const s2 = new Map([["parlor:queue:7", JSON.stringify({ queue: songs(3), at: 1, order: [0, 0, 1], repeat: "sideways" })]]), r2 = page(s2);
  r2.dockRestore();
  assert.strictEqual(r2.dock.order, null, "an order that isn't the same songs is dropped");
  assert.strictEqual(r2.dock.repeat, "off");
  // closing the dock forgets it
  q.dockStop();
  assert.ok(!store.has("parlor:queue:7"));
  console.log("queue ok");
})().catch((e) => { console.error(e); process.exit(1); });
