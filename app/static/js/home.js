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
    stats.requests != null && stat("#/requests", "var(--violet)", ICON.film, stats.requests, `${stats.requests === 1 ? "request" : "requests"} waiting for approval`),
    stats.songs != null && stat("#/library", "var(--amber)", ICON.disc, compact.format(stats.songs), `${stats.songs === 1 ? "song" : "songs"} in your library`),
  ].filter(Boolean).join(""));

  api("/api/me/history?unfinished=true&limit=12").then((r) => fill(t, "homeContinue", r.items.length
    ? shelf("Continue watching", `<div class="vstrip">${r.items.map((v) => vcard(v)).join("")}</div>`, "#/yt/continue") : ""), () => fill(t, "homeContinue", ""));
  api("/api/me/subs").then((s) => { stats.subs = s.length; drawStats(); }).catch(() => {});
  api("/api/yt/library").then((r) => { stats.videos = r.items.length; stats.used = r.used; drawStats(); }).catch(() => {});
  api("/api/yt/feed?limit=24").then((r) => fill(t, "homeSubs", r.channels
    ? shelf("New from your subscriptions", `<div class="vstrip">${hideShorts(r.items).slice(0, 16).map((v) => vcard(v)).join("")}</div>`, "#/yt/subs")
    : `<div class="shelf-head"><h2>${tag("var(--coral)", "Watch")}</h2></div><div class="empty-state" style="padding-top:0">Follow some YouTube channels and their new videos land here. <a href="#/yt/channels">Import your subscriptions</a>.</div>`),
  () => fill(t, "homeSubs", ""));
  api("/api/seerr/status").then(async (s) => {
    if (!s.connected) return fill(t, "homeMedia", ME?.admin ? `<div class="shelf-head"><h2>${tag("var(--violet)", "Movies & TV")}</h2></div><div class="empty-state" style="padding-top:0">Connect Seerr in <a href="#/settings">Settings</a> to discover and request films and shows.</div>` : "");
    const [d, mine] = await Promise.all([api("/api/seerr/discover"), api("/api/seerr/requests?filter=pending").catch(() => null)]);
    if (mine?.linked) { stats.requests = mine.items.length; drawStats(); }
    fill(t, "homeMedia", shelf("Trending films and shows", `<div class="pstrip">${(d.trending || []).filter((x) => x.type !== "person").map(pcard).join("")}</div>`, "#/media"));
  }).catch(() => fill(t, "homeMedia", ""));
  api("/music/api/home").then((feed) => fill(t, "homeMusic",
    shelf("New music this week", grid(feed.new.map((a) => tile("album", a, creditText(a.artists))).join(""), "strip"), "#/music"))).catch(() => fill(t, "homeMusic", ""));
  (libraryOnce ||= loadLibrary().catch(() => { libraryOnce = null; })).then((songs) => { if (songs?.length) { stats.songs = songs.length; drawStats(); } });
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
  const userRow = (u) => `<tr data-user="${u.id}">
    <td><strong>${esc(u.name)}</strong><br><small class="dim">${u.admin ? "Admin" : "Member"}${u.jellyfin ? " · Jellyfin" : ""}${u.seen ? ` · seen ${since(u.seen)}` : ""}</small></td>
    <td>${status.connected ? `<select class="input" data-seerr aria-label="Seerr user for ${esc(u.name)}"><option value="">${u.jellyfin ? "Same Jellyfin account" : "Not linked"}</option>${seerrUsers.map((x) => option(x, u.seerr_id)).join("")}</select>
      ${!u.seerr_id && seerr.suggested[u.id] ? `<br><small class="dim">Same name in Seerr: ${esc(seerrUsers.find((x) => x.id === seerr.suggested[u.id])?.name || "")}. Pick it if it's them.</small>` : ""}` : `<small class="dim">Seerr not connected</small>`}</td>
    <td><div class="acts">
      <button class="ghost" data-admin-toggle="${u.admin ? 0 : 1}">${u.admin ? "Make member" : "Make admin"}</button>
      ${u.jellyfin ? "" : `<button class="ghost" data-reset>Reset password</button>`}
      ${u.id === ME.id ? "" : `<button class="ghost danger" data-delete-user>Delete</button>`}</div></td></tr>`;
  $("#adminSide").innerHTML = `
    <section class="panel"><div class="head"><div><h3>Seerr</h3><p>${status.connected ? `Connected${status.version ? `, version ${esc(status.version)}` : ""}.` : `Not connected${status.reason && status.reason !== "not set up" ? `: ${esc(status.reason)}` : "."}`}</p></div>
        ${pill(status.connected ? "available" : "failed", status.connected ? "Connected" : "Offline")}</div>
      <form id="seerrForm">
        <label class="field"><span>Address</span><input id="seerrUrl" value="${esc(s.seerr_url)}" placeholder="http://localhost:5055"><small>From this server's point of view.</small></label>
        <label class="field"><span>API key</span><input id="seerrKey" type="password" autocomplete="off" placeholder="${s.seerr_key_set ? `saved (${esc(s.seerr_key_hint)}); leave empty to keep it` : "Seerr → Settings → General → API Key"}"></label>
        <div class="actions"><button class="primary small">Save and test</button></div></form>
    </section>
    <section class="panel"><h3>Signing in</h3>
      <form id="signinForm">
        <label class="field"><span>Jellyfin address</span><input id="jfUrl" value="${esc(s.jellyfin_url)}" placeholder="http://localhost:8096"><small>When set, people can sign in with their Jellyfin account; Jellyfin admins are admins here.</small></label>
        <label class="check"><input type="checkbox" id="openSignup"${s.open_signup ? " checked" : ""}><span>Anyone who can reach this page can create an account</span></label>
        <div class="actions"><button class="primary small">Save</button></div></form>
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
    put("/api/settings", { jellyfin_url: $("#jfUrl").value.trim(), open_signup: $("#openSignup").checked }).then(() => toast("Saved"), (err) => toast(err.message));
  });
  $("#addUser").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/api/users", { name: $("#newName").value, password: $("#newPw").value, admin: $("#newAdmin").checked })
      .then(() => { toast("Account added"); keepScroll = true; route(); }, (err) => toast(err.message));
  });
  $("#adminSide").addEventListener("change", (e) => {
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

async function showGate(mode) {
  ME = null;
  const state = await fetch("/api/auth/state").then((r) => r.json()).catch(() => ({}));
  mode ||= state.setup ? "setup" : "login";
  const titles = { setup: ["Welcome to yeerr", "Create the admin account. You can add everyone else after."], login: ["Sign in", state.jellyfin ? "Use your yeerr or Jellyfin account." : ""], signup: ["Create an account", ""] };
  const [title, blurb] = titles[mode];
  const pwHint = mode === "login" ? "current-password" : "new-password"; // the browser's autocomplete hint  pragma: allowlist secret
  $("#gate").innerHTML = `<div class="gate">${aurora}
    <form id="gateForm" autocomplete="on">
      <div class="brand"><img src="logo.svg" alt=""><span>yeerr</span></div>
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

/* ---------- start ---------- */

page("", showDashboard, "home");
page("search", showSearchAll, "home");
page("settings", showSettings, "settings");

async function boot() {
  try {
    ME = await api("/api/me");
  } catch {
    return; // api() showed the sign-in screen
  }
  $("#account").textContent = (ME.name || "?").trim()[0].toUpperCase();
  $("#account").title = ME.name;
  poll();
  route();
}
window.addEventListener("hashchange", () => { if (ME) route(); });
boot();
