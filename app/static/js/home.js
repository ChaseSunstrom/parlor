/* Home, search across everything, settings, sign-in, and start-up. */

function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Up late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}
const slot = (id, title, body = skeletons(4)) => `<div id="${id}">${title ? `<div class="shelf-head"><h2>${esc(title)}</h2></div>` : ""}${body}</div>`;
const fill = (t, id, html) => { const el = $(`#${id}`); if (t === ticket && el) el.innerHTML = html; };
const tag = (color, label) => `<span class="section-tag" style="--c:${color}"><i></i>${esc(label)}</span>`;

async function showDashboard(t) {
  paint(t, `<section class="home">${aurora}
      <h1>${greeting()}, ${esc(ME?.name || "")}</h1>
      <p>Your videos, films, shows and music, in one place and without ads.</p>
      <div class="stats-row" id="stats"></div>
    </section>
    <div class="wrap">
      ${slot("homeContinue", "")}
      ${slot("homeSubs", "")}
      ${slot("homeMedia", "", skeletons(6, "p", "pstrip"))}
      ${slot("homeMusic", "", "")}
    </div>`);
  const stat = (href, color, icon, n, label) => `<a class="stat-card" href="${href}" style="--c:${color}"><span class="ic">${icon}</span><span><b>${n}</b><span>${esc(label)}</span></span></a>`;
  const stats = {};
  const drawStats = () => fill(t, "stats", [
    stats.subs != null && stat("#/yt/channels", "var(--coral)", ICON.tv, stats.subs, `${stats.subs === 1 ? "channel" : "channels"} you follow`),
    stats.videos != null && stat("#/yt/downloads", "var(--coral)", ICON.dl, stats.videos, `${stats.videos === 1 ? "video" : "videos"} saved${stats.used ? ` · ${bytes(stats.used)}` : ""}`),
    (stats.requests != null || stats.games > 0) && stat("#/requests", "var(--violet)", ICON.film, (stats.requests || 0) + (stats.games || 0),
      `${(stats.requests || 0) + (stats.games || 0) === 1 ? "request" : "requests"} waiting for approval`),  // films and shows in Seerr, games in Parlor
    stats.songs != null && stat("#/library", "var(--amber)", ICON.disc, compact.format(stats.songs), `${stats.songs === 1 ? "song" : "songs"} in your library`),
  ].filter(Boolean).join(""));

  api("/api/me/history?unfinished=true&limit=12").then((r) => fill(t, "homeContinue", r.items.length
    ? shelf("Continue watching", `<div class="vstrip">${r.items.map((v) => vcard(v)).join("")}</div>`, "#/yt/continue") : ""), () => fill(t, "homeContinue", ""));
  api("/api/me/subs").then((s) => { stats.subs = s.length; drawStats(); }).catch(() => {});
  if (!closedTo("games")) api("/api/games/requests").then((r) => { stats.games = r.items.filter((x) => x.state === "pending").length; drawStats(); }).catch(() => {});
  api("/api/yt/library").then((r) => { stats.videos = r.items.length; stats.used = r.used; drawStats(); }).catch(() => {});
  api("/api/yt/feed?limit=24").then((r) => fill(t, "homeSubs", r.channels
    ? shelf("New from your subscriptions", `<div class="vstrip">${hideShorts(r.items).slice(0, 16).map((v) => vcard(v)).join("")}</div>`, "#/yt/subs")
    : `<div class="shelf-head"><h2>${tag("var(--coral)", "Watch")}</h2></div><div class="empty-state" style="padding-top:0">Follow some YouTube channels and their new videos land here. <a href="#/yt/channels">Import your subscriptions</a>.</div>`),
  () => fill(t, "homeSubs", ""));
  api("/api/seerr/status").then(async (s) => {
    if (!s.connected) return fill(t, "homeMedia", ME?.admin ? `<div class="shelf-head"><h2>${tag("var(--violet)", "Films & shows")}</h2></div><div class="empty-state" style="padding-top:0">Connect Seerr in <a href="#/settings">Settings</a> to discover and request films and shows.</div>` : "");
    const [d, mine] = await Promise.all([api("/api/seerr/discover"), api("/api/seerr/requests?filter=pending").catch(() => null)]);
    if (mine?.linked) { stats.requests = mine.items.length; drawStats(); }
    fill(t, "homeMedia", shelf("Trending films and shows", `<div class="pstrip">${(d.trending || []).filter((x) => x.type !== "person").map(pcard).join("")}</div>`, "#/media"));
  }).catch(() => fill(t, "homeMedia", ""));
  api("/music/api/home").then((feed) => fill(t, "homeMusic",
    shelf("New music this week", grid(feed.new.map((a) => tile("album", a, creditText(a.artists))).join(""), "strip"), "#/music"))).catch(() => fill(t, "homeMusic", ""));
  (libraryOnce ||= loadLibrary().catch(() => { libraryOnce = null; })).then((songs) => { if (songs?.length) { stats.songs = songs.length; drawStats(); } });
}

