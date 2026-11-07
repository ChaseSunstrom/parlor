/* Torrents (admins): qBittorrent's list kept current with its own incremental sync, the everyday
   actions, speed limits and adding a magnet; qBittorrent's full page is one click away at /qbit/. */

const T_STATE = { downloading: "Downloading", forcedDL: "Downloading (forced)", metaDL: "Getting details", stalledDL: "Waiting for peers",
  queuedDL: "Queued", checkingDL: "Checking", checkingUP: "Checking", checkingResumeData: "Checking", pausedDL: "Paused", stoppedDL: "Paused",
  uploading: "Seeding", forcedUP: "Seeding (forced)", stalledUP: "Seeding", queuedUP: "Queued to seed", pausedUP: "Finished", stoppedUP: "Finished",
  error: "Error", missingFiles: "Files missing", moving: "Moving" };
const T_FILTERS = [["all", "All"], ["active", "Downloading"], ["seeding", "Seeding"], ["paused", "Paused"], ["errors", "Problems"]];
const paused = (s) => /^(paused|stopped)/.test(s);
const T_MATCH = { all: () => true, active: (t) => /DL$|^downloading$/.test(t.state) && !paused(t.state),
  seeding: (t) => /UP$|^uploading$/.test(t.state) && !paused(t.state), paused: (t) => paused(t.state), errors: (t) => ["error", "missingFiles"].includes(t.state) };
const tor = { rid: 0, list: {}, server: {}, cats: [], filter: "all", find: "" };
const rate = (n) => (n ? `${bytes(n)}/s` : "idle");

function torrentRow([hash, t]) {
  const pct = Math.round((t.progress || 0) * 1000) / 10;
  const bad = ["error", "missingFiles"].includes(t.state);
  const live = [t.dlspeed && `↓ ${bytes(t.dlspeed)}/s`, t.upspeed && `↑ ${bytes(t.upspeed)}/s`, t.eta > 0 && t.eta < 8640000 && t.progress < 1 && `${clock(t.eta)} left`]
    .filter(Boolean).join(" · ");
  return `<li class="job torrent" data-hash="${esc(hash)}"><div>
      <strong title="${esc(t.name)}">${esc(t.name)}</strong>
      <span class="sub">${esc([t.category || "No category", bytes(t.size || 0), t.ratio != null && `ratio ${(+t.ratio).toFixed(2)}`].filter(Boolean).join(" · "))}</span>
      ${t.progress < 1 ? `<div class="meter"><i style="width:${pct}%"></i></div>` : ""}
      <span class="state ${bad ? "failed" : t.progress >= 1 ? "done" : ""}">${esc(T_STATE[t.state] || t.state)}${t.progress < 1 ? ` · ${pct}%` : ""}${live ? ` · ${esc(live)}` : ""}</span>
    </div>
    <span class="job-actions">
      <button class="stop" data-t="${paused(t.state) ? "resume" : "pause"}">${paused(t.state) ? "Resume" : "Pause"}</button>
      <details class="tmenu"><summary class="icon-btn" aria-label="More for ${esc(t.name)}">⋯</summary><div class="tmenu-list">
        <button data-t="recheck">Check the files again</button>
        <button data-t="reannounce">Ask the trackers for more peers</button>
        <label>Category <select data-tcat>${["", ...new Set([...tor.cats, t.category || ""])].map((c) => `<option value="${esc(c)}"${c === (t.category || "") ? " selected" : ""}>${esc(c || "none")}</option>`).join("")}</select></label>
        <button data-t="remove">Remove, keep the files</button>
        <button data-t="purge" class="danger">Remove and delete the files</button>
      </div></details>
    </span></li>`;
}

