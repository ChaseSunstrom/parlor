/* The downloads drawer: video downloads (yeerr) and music downloads (music-fndr). */

function setDrawer(open) {
  drawer.classList.toggle("open", open);
  drawer.inert = !open;
  $("#openQueue").setAttribute("aria-expanded", open);
  if (open) { $("#closeQueue").focus(); poll(); }
}
$("#openQueue").addEventListener("click", () => setDrawer(!drawer.classList.contains("open")));
$("#closeQueue").addEventListener("click", () => { setDrawer(false); $("#openQueue").focus(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && drawer.classList.contains("open")) { setDrawer(false); $("#openQueue").focus(); } });

/* ---------- music (music-fndr's own drawer code) ---------- */

const RUNNING = ["queued", "waiting", "resolving", "downloading"];
const SONG_LABEL = { queued: "Waiting", done: "Saved", skipped: "Already there", duplicate: "Duplicate, skipped", failed: "Couldn't download" };
const FINISHED = ["done", "skipped", "duplicate", "failed"];
let jobs = [], lastJobs = "";
const expanded = new Set();
function jobState(j) {
  const where = j.steps > 1 ? `release ${j.step} of ${j.steps}` : "";
  const failed = j.failed ? `, ${j.failed} couldn't download` : "";
  if (j.status === "queued") return "Waiting to start";
  if (j.status === "waiting") return j.error;
  if (j.stop && RUNNING.includes(j.status)) return "Stopping after the songs in progress…";
  if (j.status === "resolving" && !j.steps) return j.kind === "artist" ? "Listing releases…" : "Finding songs…";
  if (j.status === "resolving") return where ? `Finding songs in ${where}` + (j.finished ? `, ${plural(j.finished, "song")} done` : "") : "Finding songs…";
  if (j.status === "downloading") return `${j.finished} of ${plural(j.total, "song")}` + (where ? `, ${where}` : "") + failed;
  if (j.status === "failed") return `Failed: ${j.error}`;
  const count = (s) => j.songs.filter((x) => x.status === s).length;
  return [
    j.status === "stopped" && "Stopped",
    count("done") && `${count("done")} saved`,
    count("skipped") && `${count("skipped")} already in your library`,
    count("duplicate") && `${plural(count("duplicate"), "duplicate")} skipped`,
    j.failed && `${j.failed} couldn't download`,
  ].filter(Boolean).join(", ") || "Done";
}
function jobItem(j) {
  // overall = finished releases + how far the current release's songs have got
  const current = j.status === "downloading" ? j.songs.slice(j.songs.length - j.current) : [];
  const inner = current.length ? current.reduce((n, s) => n + (FINISHED.includes(s.status) ? 100 : s.progress), 0) / current.length / 100 : 0;
  const pct = j.steps ? (100 * (j.step - 1 + inner)) / j.steps : 0;
  const kind = { track: "Song", album: "Album", playlist: "Playlist", artist: "Discography" }[j.kind];
  const sub = [kind, j.kind === "artist" ? "" : j.subtitle].filter(Boolean).join(", ");
  const songs = j.songs.length > 1 ? `<details data-job="${esc(j.job)}" ${expanded.has(j.job) ? "open" : ""}>
      <summary>${plural(j.songs.length, "song")}</summary>
      <ol class="songs">${j.songs.map((s) => `<li class="${esc(s.status)}"><span>${esc(s.name)} <span class="sub">${esc(s.artists.join(", "))}</span></span><span class="st" title="${esc(s.error || "")}">${esc(s.status === "downloading" ? s.message || "Downloading" : (s.status === "failed" && s.error) || SONG_LABEL[s.status] || s.status)}</span></li>`).join("")}</ol>
    </details>` : "";
  const failed = j.status === "failed" || (["done", "stopped"].includes(j.status) && j.failed);
  return `<li class="job ${esc(j.kind)}">
    ${img(j.image)}
    <div>
      <strong>${esc(j.title || "Song")}</strong>
      <span class="sub">${esc(sub)}</span>
      ${["resolving", "downloading"].includes(j.status) ? `<div class="meter"><i style="width:${pct.toFixed(1)}%"></i></div>` : ""}
      <span class="state ${failed ? "failed" : j.status === "done" ? "done" : ""}">${esc(jobState(j))}</span>
    </div>
    ${["resolving", "downloading"].includes(j.status)
      ? (j.stop ? "<span></span>" : `<button class="stop" data-remove="${esc(j.job)}" aria-label="Stop ${esc(j.title || "download")}">Stop</button>`)
      : `<span class="job-actions">${failed || j.status === "stopped"
          ? `<button class="stop retry" data-retry="${esc(j.job)}" aria-label="Retry ${esc(j.title || "download")}">Retry</button>` : ""}
          <button class="icon-btn" data-remove="${esc(j.job)}" aria-label="Remove ${esc(j.title || "download")}">${ICON.x}</button></span>`}
    ${songs}
  </li>`;
}