/* Settings → Services: the apps Parlor's compose runs, and the setup that wires them together */
const SETUP_STEP = { ok: ["available", "Already set"], done: ["done", "Set up"], skipped: ["pending", "Waiting"], failed: ["failed", "Failed"] };
async function drawServices(t) {
  const box = $("#services");
  const s = await api("/api/stack").catch(() => null);
  if (!box || t !== ticket || !s?.on) return;
  const steps = (app) => s.run.results.filter((r) => r.app === app);
  // Checkrr: its last scan, its own page (the list of broken files), and whether it replaces what it finds
  const checkrr = (a) => {
    const page = /\/\/checkrr:8585$/.test(a.url) ? `http://${location.hostname}:8585/` : a.url;
    const scan = !a.up ? "" : a.scan ? ` · last scan ${since(Date.parse(a.scan.when) / 1000)}: ${plural(a.scan.checked, "file")}, ${a.scan.broken} broken, ${a.scan.replaced} replaced`
      : " · no scan yet (every night at 04:00)";
    return { note: `${scan} · <a href="${esc(page)}" target="_blank" rel="noopener noreferrer">broken files</a>${a.yours ? " · your own config" : ""}`,
             ask: a.yours ? "" : `<label class="check svc-ask"><input type="checkbox" id="checkrrReplace"${a.replaces ? " checked" : ""}><span>Replace broken files
               <br><small class="dim">On: a film, episode or song that can't be read is deleted by Sonarr, Radarr or Lidarr and searched for again. Off: it's only listed.</small></span></label>` };
  };
  // Questarr: its own page, and the login Parlor made for it (nobody typed one)
  const questarr = (a) => ` · <a href="${esc(/\/\/questarr:5000$/.test(a.url) ? `http://${location.hostname}:5000/` : a.url)}" target="_blank" rel="noopener noreferrer">its own page</a>${a.login ? `
    · <button type="button" class="linkish" id="questLogin">show its login</button>` : ""}`;
  const row = (a) => {
    const ck = a.app === "checkrr" ? checkrr(a) : null;
    const mine = steps(a.app), worst = ["failed", "skipped", "done", "ok"].find((st) => mine.some((r) => r.status === st));
    return `<li class="svc"><span class="state ${a.up ? "up" : "down"}" title="${a.up ? "Answering" : "Not answering"}"></span>
      <div><strong>${esc(a.label)}</strong><small class="dim">${esc(a.url)}${a.key === false ? " · no API key yet" : ""}${a.app === "qbittorrent" ? ` · <a href="#/torrents">manage torrents</a>` : ""}${ck ? ck.note : ""}${a.app === "questarr" ? questarr(a) : ""}</small>
        ${mine.length ? `<ul class="svc-steps">${mine.map((r) => `<li class="${r.status}"><b>${esc(r.step)}</b> ${esc(r.detail)}</li>`).join("")}</ul>` : ""}
        ${ck ? ck.ask : ""}</div>
      ${worst ? pill(...SETUP_STEP[worst]) : ""}</li>`;
  };
  const when = s.run.running ? "Setting up now…" : s.run.finished ? `Last set up ${since(s.run.finished)}.` : "Not set up yet.";
  box.innerHTML = `<section class="panel"><div class="head"><div><h3>Services</h3>
      <p>The apps that run beside Parlor. Setup reads their API keys, turns off logins on your home network, and connects
        qBittorrent, Prowlarr's indexers, Jellyfin, Seerr, Bazarr, Cleanuparr, Questarr and Checkrr. It only ever adds what's missing. ${when}</p></div>
      ${s.run.running ? `<span class="spin-sm" aria-label="Running"></span>` : ""}</div>
    <ul class="svc-list">${s.apps.map(row).join("")}</ul>
    ${s.indexers ? `<p class="dim">Indexers refreshed ${since(s.indexers.at)}${s.indexers.note ? `: ${esc(s.indexers.note)}` : ""}${[["added", s.indexers.added], ["switched off", s.indexers.off], ["back on", s.indexers.on]]
      .filter(([, l]) => l.length).map(([w, l]) => `; ${w} ${esc(l.join(", "))}`).join("")}. Twice a day, new public ones that work are added and dead ones rest.</p>` : ""}
    <label class="field"><span>Download quality</span><select id="stackQuality">
      <option value="best"${s.quality === "best" ? " selected" : ""}>Best available, and better copies as they appear</option>
      <option value="default"${s.quality === "default" ? " selected" : ""}>Each app's own profile</option></select>
      <small>Best available can mean 4K files of 40 GB or more. Seerr's requests and music requests use it; the apps search for upgrades weekly.</small></label>
    <form id="stackForm" class="actions"><label class="field grow"><span>Your password</span>
      <input type="password" id="stackPw" autocomplete="current-password" placeholder="Only needed the first time: it becomes your login in Jellyfin, Seerr and the *arrs"></label>
      <button class="primary small"${s.run.running ? " disabled" : ""}>${s.run.finished ? "Set up again" : "Set up everything"}</button></form>
  </section>`;
  $("#stackQuality").addEventListener("change", (e) => put("/api/stack/quality", { best: e.target.value === "best" })
    .then((r) => toast(r.detail.charAt(0).toUpperCase() + r.detail.slice(1)), (err) => toast(err.message)));
  $("#checkrrReplace")?.addEventListener("change", (e) => post("/api/stack/checkrr", { replace: e.target.checked })
    .then((r) => toast(r.replaces ? "Checkrr replaces broken files" : "Checkrr only lists broken files"), (err) => { e.target.checked = !e.target.checked; toast(err.message); }));
  $("#questLogin")?.addEventListener("click", (e) => api("/api/stack/questarr/login")
    .then((l) => { e.target.outerHTML = `<span id="questLogin">name <code>${esc(l.name)}</code>, password <code>${esc(l.password)}</code></span>`; }, (err) => toast(err.message)));
  $("#stackForm").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/api/stack/setup", { password: $("#stackPw").value || null }).then(() => { toast("Setting up your apps…"); drawServices(t); }, (err) => toast(err.message));
  });
  const again = () => t === ticket && drawServices(t);
  if (s.run.running) setTimeout(again, 2500);
}

/* Settings → Services → VPN: qBittorrent's tunnel, set any time after the stack is running */
const VPN_PILL = { connected: ["available", "Connected"], off: ["failed", "Off"], waiting: ["pending", "Blocked"], starting: ["pending", "Starting"],
  connecting: ["pending", "Reconnecting"], restarting: ["pending", "Restarting"], stopped: ["failed", "Not running"] };
async function drawVpn(t) {
  const box = $("#vpn");
  const v = await api("/api/stack/vpn").catch(() => null);
  if (!box || t !== ticket || !v?.available) return;
  box.innerHTML = `<section class="panel"><div class="head"><div><h3>VPN</h3>
      <p>${esc(v.words)}.${v.endpoint ? ` Server: ${esc(v.endpoint)}.` : ""} Upload the WireGuard file from your VPN provider; replacing it switches servers.</p></div>
      ${pill(...(VPN_PILL[v.state] || ["failed", v.state.startsWith("broken") ? "Broken" : "Unknown"]))}</div>
    <div class="actions">
      <label class="ghost" style="cursor:pointer" tabindex="0" role="button" id="wgPick">${v.file ? "Replace the WireGuard file" : "Upload a WireGuard file"}<input type="file" id="wgFile" accept=".conf,text/plain" hidden></label>
      ${v.file ? `<button class="ghost danger" id="wgRemove">Remove it</button>` : ""}
      ${v.off ? `<button class="ghost" id="vpnOn">Turn the VPN back on</button>` : `<button class="ghost danger" id="vpnOff">Run without a VPN…</button>`}
    </div></section>`;
  const redraw = (msg) => () => { toast(msg); drawVpn(t); };
  const fail = (err) => toast(err.message);
  $("#wgFile").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    e.target.value = "";  // the same file can be picked again after fixing it
    if (f) put("/api/stack/vpn", { conf: await f.text() }).then(redraw("Saved: the VPN restarts with it"), fail);
  });
  $("#wgPick").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#wgFile").click(); } });
  $("#wgRemove")?.addEventListener("click", () => confirm("Remove the WireGuard file? qBittorrent goes offline until there's a new one.")
    && del("/api/stack/vpn").then(redraw("Removed: qBittorrent is offline"), fail));
  $("#vpnOn")?.addEventListener("click", () => put("/api/stack/vpn/off", { off: false }).then(redraw("VPN back on"), fail));
  $("#vpnOff")?.addEventListener("click", () => confirm("Run qBittorrent without a VPN? Its traffic, and your home's IP address, will be visible to everyone you download from and to your internet provider.")
    && put("/api/stack/vpn/off", { off: true, confirm: true }).then(redraw("VPN off: qBittorrent connects directly"), fail));
  if (["starting", "connecting", "restarting"].includes(v.state)) setTimeout(() => t === ticket && drawVpn(t), 3000);
}