function drawTorrents(t) {
  const box = $("#torList");
  if (!box || t !== ticket) return;
  const s = tor.server, words = tor.find.toLowerCase();
  const rows = Object.entries(tor.list).filter(([, x]) => T_MATCH[tor.filter](x) && (!words || x.name.toLowerCase().includes(words)))
    .sort(([, a], [, b]) => (b.added_on || 0) - (a.added_on || 0));
  $("#torSpeeds").textContent = [`↓ ${rate(s.dl_info_speed)}`, `↑ ${rate(s.up_info_speed)}`,
    s.free_space_on_disk != null && `${bytes(s.free_space_on_disk)} free`, s.connection_status && s.connection_status !== "connected" && `connection: ${s.connection_status}`]
    .filter(Boolean).join(" · ");
  $("#torAlt").checked = !!s.use_alt_speed_limits;
  const mbs = (n) => (n ? String(Math.round((n / 1048576) * 10) / 10) : "");
  if (!tor.limitsShown && s.dl_rate_limit != null) {  // once: what qBittorrent has now, so setting one keeps the other
    $("#torDown").value = mbs(s.dl_rate_limit);
    $("#torUp").value = mbs(s.up_rate_limit);
    tor.limitsShown = true;
  }
  if (box.contains(document.activeElement) && document.activeElement.matches("select")) return;  // mid-choice in a menu
  box.innerHTML = rows.length ? rows.map(torrentRow).join("")
    : `<li class="empty">${Object.keys(tor.list).length ? "None here." : "No torrents. Requests from Seerr and music requests show up here while they download."}</li>`;
  if (tor.menu) box.querySelector(`[data-hash="${CSS.escape(tor.menu)}"] details`)?.setAttribute("open", "");  // a menu stays open across updates
}

async function pollTorrents(t) {
  if (t !== ticket || !$("#torList")) return;
  try {
    const d = await api(`/api/qbit/torrents?rid=${tor.rid}`);
    if (d.full_update) tor.list = {};
    for (const [h, x] of Object.entries(d.torrents)) tor.list[h] = { ...tor.list[h], ...x };
    d.torrents_removed.forEach((h) => delete tor.list[h]);
    Object.assign(tor.server, d.server_state);
    if (d.full_update) tor.cats = d.categories || [];
    else if (d.categories) tor.cats = [...new Set([...tor.cats, ...d.categories])];
    tor.cats = tor.cats.filter((c) => !d.categories_removed.includes(c));
    tor.rid = d.rid;
    $("#torError").hidden = true;
    drawTorrents(t);
  } catch (err) {
    $("#torError").hidden = false;
    $("#torError").textContent = err.message;
  }
  setTimeout(() => pollTorrents(t), document.hidden ? 10000 : 2000);
}