/* ---------- videos ---------- */

const V_RUNNING = ["queued", "waiting", "downloading", "processing"];
const STEP = { Merger: "Joining video and audio", FFmpegMerger: "Joining video and audio", EmbedThumbnail: "Adding the thumbnail",
  FFmpegMetadata: "Adding chapters and details", FFmpegEmbedSubtitle: "Adding subtitles", FFmpegExtractAudio: "Converting the audio",
  ModifyChapters: "Cutting out sponsors", SponsorBlock: "Finding sponsor segments", ThumbnailsConvertor: "Preparing the thumbnail",
  FFmpegThumbnailsConvertor: "Preparing the thumbnail", MoveFiles: "Saving", MetadataParser: "Tagging" };
let videoJobs = [];

function videoState(j) {
  if (j.status === "queued") return "Waiting to start";
  if (j.status === "waiting") return j.error || "Waiting";
  if (j.stop) return "Stopping…";
  if (j.status === "downloading") {
    const parts = [j.progress != null ? `${Math.floor(j.progress)}%` : "Starting", j.size && bytes(j.size), j.speed && `${bytes(j.speed)}/s`, j.eta != null && `${clock(j.eta)} left`];
    return parts.filter(Boolean).join(" · ");
  }
  if (j.status === "processing") return `${STEP[j.step] || "Finishing up"}…`;
  if (j.status === "failed") return `Failed: ${j.error}`;
  if (j.status === "stopped") return "Stopped";
  return j.kind === "music" ? "Saved to your music library" : `Saved${j.size ? `, ${bytes(j.size)}` : ""}`;
}

function videoItem(j) {
  const live = ["downloading", "processing"].includes(j.status);
  const unknown = j.status === "processing" || j.progress == null;
  const label = j.kind === "music" ? "Audio to your music library" : `Video, ${j.quality === "best" ? "best quality" : j.quality + "p"}`;
  const play = j.status === "done" && j.kind === "video" ? ` <a href="#/watch/${esc(j.id)}">Play</a>` : "";
  return `<li class="job video">
    <img src="https://i.ytimg.com/vi/${esc(j.id)}/mqdefault.jpg" alt="" loading="lazy">
    <div>
      <strong>${esc(j.title || "YouTube video")}</strong>
      <span class="sub">${esc([label, j.channel].filter(Boolean).join(" · "))}</span>
      ${live ? `<div class="meter${unknown ? " unknown" : ""}"><i style="width:${(j.progress || 0).toFixed(1)}%"></i></div>` : ""}
      <span class="state ${j.status === "failed" ? "failed" : j.status === "done" ? "done" : ""}">${esc(videoState(j))}${play}</span>
    </div>
    ${live
      ? (j.stop ? "<span></span>" : `<button class="stop" data-vremove="${esc(j.job)}">Stop</button>`)
      : `<span class="job-actions">${["failed", "stopped", "waiting"].includes(j.status) ? `<button class="stop retry" data-vretry="${esc(j.job)}">${j.status === "waiting" ? "Try now" : "Retry"}</button>` : ""}
          <button class="icon-btn" data-vremove="${esc(j.job)}" aria-label="Remove">${ICON.x}</button></span>`}
  </li>`;
}

/* ---------- both ---------- */