/* Settings → Music apps: app passwords for Subsonic clients; a new one is shown once */
async function drawAppKeys(t, fresh) {
  const keys = await api("/api/me/app-passwords").catch(() => null);
  const box = $("#appKeyList");
  if (!box || t !== ticket || !keys) return;
  box.innerHTML = (fresh ? `<div class="notice"><p>The app password for <strong>${esc(fresh.name)}</strong>. Copy it now: it isn't shown again.</p>
      <div class="actions"><code class="secret" id="freshKey">${esc(fresh.secret)}</code><button class="ghost" id="copyKey">Copy</button></div></div>` : "")
    + (keys.length ? `<ul class="svc-list">${keys.map((k) => `<li class="svc two"><div><strong>${esc(k.name)}</strong>
        <small class="dim">made ${since(k.created)}${k.used ? `, last used ${since(k.used)}` : ", not used yet"}</small></div>
        <button class="ghost danger" data-drop-key="${k.id}">Delete</button></li>`).join("")}</ul>` : `<p class="dim">No app passwords yet.</p>`);
  $("#copyKey")?.addEventListener("click", () => navigator.clipboard.writeText(fresh.secret).then(() => toast("Copied"), () => toast("Select it and copy")));
  box.querySelectorAll("[data-drop-key]").forEach((b) => b.addEventListener("click", () =>
    confirm("Delete this app password? The app using it stops working until you give it a new one.")
      && del(`/api/me/app-passwords/${b.dataset.dropKey}`).then(() => drawAppKeys(t), (err) => toast(err.message))));
}

async function drawApiKeys(t, fresh) {
  const keys = await api("/api/me/api-keys").catch(() => null);
  const box = $("#apiKeyList");
  if (!box || t !== ticket || !keys) return;
  box.innerHTML = (fresh ? `<div class="notice"><p>The key for <strong>${esc(fresh.name)}</strong>. Copy it now: it isn't shown again.</p>
      <div class="actions"><code class="secret" id="freshApiKey">${esc(fresh.key)}</code><button class="ghost" id="copyApiKey">Copy</button></div></div>` : "")
    + (keys.length ? `<ul class="svc-list">${keys.map((k) => `<li class="svc two"><div><strong>${esc(k.name)}</strong> <code>${esc(k.hint)}</code>
        <small class="dim">${k.scope === "search" ? "search and read only" : "search, download and request"} · made ${since(k.created)}${k.used ? `, last used ${since(k.used)}` : ", not used yet"}</small></div>
        <button class="ghost danger" data-drop-api="${k.id}">Delete</button></li>`).join("")}</ul>` : `<p class="dim">No API keys yet.</p>`);
  $("#copyApiKey")?.addEventListener("click", () => navigator.clipboard.writeText(fresh.key).then(() => toast("Copied"), () => toast("Select it and copy")));
  box.querySelectorAll("[data-drop-api]").forEach((b) => b.addEventListener("click", () =>
    confirm("Delete this key? Whatever uses it stops working at once.") && del(`/api/me/api-keys/${b.dataset.dropApi}`).then(() => drawApiKeys(t), (err) => toast(err.message))));
}

/* Settings → Signed-in devices: your sessions, each one can be signed out */
const deviceName = (agent) => (agent || "").replace(/^Mozilla\/5\.0 \(([^)]*)\)/, "$1").replace(/\s+/g, " ").slice(0, 80) || "Unknown device";
async function drawDevices(t) {
  const list = await api("/api/me/sessions").catch(() => null);
  const box = $("#deviceList");
  if (!box || t !== ticket || !list) return;
  box.innerHTML = `<ul class="svc-list">${list.map((d) => `<li class="svc two"><div><strong>${esc(d.tv ? "A paired TV" : deviceName(d.agent))}</strong>${d.current ? " <span class=\"dim\">(this one)</span>" : ""}
      <small class="dim">signed in ${since(d.created)}, last used ${since(d.seen)}</small></div>
      <button class="ghost danger" data-drop-device="${esc(d.id)}"${d.current ? " data-here" : ""}>Sign out</button></li>`).join("")}</ul>`;
  box.querySelectorAll("[data-drop-device]").forEach((b) => b.addEventListener("click", () =>
    confirm("Sign this device out? It has to sign in again.") && del(`/api/me/sessions/${b.dataset.dropDevice}`).then(
      () => (b.hasAttribute("data-here") ? location.reload() : drawDevices(t)), (err) => toast(err.message))));
}

/* You: your lists, your library and settings in one place (the phone's "more" page) */
async function showYou(t) {
  paint(t, `<section class="home">${aurora}<h1>${esc(ME?.name || "You")}</h1><p>Your lists, your library and your settings.</p></section>
    <div class="wrap">${youHtml()}${slot("youContinue", "", "")}${slot("youLater", "", "")}</div>`);
  api("/api/me/history?unfinished=true&limit=16").then((r) => fill(t, "youContinue", r.items.length ? shelf("Continue watching", `<div class="vstrip">${r.items.map((v) => vcard(v)).join("")}</div>`, "#/yt/continue") : ""), () => fill(t, "youContinue", ""));
  api("/api/me/later").then((r) => { const items = r.items || r; fill(t, "youLater", items.length ? shelf("Watch later", `<div class="vstrip">${items.slice(0, 16).map((v) => vcard(v)).join("")}</div>`, "#/yt/later") : ""); }, () => fill(t, "youLater", ""));
}

async function showSearchAll(t, parts) {
  const q = parts.join("/");
  input.value = q;
  const enc = encodeURIComponent(q);
  paint(t, `<div class="wrap">
    ${slot("sYt", "", skeletons(4))}
    ${slot("sMedia", "", "")}
    ${slot("sMusic", "", "")}
  </div>`);
  api(`/api/yt/search?q=${enc}`).then((r) => {
    if (r.link) return openYtLink(r.link);
    const vids = r.items.filter((i) => i.type === "video").slice(0, 8), chans = r.items.filter((i) => i.type === "channel").slice(0, 6);
    fill(t, "sYt", vids.length || chans.length ? `<div class="shelf-head"><h2>${tag("var(--coral)", "Videos")}</h2><a class="more" href="#/yt/search/${enc}">More videos</a></div>
      ${chans.length ? `<div class="strip" style="grid-auto-columns:minmax(150px,170px);margin-bottom:18px">${chans.map(ccard).join("")}</div>` : ""}${vgrid(vids)}` : "");
  }, (e) => fill(t, "sYt", `<p class="dim">YouTube search failed: ${esc(e.message)}</p>`));
  checkSeerr().then((ok) => (ok === true || ok.connected ? api(`/api/seerr/search?q=${enc}`) : { items: [] })).then((r) => {
    const titles = r.items.filter((x) => x.type !== "person");
    fill(t, "sMedia", titles.length ? `<div class="shelf-head"><h2>${tag("var(--violet)", "Films and shows")}</h2><a class="more" href="#/media/search/${enc}">More</a></div>${pstrip(titles)}` : "");
  }, () => fill(t, "sMedia", ""));
  api(`/music/api/search?q=${enc}`).then((r) => {
    if (r.link) return fill(t, "sMusic", `<div class="shelf-head"><h2>${tag("var(--amber)", "Music")}</h2></div><p><a class="ghost" href="#/${esc(r.link.type)}/${esc(r.link.id)}">Open the Spotify ${esc(r.link.type)}</a></p>`);
    const songs = rows(r.tracks.slice(0, 5), { art: true });
    const artists = r.artists.slice(0, 8).map((a) => tile("artist", a, "Artist", false)).join("");
    const albums = r.albums.slice(0, 8).map((a) => tile("album", a, [a.year, creditText(a.artists)].filter(Boolean).join(", "))).join("");
    fill(t, "sMusic", songs || artists || albums ? `<div class="shelf-head"><h2>${tag("var(--amber)", "Music")}</h2><a class="more" href="#/music/search/${enc}">More music</a></div>
      ${songs}${artists ? `<div class="strip" style="margin-top:22px">${artists}</div>` : ""}${albums ? `<div class="strip" style="margin-top:22px">${albums}</div>` : ""}` : "");
  }, () => fill(t, "sMusic", ""));
}