async function showTorrents(t) {
  if (!ME?.admin) return paint(t, `<div class="wrap"><div class="empty-state">Only admins manage torrents.</div></div>`);
  Object.assign(tor, { rid: 0, list: {}, menu: null, limitsShown: false });
  paint(t, `<section class="home">${aurora}<h1>Torrents</h1><p id="torSpeeds" class="dim">${SPIN}</p></section>
  <div class="wrap">
    <p class="notice" id="torError" hidden></p>
    <div class="chips page-actions">${T_FILTERS.map(([k, l]) => `<button data-tfilter="${k}"${k === tor.filter ? ' aria-pressed="true" class="on"' : ""}>${l}</button>`).join("")}
      <a class="ghost" href="/qbit/" target="_blank" rel="noopener">Open qBittorrent</a></div>
    <div class="actions"><label class="field grow"><span class="vh">Find</span><input id="torFind" placeholder="Find a torrent" value="${esc(tor.find)}"></label>
      <button class="ghost" data-tall="pause">Pause all</button><button class="ghost" data-tall="resume">Resume all</button></div>
    <ul class="jobs" id="torList"><li class="empty">${SPIN}</li></ul>
    <div class="settings-grid">
      <section class="panel"><h3>Add a torrent</h3>
        <form id="torAdd" class="actions"><label class="field grow"><span class="vh">Magnet link</span><input id="torMagnet" placeholder="magnet:?xt=… or a link to a .torrent" required></label>
          <button class="primary small">Add</button></form>
        <small class="dim">Requests through Seerr and the music page are added by the apps themselves; this is for anything else.</small></section>
      <section class="panel"><h3>Speed</h3>
        <label class="check"><input type="checkbox" id="torAlt"><span>Slow mode<br><small class="dim">qBittorrent's alternative speed limits, for when the house needs the bandwidth.</small></span></label>
        <form id="torLimits" class="actions"><label class="field"><span>Download limit, MB/s</span><input id="torDown" type="number" min="0" step="0.5" placeholder="no limit"></label>
          <label class="field"><span>Upload limit, MB/s</span><input id="torUp" type="number" min="0" step="0.5" placeholder="no limit"></label>
          <button class="ghost">Set limits</button></form></section>
    </div>
  </div>`);
  const act = (action, hashes, extra = {}) => post(`/api/qbit/torrents/${action}`, { hashes, ...extra }).then(() => { tor.rid = 0; }, (err) => toast(err.message));
  main.querySelectorAll("[data-tfilter]").forEach((b) => b.addEventListener("click", () => {
    tor.filter = b.dataset.tfilter;
    main.querySelectorAll("[data-tfilter]").forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
    drawTorrents(t);
  }));
  $("#torFind").addEventListener("input", (e) => { tor.find = e.target.value; drawTorrents(t); });
  main.querySelectorAll("[data-tall]").forEach((b) => b.addEventListener("click", () => act(b.dataset.tall, "all")));
  $("#torList").addEventListener("click", (e) => {
    const b = e.target.closest("[data-t]"), hash = e.target.closest("[data-hash]")?.dataset.hash;
    if (!b || !hash) return;
    const x = tor.list[hash], what = b.dataset.t;
    b.closest("details")?.removeAttribute("open");
    if (what === "remove" && confirm(`Remove “${x.name}” from qBittorrent? Its files stay.`)) act("delete", [hash]);
    else if (what === "purge" && confirm(`Remove “${x.name}” and delete its files? If Sonarr, Radarr or Lidarr hasn't imported it yet, it's gone.`)) act("delete", [hash], { delete_files: true });
    else if (!["remove", "purge"].includes(what)) act(what, [hash]);
  });
  $("#torList").addEventListener("change", (e) => {
    const hash = e.target.closest("[data-hash]")?.dataset.hash;
    if (e.target.matches("[data-tcat]") && hash) act("category", [hash], { category: e.target.value }).then(() => toast("Category changed"));
  });
  $("#torList").addEventListener("toggle", (e) => {
    if (e.target.open) tor.menu = e.target.closest("[data-hash]")?.dataset.hash;
    else if (tor.menu === e.target.closest("[data-hash]")?.dataset.hash) tor.menu = null;
  }, true);
  const closeMenu = () => { tor.menu = null; $$("#torList details[open]").forEach((d) => d.removeAttribute("open")); };
  document.addEventListener("click", (e) => { if (tor.menu && !e.target.closest(".tmenu")) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && tor.menu) closeMenu(); });
  $("#torAdd").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/api/qbit/add", { magnet: $("#torMagnet").value }).then(() => { $("#torMagnet").value = ""; toast("Added"); tor.rid = 0; }, (err) => toast(err.message));
  });
  $("#torAlt").addEventListener("change", (e) => put("/api/qbit/limits", { alt: e.target.checked }).then(() => toast(e.target.checked ? "Slow mode on" : "Slow mode off"), (err) => toast(err.message)));
  $("#torLimits").addEventListener("submit", (e) => {
    e.preventDefault();
    const mb = (v) => (v === "" ? 0 : Math.round(+v * 1048576));
    put("/api/qbit/limits", { down: mb($("#torDown").value), up: mb($("#torUp").value) }).then(() => toast("Limits set"), (err) => toast(err.message));
  });
  pollTorrents(t);
}
page("torrents", showTorrents, "media");
