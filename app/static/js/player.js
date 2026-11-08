/* The dock: music and audiobooks keep playing while you move around Parlor. One <audio>, a queue and the
   system's media controls. Jellyfin items stream through /api/jf; an audiobook (and any track you stop part
   way) keeps its place in Jellyfin, so another device picks it up there. */

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
  <button class="icon-btn" id="dockClose" aria-label="Stop and close">${ICON.x}</button>
</div>`);

const dock = { queue: [], at: -1, audio: new Audio(), saved: 0, played: false, seeking: false, token: 0 };
dock.audio.preload = "auto";
const byId = (id) => document.getElementById(id);

/** play a list of items from `at`; an item is { title, by, image, href, runtime, src } or { jf: Jellyfin id, keep: true for audiobooks } */
function dockPlay(queue, at = 0) {
  dockSave();
  dock.queue = queue.filter(Boolean);
  dockGo(at);
}

async function dockGo(i) {
  if (i < 0 || i >= dock.queue.length) return;
  dockSave();
  const token = ++dock.token;
  dock.loading = true; // until the new song's address is in: the old song's time isn't the new one's
  dock.audio.pause();
  dock.at = i;
  dock.saved = 0;
  dock.played = false;
  const x = dock.queue[i];
  drawDock(x);
  document.querySelectorAll("video").forEach((v) => v.pause()); // one thing plays at a time
  let src = x.src, start = x.start || 0;
  if (!src && x.jf) {
    try {
      const p = await post(`/api/jf/playback/${x.jf}`, x.keep ? {} : { start: 0 }); // an audiobook resumes; a song starts at the top
      src = p.url;
      start = p.position || 0;
      x.runtime ||= p.runtime;
    } catch (err) {
      if (token === dock.token) { dock.loading = false; toast(err.message); if (i + 1 < dock.queue.length) dockGo(i + 1); }
      return;
    }
  }
  if (token !== dock.token) return;
  dock.loading = false;
  dock.audio.src = src;
  if (start) dock.audio.addEventListener("loadedmetadata", () => { dock.audio.currentTime = start; }, { once: true });
  dock.audio.play().catch(() => {}); // a browser that blocks autoplay leaves it paused; the button starts it
  dockKeys();
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: x.title || "", artist: x.by || "", album: x.album || "",
      artwork: x.image ? [{ src: new URL(x.image, location.href).href, sizes: "600x600" }] : [] });
  }
}

function drawDock(x) {
  byId("dock").hidden = false;
  document.body.classList.add("docked");
  byId("dockArt").innerHTML = x.image ? `<img src="${esc(x.image)}" alt="">` : `<span class="blank"></span>`;
  byId("dockArt").href = byId("dockTitle").href = x.href || "#/jf";
  byId("dockTitle").textContent = x.title || "";
  byId("dockBy").textContent = x.by || "";
  byId("dockNext").disabled = dock.at >= dock.queue.length - 1;
  byId("dockLen").textContent = clock(x.runtime || 0);
}

/** where you are, saved to Jellyfin: audiobooks always, songs once they're played through */
function dockSave(ended = false) {
  const x = dock.queue[dock.at], a = dock.audio;
  if (!x?.jf || !a.src || dock.loading) return;
  const at = a.currentTime || 0, len = a.duration || x.runtime || 0;
  if (!dock.played && (ended || (len && at / len > 0.95))) {
    dock.played = true;
    post("/api/jf/progress", { id: x.jf, played: true }).catch(() => {});
  } else if (x.keep && !dock.played && Math.abs(at - dock.saved) >= 5) {
    dock.saved = at;
    post("/api/jf/progress", { id: x.jf, position: Math.floor(at) }).catch(() => {});
  }
}

function dockStop() {
  dockSave();
  dock.token++;
  dock.loading = false;
  dock.audio.pause();
  dock.audio.removeAttribute("src");
  dock.audio.load();
  dock.queue = [];
  dock.at = -1;
  byId("dock").hidden = true;
  document.body.classList.remove("docked", "dock-playing");
  if ("mediaSession" in navigator) navigator.mediaSession.metadata = null;
}

const dockToggle = () => (dock.audio.paused ? dock.audio.play().catch((err) => toast(err.message)) : dock.audio.pause());
const dockBack = () => (dock.audio.currentTime > 4 || dock.at <= 0 ? (dock.audio.currentTime = 0) : dockGo(dock.at - 1));

const dockAudio = dock.audio;
dockAudio.addEventListener("play", () => { document.body.classList.add("dock-playing"); byId("dockPlay").innerHTML = ICON.pause; byId("dockPlay").setAttribute("aria-label", "Pause"); });
dockAudio.addEventListener("pause", () => { document.body.classList.remove("dock-playing"); byId("dockPlay").innerHTML = ICON.play; byId("dockPlay").setAttribute("aria-label", "Play"); dockSave(); });
dockAudio.addEventListener("ended", () => { dockSave(true); dock.at < dock.queue.length - 1 ? dockGo(dock.at + 1) : document.body.classList.remove("dock-playing"); });
dockAudio.addEventListener("error", () => { if (dockAudio.src && dock.at >= 0) toast("This one won't play here; skipping"); if (dockAudio.src && dock.at < dock.queue.length - 1) dockGo(dock.at + 1); });
dockAudio.addEventListener("timeupdate", () => {
  const len = dockAudio.duration && isFinite(dockAudio.duration) ? dockAudio.duration : dock.queue[dock.at]?.runtime || 0;
  if (!dock.seeking) byId("dockSeek").value = len ? Math.round((1000 * dockAudio.currentTime) / len) : 0;
  byId("dockLine").style.width = len ? `${(100 * dockAudio.currentTime) / len}%` : "0";
  byId("dockAt").textContent = clock(dockAudio.currentTime);
  byId("dockLen").textContent = clock(len);
  if (Math.abs(dockAudio.currentTime - dock.saved) >= 15) dockSave();
});
byId("dockPlay").addEventListener("click", dockToggle);
byId("dockNext").addEventListener("click", () => dockGo(dock.at + 1));
byId("dockPrev").addEventListener("click", dockBack);
byId("dockClose").addEventListener("click", dockStop);
byId("dockSeek").addEventListener("input", () => { dock.seeking = true; });
byId("dockSeek").addEventListener("change", (e) => {
  dock.seeking = false;
  const len = dockAudio.duration && isFinite(dockAudio.duration) ? dockAudio.duration : dock.queue[dock.at]?.runtime || 0;
  if (len) dockAudio.currentTime = (len * e.target.value) / 1000;
});
addEventListener("pagehide", () => dockSave());
document.addEventListener("play", (e) => { if (e.target.tagName === "VIDEO") dockAudio.pause(); }, true); // a video starting pauses the dock

/** the lock screen's and keyboard's media keys drive the dock (a video page takes them while it plays) */
function dockKeys() {
  if (!("mediaSession" in navigator)) return;
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", () => dockAudio.play());
  ms.setActionHandler("pause", () => dockAudio.pause());
  ms.setActionHandler("nexttrack", () => dockGo(dock.at + 1));
  ms.setActionHandler("previoustrack", dockBack);
  for (const k of ["seekbackward", "seekforward"]) ms.setActionHandler(k, null);
  ms.setActionHandler("seekto", (d) => { dockAudio.currentTime = d.seekTime; });
}