/* ---------- settings ---------- */

const SB_CHOICES = [["sponsor", "Sponsors"], ["selfpromo", "Self-promotion"], ["interaction", "Like and subscribe reminders"], ["intro", "Intros"], ["outro", "End cards and credits"],
  ["preview", "Previews and recaps"], ["filler", "Tangents and filler"], ["music_offtopic", "Non-music parts of music videos"]];

async function showSettings(t) {
  const me = ME;
  const check = (id, label, on, note = "") => `<label class="check"><input type="checkbox" id="${id}"${on ? " checked" : ""}><span>${label}${note ? `<br><small class="dim">${note}</small>` : ""}</span></label>`;
  paint(t, `<section class="home">${aurora}<h1>Settings</h1><p>Signed in as <strong>${esc(me.name)}</strong>${me.admin ? ", an admin" : ""}.</p></section>
  <div class="wrap settings-grid">
    <div>
      <section class="panel"><h3>Playing videos</h3>
        <p>These stay on this device.</p>
        <label class="field"><span>Quality</span><select id="pQuality">${[["best", "Best available (up to 4K)"], ["2160", "2160p (4K)"], ["1440", "1440p"], ["1080", "1080p"], ["720", "720p"], ["480", "480p"], ["360", "360p"]]
          .map(([v, l]) => `<option value="${v}"${pref("quality") === v ? " selected" : ""}>${l}</option>`).join("")}</select><small>Also the default for downloads.</small></label>
        ${check("pAutoplay", "Play the next video automatically", pref("autoplay"))}
        ${check("pShorts", "Hide Shorts in feeds", pref("hideShorts"))}
        ${check("pCut", "Cut sponsors out of downloaded videos", pref("cutSponsors"), "Uses SponsorBlock when the download finishes.")}
        <label class="field"><span>Captions language</span><input id="pCaptions" value="${esc(pref("captions"))}" placeholder="for example en, or leave empty for off"></label>
      </section>
      <section class="panel"><h3>Skip automatically</h3>
        <p>Parts of videos the SponsorBlock community has marked. You can always undo a skip.</p>
        ${SB_CHOICES.map(([k, l]) => check(`sb-${k}`, l, pref("skip").includes(k))).join("")}
      </section>
      <section class="panel"><h3>Your YouTube account</h3>
        <p>Optional. Add your YouTube cookies to see YouTube's own recommendations, your YouTube subscriptions feed and watch later, and to play videos that need a sign-in.
          Export them with a "cookies.txt" browser extension while signed in to youtube.com. Only this server sees them. YouTube can flag accounts used like this, so a spare account is safer.</p>
        <p><strong>${me.youtube_cookies ? "Cookies added." : "No cookies added."}</strong></p>
        <label class="field"><span class="vh">cookies.txt</span><textarea id="cookieText" spellcheck="false" placeholder="# Netscape HTTP Cookie File"></textarea></label>
        <div class="actions"><button class="primary small" id="saveCookies">Save cookies</button>
          <label class="ghost" style="cursor:pointer">Open cookies.txt<input type="file" id="cookieFile" accept=".txt" hidden></label>
          ${me.youtube_cookies ? `<button class="ghost danger" id="dropCookies">Remove them</button>` : ""}</div>
      </section>
      <section class="panel" id="appKeys"><h3>Music apps</h3>
        <p>Symfonium, Tempo, DSub, Feishin and other Subsonic apps can play your music folder straight from here. In the app, add a Subsonic server:
          address <strong>${esc(location.origin)}</strong>, user <strong>${esc(me.name)}</strong>, and an app password made below (or your own password, if the app has no other choice).</p>
        <div id="appKeyList">${SPIN}</div>
        <form id="appKeyForm" class="actions"><label class="field grow"><span class="vh">Name</span><input id="appKeyName" maxlength="40" placeholder="Which app, like Symfonium on my phone" required></label>
          <button class="primary small">Make an app password</button></form>
      </section>
      <section class="panel" id="apiKeys"><h3>API keys</h3>
        <p>For scripts and other programs: a key acts as you, with what your account may use. Send it as <code>X-Api-Key</code>.
          Search everything with <code>/api/v1/search?q=…</code>, get anything with <code>POST /api/v1/get</code>; the details are in <a href="/api/docs" target="_blank" rel="noopener">the API docs</a>.</p>
        <div id="apiKeyList">${SPIN}</div>
        <form id="apiKeyForm" class="actions"><label class="field grow"><span class="vh">Name</span><input id="apiKeyName" maxlength="40" placeholder="What uses it, like Home Assistant" required></label>
          <label class="field"><span class="vh">What it may do</span><select id="apiKeyScope"><option value="full">Search, download and request</option><option value="search">Search and read only</option></select></label>
          <button class="primary small">Make a key</button></form>
      </section>
      <section class="panel" id="devices"><h3>Signed-in devices</h3>
        <p>Where your account is signed in. Sign one out and it has to sign in again; only you can do this for your own devices.</p>
        <div id="deviceList">${SPIN}</div>
      </section>
      <section class="panel" id="devicePanel"></section>
      <section class="panel" id="privatePanel"></section>
      <section class="panel"><h3>Your account</h3>
        ${me.local ? `<form id="pwForm"><label class="field"><span>Current password</span><input type="password" id="pwOld" autocomplete="current-password" required></label>
          <label class="field"><span>New password</span><input type="password" id="pwNew" autocomplete="new-password" minlength="8" required></label>
          <div class="actions"><button class="primary small">Change password</button></div></form>`
          : `<p>You sign in with your Jellyfin account; change your password there.</p>`}
        <div class="actions"><button class="ghost" id="everywhere">${ICON.door}Sign out everywhere</button></div>
      </section>
    </div>
    <div id="adminSide">${me.admin ? `<section class="panel"><div class="head"><h3>Admin</h3></div><div class="wrap" style="padding:0">${SPIN}</div></section>` : ""}</div>
  </div>`);

  const keep = (id, key, read) => $(id).addEventListener("change", () => { setPref(key, read($(id))); toast("Saved"); });
  keep("#pQuality", "quality", (el) => el.value);
  keep("#pAutoplay", "autoplay", (el) => el.checked);
  keep("#pShorts", "hideShorts", (el) => el.checked);
  keep("#pCut", "cutSponsors", (el) => el.checked);
  keep("#pCaptions", "captions", (el) => el.value.trim());
  SB_CHOICES.forEach(([k]) => $(`#sb-${k}`).addEventListener("change", () => {
    setPref("skip", SB_CHOICES.map(([c]) => c).filter((c) => $(`#sb-${c}`).checked));
    toast("Saved");
  }));
  drawAppKeys(t);
  drawApiKeys(t);
  drawDevices(t);
  drawDevice();
  drawPrivatePanel();
  $("#apiKeyForm").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/api/me/api-keys", { name: $("#apiKeyName").value, scope: $("#apiKeyScope").value }).then((k) => { $("#apiKeyName").value = ""; drawApiKeys(t, k); },
      (err) => toast(err.message));
  });
  $("#appKeyForm").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/api/me/app-passwords", { name: $("#appKeyName").value }).then((k) => {
      $("#appKeyName").value = "";
      drawAppKeys(t, k);
    }, (err) => toast(err.message));
  });
  $("#cookieFile").addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) $("#cookieText").value = await f.text(); });
  $("#saveCookies").addEventListener("click", () => put("/api/me/youtube-cookies", { text: $("#cookieText").value })
    .then(async (r) => { toast(`Saved ${plural(r.cookies, "cookie")}`); ME = await api("/api/me"); route(); }, (e) => toast(e.message)));
  $("#dropCookies")?.addEventListener("click", () => del("/api/me/youtube-cookies").then(async () => { ME = await api("/api/me"); route(); }));
  $("#pwForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    post("/api/me/password", { old: $("#pwOld").value, new: $("#pwNew").value }).then(() => { toast("Password changed. Other devices have to sign in again."); e.target.reset(); }, (err) => toast(err.message));
  });
  $("#everywhere").addEventListener("click", () => confirm("Sign out on every device, this one too?") && post("/api/auth/logout-everywhere").finally(() => location.reload()));
  if (me.admin) adminPanels(t);
}