let wasActive = 0, lastVideos = "";
function renderJobs() {
  const musicActive = jobs.filter((j) => RUNNING.includes(j.status)).length;
  if (musicActive < wasActive) loadLibrary().catch(() => {});
  wasActive = musicActive;
  const videoActive = videoJobs.filter((j) => V_RUNNING.includes(j.status)).length;
  const active = musicActive + videoActive;
  const failedJobs = retryable().length + videoJobs.filter((j) => ["failed", "stopped"].includes(j.status)).length;
  $("#retryFailed").hidden = !failedJobs;
  $("#retryFailed").textContent = `Retry ${failedJobs} failed`;
  $("#torrentsLink").hidden = !ME?.admin;
  $("#count").hidden = !active;
  $("#count").textContent = active;
  $("#eq").hidden = !videoJobs.some((j) => j.status === "downloading") && !jobs.some((j) => j.status === "downloading");
  document.body.classList.toggle("busy", active > 0);
  $("#jobs").innerHTML = jobs.length
    ? jobs.map(jobItem).join("")
    : `<li class="empty">No music downloads. Use the download buttons on songs, albums, playlists and artists.</li>`;
  $("#videoJobs").innerHTML = videoJobs.length
    ? videoJobs.map(videoItem).join("")
    : `<li class="empty">No video downloads. Use the download button on any video.</li>`;
}
$("#jobs").addEventListener("toggle", (e) => {
  const id = e.target.dataset.job;
  if (id) e.target.open ? expanded.add(id) : expanded.delete(id);
}, true);

async function poll() {
  const [m, v] = await Promise.allSettled([api("/music/api/downloads"), api("/api/yt/downloads")]);
  let changed = false;
  if (m.status === "fulfilled") { const s = JSON.stringify(m.value); if (s !== lastJobs) { lastJobs = s; jobs = m.value; changed = true; } }
  if (v.status === "fulfilled") { const s = JSON.stringify(v.value); if (s !== lastVideos) { lastVideos = s; videoJobs = v.value; changed = true; } }
  if (changed) { renderJobs(); onDownloadsChanged(); }
}
let downloadsHooks = [];
function onDownloadsChanged() { downloadsHooks.forEach((fn) => fn()); }
const retryable = () => jobs.filter((j) => j.status === "failed" || j.status === "stopped");

$("#retryFailed").addEventListener("click", async () => {
  const list = retryable(), vids = videoJobs.filter((j) => ["failed", "stopped"].includes(j.status));
  await Promise.all([
    ...list.map((j) => post(`/music/api/downloads/${j.job}/retry`).catch(() => {})),
    ...vids.map((j) => post(`/api/yt/downloads/${j.job}/retry`).catch(() => {})),
  ]);
  toast(`Queued ${plural(list.length + vids.length, "download")} again. What's already saved is skipped.`);
  poll();
});
$("#clearDone").addEventListener("click", async () => {
  await Promise.all([
    ...jobs.filter((j) => !RUNNING.includes(j.status)).map((j) => del(`/music/api/downloads/${j.job}`).catch(() => {})),
    ...videoJobs.filter((j) => !V_RUNNING.includes(j.status)).map((j) => del(`/api/yt/downloads/${j.job}`).catch(() => {})),
  ]);
  poll();
});
document.addEventListener("click", (e) => {
  const rm = e.target.closest("[data-vremove]");
  if (rm) {
    rm.disabled = true;
    del(`/api/yt/downloads/${rm.dataset.vremove}`).then((r) => { if (r.stopping) toast("Stopping"); poll(); }, (err) => { rm.disabled = false; toast(err.message); });
  }
  const again = e.target.closest("[data-vretry]");
  if (again) {
    again.disabled = true;
    post(`/api/yt/downloads/${again.dataset.vretry}/retry`).then(() => poll(), (err) => { again.disabled = false; toast(err.message); });
  }
});

// quick while something runs or the drawer is open, slow otherwise
(async function loop() {
  if (ME) await poll().catch(() => {});
  const busy = document.body.classList.contains("busy") || drawer.classList.contains("open");
  setTimeout(loop, busy ? 1500 : 6000);
})();