async function adminPanels(t) {
  const [s, users, status, yt] = await Promise.all([api("/api/settings"), api("/api/users"), api("/api/seerr/status"), api("/api/yt/status").catch(() => ({}))]);
  const seerr = status.connected ? await api("/api/seerr/users").catch(() => ({ users: [], suggested: {} })) : { users: [], suggested: {} };
  const seerrUsers = seerr.users;
  if (t !== ticket) return;
  const option = (u, sel) => `<option value="${u.id}"${sel === u.id ? " selected" : ""}>${esc(u.name)}${u.jellyfin ? ` (Jellyfin: ${esc(u.jellyfin)})` : ""}</option>`;
  const limits = (u) => [u.blocked.length && `no ${u.blocked.map((s) => SECTION_NAMES[s][0].toLowerCase()).join(", ")}`,
    u.libraries && `${u.libraries.length} ${u.libraries.length === 1 ? "library" : "libraries"}`].filter(Boolean).join(" · ");
  const userRow = (u) => `<tr data-user="${u.id}">
    <td><strong>${esc(u.name)}</strong><br><small class="dim">${u.admin ? "Admin" : "Member"}${u.jellyfin ? " · Jellyfin" : ""}${u.seen ? ` · seen ${since(u.seen)}` : ""}${!u.admin && limits(u) ? ` · ${esc(limits(u))}` : ""}</small></td>
    <td>${status.connected ? `<select class="input" data-seerr aria-label="Seerr user for ${esc(u.name)}"><option value="">${u.jellyfin ? "Same Jellyfin account" : "Not linked"}</option>${seerrUsers.map((x) => option(x, u.seerr_id)).join("")}</select>
      ${!u.seerr_id && seerr.suggested[u.id] ? `<br><small class="dim">Same name in Seerr: ${esc(seerrUsers.find((x) => x.id === seerr.suggested[u.id])?.name || "")}. Pick it if it's them.</small>` : ""}` : `<small class="dim">Seerr not connected</small>`}
      ${u.jellyfin ? "" : `<br><input class="input" data-jf value="${esc(u.jf_user || "")}" placeholder="Jellyfin user: the default" autocomplete="off" aria-label="Jellyfin user ${esc(u.name)} plays as">`}</td>
    <td><div class="acts">
      ${u.admin ? "" : `<button class="ghost" data-access aria-expanded="false">Access</button>`}
      <button class="ghost" data-admin-toggle="${u.admin ? 0 : 1}">${u.admin ? "Make member" : "Make admin"}</button>
      ${u.jellyfin ? "" : `<button class="ghost" data-reset>Reset password</button>`}
      ${u.id === ME.id ? "" : `<button class="ghost danger" data-delete-user>Delete</button>`}</div></td></tr>`;
  $("#adminSide").innerHTML = `<div id="services"></div><div id="vpn"></div><div id="security"></div><div id="tvPanel"></div>
    <section class="panel"><div class="head"><div><h3>Seerr</h3><p>${status.connected ? `Connected${status.version ? `, version ${esc(status.version)}` : ""}.` : `Not connected${status.reason && status.reason !== "not set up" ? `: ${esc(status.reason)}` : "."}`}</p></div>
        ${pill(status.connected ? "available" : "failed", status.connected ? "Connected" : "Offline")}</div>
      <form id="seerrForm">
        <label class="field"><span>Address</span><input id="seerrUrl" value="${esc(s.seerr_url)}" placeholder="http://localhost:5055"><small>From this server's point of view.</small></label>
        <label class="field"><span>API key</span><input id="seerrKey" type="password" autocomplete="off" placeholder="${s.seerr_key_set ? `saved (${esc(s.seerr_key_hint)}); leave empty to keep it` : "Seerr → Settings → General → API Key"}"></label>
        <div class="actions"><button class="primary small">Save and test</button></div></form>
    </section>
    <section class="panel"><h3>Jellyfin and signing in</h3>
      <form id="signinForm">
        <label class="field"><span>Jellyfin address</span><input id="jfUrl" value="${esc(s.jellyfin_url)}" placeholder="http://localhost:8096"><small>When set, people can sign in with their Jellyfin account; Jellyfin admins are admins here.</small></label>
        <label class="field"><span>Jellyfin API key</span><input id="jfKey" type="password" autocomplete="off" placeholder="${s.jellyfin_key_set ? "saved; leave empty to keep it" : "Jellyfin → Dashboard → API Keys"}"><small>The Library plays through it. Setup makes one for the Jellyfin that came with Parlor; a Jellyfin of your own needs its key here.</small></label>
        <label class="field"><span>Jellyfin user to play as</span><input id="jfUser" value="${esc(s.jellyfin_user)}" autocomplete="off"><small>Whose library and watched marks people get when their account isn't linked to a Jellyfin user of their own.</small></label>
        <label class="check"><input type="checkbox" id="openSignup"${s.open_signup ? " checked" : ""}><span>Anyone who can reach this page can create an account</span></label>
        <div class="actions"><button class="primary small">Save</button></div></form>
    </section>
    <section class="panel" id="musicQuality" hidden><h3>Music downloads</h3>
      <label class="field"><span>New downloads are saved as</span><select id="musicQualitySel"></select>
        <small id="musicQualityNote"></small></label>
    </section>
    <section class="panel"><h3>Music requests</h3>
      <p>When Lidarr finds nothing for a requested album in 6 hours, or its download fails or stalls, Parlor gets it from YouTube instead and has Lidarr import it.</p>
      <label class="check"><input type="checkbox" id="fallbackWanted"${s.music_fallback_wanted ? " checked" : ""}><span>Do that for Lidarr's own wanted list too (albums added in Lidarr, or by watching an artist), a few at a time</span></label>
    </section>
    <section class="panel"><h3>People</h3>
      <p>Each account has its own subscriptions, history, watch later and requests. Requests go to Seerr as the linked Seerr user, with their limits and approvals. Jellyfin sign-ins link to the same Jellyfin account in Seerr by themselves; link everyone else here.</p>
      <table class="table"><thead><tr><th>Account</th><th>Seerr user</th><th></th></tr></thead><tbody>${users.map(userRow).join("")}</tbody></table>
      <form id="addUser" style="margin-top:18px"><strong>Add someone</strong>
        <label class="field"><span>Name</span><input id="newName" required maxlength="40" autocomplete="off"></label>
        <label class="field"><span>Password</span><input id="newPw" type="password" required minlength="8" autocomplete="new-password"></label>
        <label class="check"><input type="checkbox" id="newAdmin"><span>Admin</span></label>
        <div class="actions"><button class="primary small">Add account</button></div></form>
    </section>
    <section class="panel"><h3>Downloaded videos</h3>
      <form id="keepForm">
        <label class="field"><span>Delete downloaded videos</span><select class="input" id="keepDays">
          ${[[0, "Never"], [7, "After a week"], [14, "After 2 weeks"], [30, "After 30 days"], [60, "After 60 days"], [90, "After 90 days"], [180, "After 6 months"], [365, "After a year"]]
            .concat([0, 7, 14, 30, 60, 90, 180, 365].includes(yt.retention?.days ?? 0) ? [] : [[yt.retention.days, `After ${plural(yt.retention.days, "day")}`]])
            .map(([d, label]) => `<option value="${d}"${d === (yt.retention?.days ?? 0) ? " selected" : ""}>${label}</option>`).join("")}</select>
          <small>Videos anyone pressed Keep on are never deleted. Checked every hour.</small></label>
        <label class="check"><input type="checkbox" id="keepWatched"${yt.retention?.watched_only ? " checked" : ""}><span>Only once someone has watched it to the end, counting from then</span></label>
        <div class="actions"><button class="primary small">Save</button></div></form>
    </section>
    <section class="panel"><h3>About</h3><p>yt-dlp ${esc(yt.yt_dlp || "?")}, updated on every start and daily. Up to ${esc(yt.parallel || 2)} video downloads at once, saved to <code>${esc(yt.videos || "")}</code>.</p></section>`;

  $("#seerrForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const body = { seerr_url: $("#seerrUrl").value.trim() };
    if ($("#seerrKey").value.trim()) body.seerr_key = $("#seerrKey").value.trim();
    put("/api/settings", body).then(() => api("/api/seerr/status")).then((st) => {
      seerrOk = st.connected || null;
      toast(st.connected ? "Seerr is connected" : `Saved, but Seerr didn't answer: ${st.reason}`);
      keepScroll = true; route();
    }, (err) => toast(err.message));
  });
  $("#signinForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const key = $("#jfKey").value.trim();  // empty keeps the saved key: it is never sent back, so the field starts empty
    put("/api/settings", { jellyfin_url: $("#jfUrl").value.trim(), jellyfin_user: $("#jfUser").value.trim(), open_signup: $("#openSignup").checked, ...(key && { jellyfin_key: key }) }).then(() => {
      if (key) Object.assign($("#jfKey"), { value: "", placeholder: "saved; leave empty to keep it" });
      toast("Saved");
    }, (err) => toast(err.message));
  });
  // what new music downloads are saved as (music-fndr's choice); songs already there are never fetched again
  api("/music/api/quality").then((q) => {
    const sel = $("#musicQualitySel");
    if (!sel || t !== ticket) return;
    const note = () => { $("#musicQualityNote").textContent = `${(q.choices.find((c) => c.id === sel.value)?.label.split(": ")[1] || "Set by FORMAT and BITRATE in .env") }. Songs you already have are never fetched again.`; };
    sel.innerHTML = (q.quality === "custom" ? `<option value="custom" disabled>As set by FORMAT and BITRATE in .env</option>` : "")
      + q.choices.map((c) => `<option value="${esc(c.id)}">${esc(c.label.split(":")[0])}</option>`).join("");
    sel.value = q.quality;
    note();
    $("#musicQuality").hidden = false;
    sel.addEventListener("change", () => put("/music/api/quality", { quality: sel.value }).then(() => { note(); toast("Saved: new downloads use it"); }, (err) => toast(err.message)));
  }, () => {});
  $("#fallbackWanted")?.addEventListener("change", (e) => {
    put("/api/settings", { music_fallback_wanted: e.target.checked }).then(() => toast("Saved"), (err) => { e.target.checked = !e.target.checked; toast(err.message); });
  });
  drawServices(t);
  drawVpn(t);
  drawSecurity();
  drawTvPanel();
  $("#keepForm").addEventListener("submit", (e) => {
    e.preventDefault();
    put("/api/yt/retention", { days: +$("#keepDays").value, watched_only: $("#keepWatched").checked })
      .then((r) => toast(ruleText(r).split(".")[0] + "."), (err) => toast(err.message));
  });
  $("#addUser").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/api/users", { name: $("#newName").value, password: $("#newPw").value, admin: $("#newAdmin").checked })
      .then(() => { toast("Account added"); keepScroll = true; route(); }, (err) => toast(err.message));
  });
  $("#adminSide").addEventListener("change", (e) => {
    const jf = e.target.closest("[data-jf]");  // empty: back to the default user (Jellyfin and signing in)
    if (jf) return patch(`/api/users/${jf.closest("[data-user]").dataset.user}`, { jf_user: jf.value.trim() }).then(() => toast("Saved"), (err) => toast(err.message));
    const sel = e.target.closest("[data-seerr]");
    if (!sel) return;
    const id = sel.closest("[data-user]").dataset.user;
    patch(`/api/users/${id}`, sel.value ? { seerr_id: +sel.value } : { unlink_seerr: true }).then(() => toast("Saved"), (err) => toast(err.message));
  });
  $("#adminSide").addEventListener("click", (e) => {
    const row = e.target.closest("[data-user]");
    if (!row) return;
    const id = row.dataset.user;
    const done = () => { keepScroll = true; route(); };
    const opener = e.target.closest("[data-access]");
    if (opener) accessEditor(row, users.find((u) => String(u.id) === id), opener);
    const flip = e.target.closest("[data-admin-toggle]");
    if (flip) patch(`/api/users/${id}`, { admin: flip.dataset.adminToggle === "1" }).then(done, (err) => toast(err.message));
    if (e.target.closest("[data-reset]")) {
      const pw = prompt("New password (at least 8 characters). They'll be signed out everywhere.");
      if (pw) patch(`/api/users/${id}`, { password: pw }).then(() => toast("Password reset"), (err) => toast(err.message));
    }
    if (e.target.closest("[data-delete-user]") && confirm("Delete this account, with its subscriptions and history?")) del(`/api/users/${id}`).then(done, (err) => toast(err.message));
  });
}

/* ---------- sign in ---------- */

async function showGate(mode, here = false) {
  ME = null;
  forgetMe();
  const state = await fetch("/api/auth/state").then((r) => r.json()).catch(() => ({}));
  if (TV_MODE && !here && !state.setup) return showTvPair(); // a TV pairs by a code instead (tv.js)
  mode ||= state.setup ? "setup" : "login";
  const titles = { setup: ["Welcome to Parlor", "Create the admin account. You can add everyone else after."], login: ["Sign in", state.jellyfin ? "Use your Parlor or Jellyfin account." : ""], signup: ["Create an account", ""] };
  const [title, blurb] = titles[mode];
  const pwHint = mode === "login" ? "current-password" : "new-password"; // the browser's autocomplete hint  pragma: allowlist secret
  $("#gate").innerHTML = `<div class="gate">${aurora}
    <form id="gateForm" autocomplete="on">
      <div class="brand"><img src="logo.svg" alt=""><span>Parlor</span></div>
      <h1>${esc(title)}</h1>${blurb ? `<p>${esc(blurb)}</p>` : ""}
      <label class="field"><span>Name</span><input id="gName" name="username" autocomplete="username" required maxlength="40" autofocus></label>
      <label class="field"><span>Password</span><input id="gPw" name="password" type="password" autocomplete="${pwHint}" required ${mode === "login" ? "" : 'minlength="8"'}></label>
      <p class="err" id="gErr" role="alert"></p>
      <button class="primary">${mode === "setup" ? "Create and sign in" : mode === "signup" ? "Create account" : "Sign in"}</button>
      ${mode === "login" && state.signup ? `<p class="alt">New here? <button type="button" data-mode="signup">Create an account</button></p>` : ""}
      ${mode === "signup" ? `<p class="alt"><button type="button" data-mode="login">I have an account</button></p>` : ""}
      <div class="marks"><span><i style="background:var(--coral)"></i>Videos</span><span><i style="background:var(--violet)"></i>Films &amp; TV</span><span><i style="background:var(--amber)"></i>Music</span></div>
    </form></div>`;
  $("#gate [data-mode]")?.addEventListener("click", (e) => showGate(e.target.dataset.mode));
  $("#gateForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("#gateForm .primary");
    button.disabled = true;
    try {
      await post(`/api/auth/${mode}`, { name: $("#gName").value.trim(), password: $("#gPw").value });
      $("#gate").innerHTML = "";
      await boot();
    } catch (err) {
      $("#gErr").textContent = err.message;
      button.disabled = false;
    }
  });
}

/* ---------- your private items: a key of your own, sealed by your password ---------- */

async function drawPrivatePanel(code) {
  const box = $("#privatePanel");
  if (!box) return;
  if (ME?.sections && (ME.sections.watch === false || ME.sections.downloads === false)) return box.remove();
  const s = await api("/api/private/state").catch(() => null);
  if (!s) return box.remove();
  const pw = (id, label = "Your password") => `<label class="field"><span>${label}</span><input type="password" id="${id}" autocomplete="current-password" required></label>`;
  box.innerHTML = `<h3>Private items</h3>
    ${code ? `<div class="notice"><strong>Your recovery code, shown this once:</strong> <code class="secret">${esc(code)}</code> <button type="button" class="ghost small" id="copyCode">Copy</button>
        <p>Write it down somewhere safe. If an admin ever resets your password, only this code opens your private items again; without it they're gone, for you and for everyone.</p></div>` : ""}
    ${!s.on ? `<p>Make a downloaded video private and it's encrypted with a key that's yours alone, sealed by your password. Only you, and people you share it with, can open it: not the server's other people, not its admins.</p>
        <form id="privOn">${pw("privPw")}<div class="actions"><button class="primary small">Turn on private items</button></div></form>`
      : s.open ? `<p>On. Your private items open while you're signed in; make a video private with the lock button on Downloaded.</p>`
      : `<p>On, and locked for now (Parlor restarted, or your password was reset). Enter your password to open them.</p>
        <form id="privOpen">${pw("privOpenPw")}<div class="actions"><button class="primary small">Open</button></div></form>
        <details><summary>Your password was reset by an admin?</summary>
          <form id="privRecover"><label class="field"><span>Recovery code</span><input id="privCode" autocomplete="off" required></label>${pw("privRecPw")}
            <div class="actions"><button class="primary small">Use the recovery code</button></div></form></details>`}`;
  $("#copyCode")?.addEventListener("click", () => navigator.clipboard.writeText(code).then(() => toast("Copied"), () => toast("Select it and copy")));
  const send = (id, path, body, done) => $(id)?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await post(path, body()); done(r); } catch (err) { toast(err.message); }
  });
  send("#privOn", "/api/private/enable", () => ({ password: $("#privPw").value }), (r) => drawPrivatePanel(r.recovery_code));
  send("#privOpen", "/api/private/unlock", () => ({ password: $("#privOpenPw").value }), () => { toast("Opened"); drawPrivatePanel(); });
  send("#privRecover", "/api/private/recover", () => ({ code: $("#privCode").value, password: $("#privRecPw").value }), () => { toast("Opened, and sealed by your password again"); drawPrivatePanel(); });
}

/* ---------- who may use what (admins) ---------- */

const SECTION_NAMES = {
  watch: ["Watch", "YouTube: feeds, videos, subscriptions"], media: ["Films & shows", "discovering and requesting films and shows"],
  music: ["Music", "finding and requesting music, and music apps"], library: ["Library", "playing what's in Jellyfin"],
  downloads: ["Downloads", "saving videos and songs to the server"],
};

async function accessEditor(row, u, opener) {
  const open = row.nextElementSibling?.classList.contains("access-row");
  row.parentElement.querySelectorAll(".access-row").forEach((r) => r.remove());
  row.parentElement.querySelectorAll("[data-access]").forEach((b) => b.setAttribute("aria-expanded", "false"));
  if (open) return;
  opener.setAttribute("aria-expanded", "true");
  const libs = await api("/api/access/libraries").then((r) => r.libraries).catch(() => null);
  const edit = document.createElement("tr");
  edit.className = "access-row";
  const check = (name, value, on, label, note) => `<label class="check"><input type="checkbox" name="${name}" value="${esc(value)}"${on ? " checked" : ""}><span>${esc(label)}${note ? ` <small class="dim">${esc(note)}</small>` : ""}</span></label>`;
  edit.innerHTML = `<td colspan="3"><form class="access">
      <fieldset><legend>What ${esc(u.name)} can use</legend>
        ${Object.entries(SECTION_NAMES).map(([k, [label, note]]) => check("section", k, !u.blocked.includes(k), label, note)).join("")}</fieldset>
      ${libs ? `<fieldset><legend>Jellyfin libraries they see</legend>
        ${check("all", "1", u.libraries === null, "All of them, new ones included")}
        ${libs.map((l) => check("lib", l.id, u.libraries === null || u.libraries.includes(l.id), l.name)).join("")}
        <small class="dim">Held by Jellyfin itself: they play as a Jellyfin user of their own, so its apps show the same.</small></fieldset>`
        : `<p class="dim">Jellyfin isn't connected, so its libraries can't be limited yet.</p>`}
      <div class="actions"><button class="primary small">Save</button><button type="button" class="ghost" data-cancel>Cancel</button></div>
    </form></td>`;
  row.after(edit);
  const form = edit.querySelector("form");
  const all = form.querySelector("[name=all]");
  form.addEventListener("change", (e) => {
    if (e.target === all) form.querySelectorAll("[name=lib]").forEach((b) => { b.checked = all.checked; });
    else if (e.target.name === "lib" && all) all.checked = [...form.querySelectorAll("[name=lib]")].every((b) => b.checked);
  });
  form.querySelector("[data-cancel]").addEventListener("click", () => { edit.remove(); opener.setAttribute("aria-expanded", "false"); opener.focus(); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { blocked: [...form.querySelectorAll("[name=section]")].filter((b) => !b.checked).map((b) => b.value) };
    if (libs) body.libraries = all.checked ? null : [...form.querySelectorAll("[name=lib]:checked")].map((b) => b.value);
    try {
      await put(`/api/users/${u.id}/access`, body);
      toast(`Saved what ${u.name} can use`);
      keepScroll = true; route();
    } catch (err) { toast(err.message); }
  });
  form.querySelector("input")?.focus();
}

/** is this section closed to the person signed in (an admin's choice in Settings → Users)? */
const closedTo = (s) => !!ME?.sections && ME.sections[s] === false;
/** a person's own view: sections closed to them leave the menus, and download buttons go */
function applyAccess() {
  const closed = closedTo;
  const needs = { "#/games": "games" };  // pages whose section isn't their group's
  for (const g of NAV) if (g.pages) g.pages = g.pages.filter(([, href]) => !closed(needs[href]));
  const gone = { library: closed("library"), watch: closed("watch"), music: closed("music"), media: closed("media") && closed("music") && closed("games") && closed("library") };
  for (const key of Object.keys(gone)) {
    const i = NAV.findIndex((g) => g.key === key);
    if (i < 0 || !gone[key]) continue;
    if (key === "library" && !closed("games")) NAV[i] = { key: "games", label: "Games", icon: "pad", href: "#/games" };  // Games without the Library: a section of its own
    else NAV.splice(i, 1);
  }
  const you = NAV.find((g) => g.key === "you");
  if (you) you.pages = you.pages.filter(([, href]) => !(href.startsWith("#/yt") && (closed("watch") || href === "#/yt/downloads" && closed("downloads"))));
  document.body.classList.toggle("no-downloads", closed("downloads"));
}

/* ---------- locked: the encryption key waits for the owner's passphrase ---------- */

function showUnlock() {
  ME = null;
  if ($("#unlockForm")) return;
  $("#gate").innerHTML = `<div class="gate">${aurora}
    <form id="unlockForm" autocomplete="off">
      <div class="brand"><img src="logo.svg" alt=""><span>Parlor</span></div>
      <h1>Parlor is locked</h1><p>Its data is encrypted, and it opens only with the passphrase its owner set. Everything waits until then.</p>
      <label class="field"><span>Passphrase</span><input id="uPass" type="password" autocomplete="off" required autofocus></label>
      <p class="err" id="uErr" role="alert"></p>
      <button class="primary">Unlock</button>
    </form></div>`;
  $("#unlockForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("#unlockForm .primary");
    button.disabled = true;
    try {
      await post("/api/vault/unlock", { passphrase: $("#uPass").value });
      $("#gate").innerHTML = "";
      await boot();
    } catch (err) {
      $("#uErr").textContent = err.message;
      button.disabled = false;
    }
  });
}

async function drawSecurity() {
  const box = $("#security");
  if (!box) return;
  const s = await api("/api/vault/state").catch(() => null);
  if (!s) return;
  box.innerHTML = `<section class="panel"><div class="head"><div><h3>Security</h3>
      <p>${s.encrypted ? "Parlor's data (accounts, settings, keys, history and YouTube cookies) is encrypted at rest." : "Encryption is off (ENCRYPT=0): Parlor's data is stored plain."}</p></div>
      ${pill(s.encrypted ? "available" : "failed", s.encrypted ? "Encrypted" : "Plain")}</div>
    ${s.encrypted ? `<p>${s.key_apart ? "The key is kept apart from the data, in a folder of its own (<code>./parlor/key</code> in the compose)." : "The key is inside the data folder, so a copy of that folder can be read. Set <code>KEY_DIR</code> to a folder of its own."}
      <strong>Lose the key and the data is lost:</strong> keep a copy somewhere safe.</p>
    <form id="keyForm" class="actions"><label class="field grow"><span class="vh">Your password</span><input id="keyPw" type="password" autocomplete="current-password" placeholder="Your password" required></label>
      <button class="ghost">Download the key</button></form>
    <h4>Lock at boot</h4>
    <p>${s.lock_at_boot ? "On: after every start Parlor waits for the passphrase before it opens anything, downloads included." : "With a passphrase, the key on disk is sealed by it: after a restart Parlor stays locked, downloads included, until someone enters it here."}</p>
    <form id="lockForm">
      <label class="field"><span>Your password</span><input id="lockPw" type="password" autocomplete="current-password" required></label>
      ${s.lock_at_boot ? "" : `<label class="field"><span>Passphrase (12 characters or more)</span><input id="lockPass" type="password" autocomplete="new-password" minlength="12" required></label>`}
      <div class="actions"><button class="primary small">${s.lock_at_boot ? "Turn lock at boot off" : "Lock at boot"}</button></div></form>` : ""}
  </section>`;
  $("#keyForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await post("/api/vault/key", { password: $("#keyPw").value });
      $("#keyPw").value = "";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([r.key + "\n"], { type: "text/plain" }));
      a.download = r.file;
      a.click();
      URL.revokeObjectURL(a.href);
      toast("Saved as db.key. To restore, put it in ./parlor/key.");
    } catch (err) { toast(err.message); }
  });
  $("#lockForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await put("/api/vault/passphrase", { password: $("#lockPw").value, passphrase: s.lock_at_boot ? "" : $("#lockPass").value });
      toast(s.lock_at_boot ? "Lock at boot is off" : "Locked at boot: keep the passphrase safe, it can't be recovered");
      drawSecurity();
    } catch (err) { toast(err.message); }
  });
}

/* ---------- start ---------- */

page("", showDashboard, "home");
page("search", showSearchAll, "home");
page("settings", showSettings, "settings");
page("you", showYou, "settings");

async function boot() {
  if (APPED && !SERVER && !(await appServer())) return; // the Android or desktop app: which Parlor (pwa.js)
  try {
    ME = await api("/api/me");
  } catch (err) {
    if (err.offline) bootOffline(); // no network, or no server: what this device has (pwa.js)
    return; // otherwise api() showed the sign-in screen
  }
  keepMe();
  applyAccess();
  offRun(); // anything still being saved for offline carries on
  jfFlush(); // places in films made with no server, not sent yet
  tvBoot(); // a TV listens for casts; anyone else learns their TVs (tv.js)
  $("#account").textContent = (ME.name || "?").trim()[0].toUpperCase();
  $("#account").title = ME.name;
  dockRestore();
  poll();
  route();
}
window.addEventListener("hashchange", () => { if (ME) route(); });
boot();
