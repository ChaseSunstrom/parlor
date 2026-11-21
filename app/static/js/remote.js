/* A phone or a computer and the house's TVs: TVs (#/tvs) to add one by its code, a remote for each (a touchpad, what
   plays with a line to drag, volume, a box that types on the TV or searches from here, who is watching), On TV on films,
   episodes, videos, albums and songs, and the TV sets themselves (power, input, volume, their own player) for the
   accounts an admin gave them to. Every command to a TV showing Parlor goes through tvSend. tv.js is the TV's side;
   tv.py, tvhw.py and cast.py are the server's. */

let tvKnown = [], tvSets = []; // this account's paired TVs; the TV sets that play a film in their own player (cast.py)

/** after sign-in, on anything but a TV: which TVs there are, so On TV shows where it can do something */
async function remoteBoot() {
  [tvKnown, tvSets] = await Promise.all([api("/api/tv").then((r) => r.tvs, () => []), api("/api/tv/hw").then((r) => r.sets.filter((x) => x.plays), () => [])]);
  document.querySelectorAll(".ontv").forEach((b) => { b.hidden = !(tvKnown.length || tvSets.length); });
  tvBar();
}

page("tvs", showTvs, "settings");

async function showTvs(t, [id, all] = []) {
  if (id && /^\d+$/.test(id)) return showRemote(t, +id, all === "all");
  const { tvs } = await api("/api/tv");
  if (t !== ticket) return;
  tvKnown = tvs;
  tvBar();
  paint(t, `<section class="home">${aurora}<h1>TVs</h1><p>Open Parlor on a TV at <b>${esc(location.origin)}/tv</b> (or the Android app on Android TV): it shows a code. Enter it here, and this account's phones and computers can play things on that TV and work it as a remote.</p></section>
    <div class="wrap tvs-page">
      <form class="panel tvadd" id="tvAdd"><h3>Add a TV</h3>
        <div class="actions"><label class="field"><span>The code on the TV</span><input id="tvAddCode" maxlength="20" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="ABCD EFGH" required></label>
        <label class="field grow"><span>Its name</span><input id="tvAddName" maxlength="60" placeholder="Living room" required></label>
        <button class="primary small">Pair</button></div>
        <p class="dim">Only enter a code you see on a TV in front of you: that screen gets this account.</p></form>
      ${tvs.length ? `<ul class="tvlist">${tvs.map((x) => `<li data-tv="${x.id}"><span class="dot${x.online ? " on" : ""}"></span>
        <div><a href="#/tvs/${x.id}"><b>${esc(x.name)}</b></a><small class="dim">${x.online ? (x.state?.title ? `Playing ${esc(x.state.title)}` : "On") : "Off, or not showing Parlor"}</small></div>
        <a class="ghost" href="#/tvs/${x.id}">${ICON.tv}Remote</a><button class="ghost danger" data-tv-drop="${x.id}">Unpair</button></li>`).join("")}</ul>`
        : `<div class="empty-state"><strong>No TVs yet</strong>Pair one above.</div>`}
      <div class="rhw" id="rHw" hidden></div>
    </div>`);
  api("/api/tv/hw").then(({ sets }) => t === ticket && drawHw(0, sets), () => {});  // the sets themselves need no paired screen
  $("#tvAdd").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await post("/api/tv/pair", { code: $("#tvAddCode").value, name: $("#tvAddName").value.trim() });
      toast(`Paired with ${r.name}: it signs in by itself in a moment`);
      setTimeout(route, 2500);
    } catch (err) { toast(err.message); }
  });
}

document.addEventListener("click", (e) => {
  const drop = e.target.closest("[data-tv-drop]");
  if (drop && confirm("Unpair this TV? It signs out, and shows a new code.")) del(`/api/tv/${drop.dataset.tvDrop}${drop.hasAttribute("data-tv-all") ? "?everyone=true" : ""}`).then(route, (err) => toast(err.message));
});

let remote = null;
const chev = (deg) => ICON.chev.replace("<svg", `<svg style="rotate:${deg}deg"`);
const R_MUTE = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z" fill="currentColor"/><path d="m16 9.5 4.5 5m0-5-4.5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
const R_SHAKE = [{ translate: "0" }, { translate: "-6px" }, { translate: "6px" }, { translate: "-3px" }, { translate: "0" }]; // a press that didn't get there
/** a person's round picture: their initial on a colour of their own (from the name, so it is the same on every screen) */
const avatar = (name) => `<span class="avatar" style="--h:${[...String(name)].reduce((n, c) => (n * 31 + c.codePointAt(0)) % 360, 7)}" aria-hidden="true">${esc([...String(name).trim()][0]?.toUpperCase() || "?")}</span>`;
const TV_PLACES = [["#/tvsearch", "Search"], ["#/search", "Search"], ["#/jf", "Library"], ["#/watch", "Watch"], ["#/yt", "Watch"], ["#/live", "Live"], ["#/music", "Music"], ["#/library", "Music"],
  ["#/request", "Request"], ["#/games", "Games"], ["#/who", "Who's watching"], ["#/settings", "Settings"]];
const tvPlace = (hash = "") => TV_PLACES.find(([h]) => hash.startsWith(h))?.[1] || "Home"; // where a TV is, in a word
const tvOwn = (url) => (/^\/(?!\/)/.test(url || "") ? url : ""); // a picture a TV names: only from Parlor itself
/** send only the newest value while an earlier one is still on its way (a slider being dragged, a box being typed in) */
const latest = (send) => {
  let busy = false, next = null;
  const go = async (v) => {
    if (busy) { next = [v]; return; }
    busy = true;
    await send(v);
    busy = false;
    if (next) { const [n] = next; next = null; go(n); }
  };
  return go;
};

/** #/remote (the app's shortcut): the remote of the TV that is on, else of the one used last */
page("remote", async (t) => {
  const { tvs } = await api("/api/tv");
  if (t !== ticket) return;
  const last = pref("tvLast"), x = tvs.find((x) => x.online && x.id === last) || tvs.find((x) => x.online) || tvs.find((x) => x.id === last) || tvs[0];
  location.replace(x ? `#/tvs/${x.id}` : "#/tvs");
}, "settings");

async function showRemote(t, id, everyone) {
  remote?.end();
  setPref("tvLast", id);
  paint(t, `<div class="wrap remote" id="remote" data-tv="${id}">
    <header><span class="dot" id="rDot"></span><div><h1 id="rName">${esc(tvKnown.find((x) => x.id === id)?.name || "TV")}</h1><small class="dim" id="rStatus">Connecting…</small></div>
      <select id="rTvs" aria-label="Another TV" hidden></select><button class="who" id="rWho" hidden></button></header>
    <div class="rnow" id="rNow" aria-live="polite"><img id="rArt" alt="" hidden>
      <div class="rtitle"><small class="dim" id="rWhere"></small><b id="rTitle">Nothing playing</b><span class="dim" id="rBy"></span></div>
      <div class="rline"><span id="rAt">0:00</span><input type="range" id="rSeek" min="0" max="0" value="0" step="1" disabled aria-label="Where it is: drag to jump"><span id="rLen">0:00</span></div>
      <p class="rwarn" id="rWarn" role="alert" hidden></p></div>
    <div class="pad" id="rPad" tabindex="0" role="group" aria-label="Touchpad: swipe for the arrows and rest to keep going, tap for OK, tap an edge for one step that way. Arrow keys and Enter work too.">
      <span class="edge up">${chev(-90)}</span><span class="edge right">${chev(0)}</span><span class="edge down">${chev(90)}</span><span class="edge left">${chev(180)}</span>
      <span class="padok" aria-hidden="true">OK</span><i class="padtouch" aria-hidden="true"></i>
      <span class="vh">${[["up", "Up"], ["left", "Left"], ["enter", "OK"], ["right", "Right"], ["down", "Down"]].map(([k, l]) => `<button tabindex="-1" data-r="key:${k}">${l}</button>`).join("")}</span>
    </div>
    <div class="rrow"><button class="ghost" data-r="key:back">${chev(180)}Back</button><button class="ghost" data-r="key:home">${ICON.home}Home</button></div>
    <div class="rrow">
      <button class="icon-btn" data-r="seek:-10" aria-label="Back 10 seconds">−10</button>
      <button class="icon-btn big" data-r="toggle" id="rToggle" aria-label="Play or pause">${ICON.play}</button>
      <button class="icon-btn" data-r="seek:30" aria-label="Forward 30 seconds">+30</button>
      <button class="icon-btn" data-r="skip:1" aria-label="Next song">${ICON.next}</button>
    </div>
    <div class="rrow rvol"><button class="icon-btn" data-r="mute" id="rMute" aria-label="Mute" aria-pressed="false">${R_MUTE}</button>
      <button class="icon-btn" data-r="volume:-0.1" aria-label="Quieter">−</button>
      <input type="range" id="rVolume" min="0" max="100" value="100" aria-label="Volume">
      <button class="icon-btn" data-r="volume:0.1" aria-label="Louder">+</button><span class="dim" id="rVol">100%</span></div>
    <form class="actions" id="rType"><label class="field grow"><span class="vh">Search, or type on the TV</span><input id="rText" maxlength="200" placeholder="Search, or type on the TV" autocomplete="off" enterkeyhint="send"></label><button class="primary small">Send</button></form>
    <ul class="rfound" id="rFound" hidden></ul>
    <details class="rmore" id="rMore"><summary>More</summary>
      <div id="rLists"></div>
      <label class="field"><span>Sleep timer</span><select id="rSleep"><option value="0">Off</option>${[15, 30, 45, 60, 90, 120].map((m) => `<option value="${m}">In ${m} minutes</option>`).join("")}</select>
        <small id="rSleepAt">The sound fades, what plays pauses and the screen goes dark.</small></label>
      <div class="rrow"><a class="ghost" id="rHere" hidden>Continue on this phone</a><button class="ghost" data-r="stop">Stop</button><a class="ghost" href="#/tvs">All TVs</a></div>
    </details>
    <div class="rhw" id="rHw" hidden></div>
  </div>`);
  const all = everyone ? "?everyone=true" : ""; // an admin's remote from Settings → TVs
  const gone = new AbortController();
  const r = (remote = { id, all, stream: new EventSource(`/api/tv/events${all}`), tv: null, heardAt: Date.now() });
  r.end = () => { r.stream.close(); clearInterval(r.timer); gone.abort(); };
  /** where it is now: what the TV last said, and the seconds since while it plays */
  r.now = () => {
    const s = r.tv?.state || {};
    return Math.min(s.length || Infinity, (s.at || 0) + (s.playing && !s.waiting ? (Date.now() - r.heardAt) / 1000 : 0));
  };
  const tick = () => {
    if (r.seeking) return;
    const seek = $("#rSeek"), length = r.tv?.state?.length || 0, at = r.now();
    seek.max = length;
    seek.disabled = !length;
    seek.value = at;
    $("#rAt").textContent = clock(at);
    $("#rLen").textContent = clock(length);
  };
  r.timer = setInterval(() => (t === ticket && $("#remote") ? tick() : r.end()), 500);
  const draw = (r.draw = () => {
    if (t !== ticket || !$("#remote")) return;
    const x = r.tv, s = x?.state || {}, on = !!x?.online, f = (!s.title && s.focus) || null, at = String(s.page || ""); // nothing playing: what the TV's focus is on
    if (r.heard) $("#rName").textContent = x?.name || "This TV isn't paired any more";
    $("#rDot").className = `dot${on ? " on" : ""}`;
    $("#remote").classList.toggle("off", !on);
    if (s.lag != null) { r.lag = s.lag; r.ack = s.ack; } // the last press the TV answered: its number, and the time from the server to the TV's frame and back
    $("#rStatus").textContent = !navigator.onLine ? "This device is offline" : r.lost ? "Can't reach Parlor: trying again…" : !x ? ""
      : on ? (s.page ? `On${r.lag == null ? "" : r.lag < 400 ? ` · answers in ${r.lag} ms` : ` · slow to answer (${(r.lag / 1000).toFixed(1)} s)`}` : "On, starting")
        : "Off, or not showing Parlor. It reconnects by itself when it's back.";
    $("#rWhere").textContent = !on ? "" : s.title ? (s.waiting ? "Loading…" : s.playing ? "Playing" : "Paused") : `On its screen: ${tvPlace(at)}`;
    $("#rTitle").textContent = s.title || f?.title || "Nothing playing";
    $("#rBy").textContent = s.by || (f && f.kind) || "";
    const art = tvOwn(s.title ? s.image : f?.image), img = $("#rArt");
    if (img.dataset.src !== art) { img.dataset.src = art; img.hidden = !art; if (art) img.src = art; }
    $("#rWarn").hidden = !(s.blocked || s.muted);
    $("#rWarn").textContent = s.blocked ? "The TV's browser is holding this back until a key is pressed on the TV itself. A browser left running as a TV needs --autoplay-policy=no-user-gesture-required."
      : s.muted ? "The sound is muted on the TV's page: press + to turn it on." : "";
    $("#rToggle").innerHTML = s.playing ? ICON.pause : ICON.play;
    $("#rToggle").setAttribute("aria-label", s.playing ? "Pause" : "Play");
    const volume = Math.round((s.volume ?? 1) * 100);
    if (!r.sliding) $("#rVolume").value = volume;
    $("#rVol").textContent = s.muted ? "Muted" : `${volume}%`;
    $("#rMute").setAttribute("aria-pressed", String(!!s.muted));
    const offered = (Array.isArray(s.lists) ? s.lists : []).filter((l) => Array.isArray(l?.all) && l.all.every(Array.isArray));
    const lists = JSON.stringify(offered); // the player's own choices: sound, subtitles, quality
    if (lists !== r.lists && !$("#rLists select:focus")) {
      r.lists = lists;
      $("#rLists").innerHTML = offered.map((l, i) => `<label class="field"><span>${esc(l.name)}</span><select data-opt="${i}">${l.all.map(([v, n]) => `<option value="${esc(v)}"${v === l.value ? " selected" : ""}>${esc(n)}</option>`).join("")}</select></label>`).join("");
    }
    if (!s.sleep && document.activeElement !== $("#rSleep")) $("#rSleep").value = "0";
    $("#rSleepAt").textContent = s.sleep ? `The TV pauses and goes dark at ${new Date(s.sleep * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}.` : "The sound fades, what plays pauses and the screen goes dark.";
    const here = on && s.kind === "video" && /^#\/(jf\/play\/[0-9a-f]{32}|watch\/[\w-]{11})/.exec(at); // a film or a video: this phone can carry on with it
    $("#rHere").hidden = !here;
    if (here) $("#rHere").href = `${here[0]}/${Math.max(0, Math.floor(r.now()) - 3)}`;
    tick();
  });
  /** who the TV is acting as, when its household has more than one person */
  const who = async () => {
    const p = await api(`/api/tv/${id}/people${all}`).catch(() => null);
    if (t !== ticket || !$("#rWho") || !p || p.people.length < 2) return;
    const now = p.people.find((x) => x.id === p.viewer) || p.people.find((x) => x.owner) || p.people[0];
    $("#rWho").hidden = false;
    $("#rWho").innerHTML = `${avatar(now.name)}<span class="vh">Watching as ${esc(now.name)}: change who</span>`;
    $("#rWho").title = `Watching as ${now.name}`;
    $("#rWho").onclick = () => whoAsk(id, p.people, now.id, all);
  };
  r.stream.onmessage = (e) => {
    const m = JSON.parse(e.data);
    Object.assign(r, { lost: false, heard: true });
    if (m.type === "tvs") {
      r.tv = m.tvs.find((x) => x.id === id) || null;
      r.heardAt = Date.now();
      const tvs = $("#rTvs"); // more than one TV: the others are one press away
      tvs.hidden = m.tvs.length < 2;
      tvs.innerHTML = m.tvs.map((x) => `<option value="${x.id}"${x.id === id ? " selected" : ""}>${esc(x.name)}${x.online ? "" : " (off)"}</option>`).join("");
    } else if (m.tv !== id || !r.tv) return;
    else if (m.type === "online") r.tv.online = true;
    else if (m.type === "offline") r.tv.online = false;
    else if (m.type === "state") { r.tv.state = m.state; r.heardAt = Date.now(); } // on or off is the server's word alone: a TV whose stream dropped still says what it shows
    else if (m.type === "viewer") who();
    else if (m.type === "gone") r.tv = null;
    draw();
  };
  r.stream.onerror = () => {
    if (t !== ticket) return;
    r.lost = true;
    draw();
    if (r.stream.readyState === EventSource.CLOSED) setTimeout(() => t === ticket && route(), 5000); // refused: start again
  };
  r.stream.onopen = () => { r.lost = false; draw(); };
  addEventListener("hashchange", r.end, { once: true });
  for (const k of ["online", "offline"]) addEventListener(k, draw, { signal: gone.signal });
  const send = (body) => tvSend(id, body, all);
  touchpad(byId("rPad"), (key) => send({ cmd: "key", key }));
  who();
  api("/api/tv/hw").then(({ sets }) => t === ticket && drawHw(id, sets), () => {});
  $("#rTvs").addEventListener("change", (e) => go(`#/tvs/${e.target.value}${everyone ? "/all" : ""}`));
  // where it is: dragged, the time under the thumb shows; let go, the TV jumps there
  $("#rSeek").addEventListener("input", (e) => { r.seeking = true; $("#rAt").textContent = clock(+e.target.value); });
  $("#rSeek").addEventListener("change", (e) => {
    const s = r.tv?.state;
    if (s) { s.at = +e.target.value; r.heardAt = Date.now(); }
    r.seeking = false;
    send({ cmd: "seek", to: +e.target.value });
  });
  const volume = latest((v) => send({ cmd: "volume", to: v / 100 }));
  $("#rVolume").addEventListener("input", (e) => { r.sliding = true; $("#rVol").textContent = `${e.target.value}%`; volume(+e.target.value); });
  $("#rVolume").addEventListener("change", () => { r.sliding = false; });
  // the text box: the TV's keyboard while the TV has a box of its own open; otherwise a search here, whose answers play there
  const typed = latest((text) => tvSend(id, { cmd: "type", text }, all, true)); // letter by letter: no notice for each
  let finding = 0;
  $("#rText").addEventListener("input", (e) => {
    const q = e.target.value;
    clearTimeout(finding);
    if (r.tv?.state?.input) { $("#rFound").hidden = true; return typed(q); }
    finding = setTimeout(() => remoteFind(t, q.trim()), 250);
  });
  $("#rType").addEventListener("submit", (e) => {
    e.preventDefault();
    clearTimeout(finding);
    send({ cmd: "type", text: $("#rText").value, key: "enter" }).then((ok) => { if (ok) { $("#rText").value = ""; $("#rFound").hidden = true; toast("Typed on the TV"); } });
  });
  $("#rFound").addEventListener("click", (e) => {
    const b = e.target.closest("[data-found]");
    if (!b) return;
    const [kind, item] = b.dataset.found.split(":");
    send({ cmd: "cast", cast: { kind, id: item } }).then((ok) => { if (ok) { $("#rText").value = ""; $("#rFound").hidden = true; toast(kind === "item" ? "Opened on the TV" : "Playing on the TV"); } });
  });
  $("#rMore").addEventListener("change", (e) => {
    if (e.target.dataset.opt) send({ cmd: "set", opt: +e.target.dataset.opt, value: e.target.value });
    else if (e.target.id === "rSleep") send({ cmd: "sleep", by: +e.target.value }).then((ok) => ok && +e.target.value && toast(`The TV sleeps in ${e.target.value} minutes`));
  });
  $("#rHere").addEventListener("click", () => send({ cmd: "pause" })); // the link opens it here, from where the TV was
}

/** the remote's quick search: titles in the library; a film or an episode plays on the TV, a show opens there */
async function remoteFind(t, q) {
  const found = q && libraryOpen() ? await api(`/api/jf/items?search=${encodeURIComponent(q)}&types=Movie,Series,Episode&limit=8`).catch(() => null) : null;
  const box = byId("rFound");
  if (t !== ticket || !box || byId("rText").value.trim() !== q) return; // typed on since
  const items = found?.items || found || [];
  box.hidden = !q || !libraryOpen();
  box.innerHTML = items.length ? items.map((x) => `<li><button type="button" data-found="${x.type === "Series" ? "item" : "jf"}:${esc(x.id)}">${x.image ? `<img src="${esc(x.image)}" alt="" loading="lazy">` : "<i></i>"}
      <span><b>${esc(x.type === "Episode" ? x.series || x.name : x.name)}</b><small class="dim">${esc([TVH_KIND[x.type], x.type === "Episode" ? [epCode(x), x.name].filter(Boolean).join(" · ") : x.year].filter(Boolean).join(" · "))}</small></span>
      ${x.type === "Series" ? chev(0) : ICON.play}</button></li>`).join("")
    : `<li class="dim">Nothing in the library for “${esc(q)}”. Send searches everything, on the TV.</li>`;
}

/** Who's watching, from a phone: the people a TV can act as. Someone with a PIN is asked for it (not for their own, on
    their own phone); the TV starts again as the person picked. */
function whoAsk(tid, people, viewer, all = "") {
  byId("whoAsk")?.remove();
  document.body.insertAdjacentHTML("beforeend", `<dialog class="sheet who" id="whoAsk"><form method="dialog"><h2>Who's watching?</h2>
    <p class="dim">The TV shows their rows, carries on where they left off and keeps to their limits.</p>
    <div class="whos">${people.map((p) => `<button type="button" data-who="${p.id}"${p.id === viewer ? ' aria-current="true"' : ""}>${avatar(p.name)}<b>${esc(p.name)}</b>${p.pin ? `<small class="dim">${ICON.lock}PIN</small>` : ""}</button>`).join("")}</div>
    <label class="field" id="whoPinField" hidden><span id="whoPinFor"></span><input id="whoPin" type="password" inputmode="numeric" pattern="[0-9]*" maxlength="8" autocomplete="off"></label>
    <p class="rwarn" id="whoSaid" role="alert" hidden></p>
    <div class="actions"><button class="ghost" value="cancel">Close</button><button type="button" class="primary small" id="whoGo" hidden>Switch</button></div></form></dialog>`);
  const d = byId("whoAsk");
  let chosen = null;
  const pick = async (pid, pin) => {
    const name = people.find((p) => p.id === pid).name;
    try {
      await post(`/api/tv/${tid}/viewer${all}`, { user_id: pid, ...(pin ? { pin } : {}) });
      d.close();
      toast(`The TV is ${name}'s now`);
    } catch (err) {
      const ask = err.status === 403 && !pin; // they have a PIN: ask for it
      if (ask) { chosen = pid; byId("whoPinField").hidden = byId("whoGo").hidden = false; byId("whoPinFor").textContent = `${name}'s PIN`; byId("whoPin").value = ""; byId("whoPin").focus(); }
      byId("whoSaid").hidden = ask;
      byId("whoSaid").textContent = err.message;
    }
  };
  d.addEventListener("click", (e) => {
    const b = e.target.closest("[data-who]");
    if (b) pick(+b.dataset.who);
    else if (e.target.id === "whoGo" && chosen != null) pick(chosen, byId("whoPin").value);
  });
  byId("whoPin").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); byId("whoGo").click(); } });
  d.addEventListener("close", () => d.remove());
  d.showModal();
}

/** the TV set's own buttons on the remote (tvhw.py): power, input and its volume, for the sets an admin gave this account.
    Which set goes with which paired TV is remembered on this device. */
const HW = { power: "Power", input: "Input", voldown: "Vol −", mute: "Mute", volup: "Vol +", hdmi1: "HDMI 1", hdmi2: "HDMI 2", hdmi3: "HDMI 3", hdmi4: "HDMI 4", parlor: "Open Parlor on it" };
function drawHw(tvId, sets) {
  const box = byId("rHw");
  if (!box || !sets.length) return;
  const set = sets.find((s) => s.id === pref(`hw${tvId}`)) || sets[0];
  const row = (keys) => keys.some((k) => set.keys.includes(k)) ? `<div class="rrow">${keys.filter((k) => set.keys.includes(k)).map((k) => `<button class="ghost" data-hw="${k}">${HW[k]}</button>`).join("")}</div>` : "";
  box.hidden = false;
  box.dataset.set = set.id;
  box.innerHTML = `<div class="rrow"><b>The TV set</b>${sets.length > 1 ? `<select id="rHwSet" aria-label="Which TV set">${sets.map((s) => `<option value="${s.id}"${s === set ? " selected" : ""}>${esc(s.name)}</option>`).join("")}</select>` : `<span class="dim">${esc(set.name)}</span>`}</div>
    ${set.plays ? `<div class="rnow" id="hwNow" hidden aria-live="polite"><div class="rtitle"><b id="hwTitle"></b><span class="dim" id="hwState"></span></div>
      <div class="rline"><span id="hwAt">0:00</span><i id="hwLine" title="Press along the line to jump there"><b id="hwBar"></b></i><span id="hwLen">0:00</span></div>
      <div class="rrow"><button class="icon-btn" data-hwp="seek:-10" aria-label="Back 10 seconds">−10</button>
        <button class="icon-btn big" data-hwp="pause" id="hwToggle" aria-label="Pause">${ICON.pause}</button>
        <button class="icon-btn" data-hwp="seek:30" aria-label="Forward 30 seconds">+30</button><button class="ghost" data-hwp="stop">Stop</button></div></div>
      <p class="dim rnone" id="hwIdle" hidden>To play something on it, open a film or an episode in the <a href="#/jf">Library</a>, or a video in <a href="#/yt">Watch</a>, and press <b>On TV</b>. The buttons to pause and skip show here.</p>` : ""}
    ${row(["power", "input", "voldown", "mute", "volup"])}${row(["hdmi1", "hdmi2", "hdmi3", "hdmi4"])}${row(["parlor"])}`;
  byId("rHwSet")?.addEventListener("change", (e) => { setPref(`hw${tvId}`, +e.target.value); drawHw(tvId, sets); });
  clearInterval(hwTimer);
  if (set.plays) { hwNow(set.id); hwTimer = setInterval(() => hwNow(set.id), 3000); }
}

/** what Parlor is playing in a TV set's own player (cast.py), asked every few seconds while its box is on the page */
let hwTimer = 0, hwLength = 0;
async function hwNow(sid) {
  const box = byId("hwNow");
  if (!box) return clearInterval(hwTimer);
  if (document.hidden || +box.closest("[data-set]").dataset.set !== sid) return;  // a late answer for the set shown before
  const r = await api(`/api/tv/hw/${sid}/playing`).catch(() => null), p = r?.playing;
  if (r?.said) toast(r.said);
  if (!box.isConnected) return;
  const shown = !box.hidden;
  box.hidden = !p;
  byId("hwIdle").hidden = !!p || !r;
  if (!p) return;
  if (!shown) box.scrollIntoView({ block: "center" });  // it sits under the pairing form: the remote is why they're here
  hwLength = p.length;
  byId("hwTitle").textContent = p.title;
  byId("hwState").textContent = { playing: "", paused: "Paused", starting: "Starting…", stopped: "Starting…" }[p.state] ?? "";
  byId("hwAt").textContent = clock(p.at);
  byId("hwLen").textContent = clock(p.length);
  byId("hwBar").style.width = p.length ? `${Math.min(100, (100 * p.at) / p.length)}%` : "0";
  const t = byId("hwToggle"), paused = p.state === "paused";
  t.dataset.hwp = paused ? "play" : "pause";
  t.innerHTML = paused ? ICON.play : ICON.pause;
  t.setAttribute("aria-label", paused ? "Play" : "Pause");
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-hwp]"), line = e.target.closest("#hwLine");
  if (!b && !(line && hwLength)) return;
  const sid = +(b || line).closest("[data-set]").dataset.set, [what, by] = b ? b.dataset.hwp.split(":") : [];
  const r = line?.getBoundingClientRect();
  const body = line ? { do: "seek", to: Math.round(hwLength * Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))) } : by ? { do: what, by: +by } : { do: what };
  post(`/api/tv/hw/${sid}/playing`, body).then(() => hwNow(sid), (err) => toast(err.message));
});
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-hw]");
  if (b) post(`/api/tv/hw/${b.closest("[data-set]").dataset.set}/press`, { key: b.dataset.hw }).then(() => b.dataset.hw === "parlor" // an LG's browser asks LG before it opens anything (a real C6)
    && toast("Opening Parlor. If the TV says to check its network, use On TV → (its own player)"), (err) => toast(err.message));
});

/** the remote's touchpad. A swipe is an arrow: one step after the first 22 px and one more for every 44 px, so a long
    swipe walks along a row; resting at the end of a swipe keeps going, quicker and quicker. A tap is OK; a tap on an
    edge is one step that way, and held there it keeps going. Presses reach the TV in the order they were made, and a
    network that has stopped answering never has a pile of them waiting behind it. `send(key)` answers whether it got there. */
function touchpad(el, send) {
  const dot = el.querySelector(".padtouch");
  let s = null, chain = Promise.resolve(), waiting = 0; // the touch under way; the presses on their way
  const step = (key) => {
    navigator.vibrate?.(key === "enter" ? 18 : 8);
    el.querySelector(key === "enter" ? ".padok" : `.edge.${key}`).animate([{ scale: 1.6, opacity: 1 }, { scale: 1 }], { duration: 240, easing: "ease-out" });
    if (s) s.steps++;
    if (waiting > 3) return chain;
    waiting++;
    return (chain = chain.then(() => send(key)).then((ok) => { waiting--; if (!ok) el.animate(R_SHAKE, { duration: 300 }); }));
  };
  const hold = (dir, wait) => { // the next step waits for the last one to be sent
    const held = s;
    held.timer = setTimeout(async () => {
      if (s !== held || s.dir !== dir) return;
      await step(dir);
      if (s === held && s.dir === dir) hold(dir, Math.max(110, wait * 0.72));
    }, wait);
  };
  const edge = (e) => { // the outer fifth of each side, corners left out
    const b = el.getBoundingClientRect(), x = (e.clientX - b.left) / b.width, y = (e.clientY - b.top) / b.height, mid = (v) => v > 0.2 && v < 0.8;
    return mid(x) ? (y < 0.2 ? "up" : y > 0.8 ? "down" : null) : mid(y) ? (x < 0.2 ? "left" : x > 0.8 ? "right" : null) : null;
  };
  const place = (e) => { const b = el.getBoundingClientRect(); dot.style.transform = `translate3d(${e.clientX - b.left}px,${e.clientY - b.top}px,0)`; };
  el.addEventListener("pointerdown", (e) => {
    if (e.button > 0 || e.target.closest(".vh")) return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    el.focus({ preventScroll: true });
    const dir = edge(e);
    s = { x: e.clientX, y: e.clientY, t: Date.now(), steps: 0, dir, edge: dir, swiped: false, moved: false, timer: 0 };
    el.classList.add("held");
    place(e);
    if (dir) hold(dir, 450);
  });
  el.addEventListener("pointermove", (e) => {
    if (!s) return;
    place(e);
    const dx = e.clientX - s.x, dy = e.clientY - s.y, far = Math.max(Math.abs(dx), Math.abs(dy));
    if (far > 12 && !s.moved) { s.moved = true; if (s.edge) { clearTimeout(s.timer); s.dir = s.edge = null; } } // a swipe that began on an edge isn't a press on it
    if (far < (s.swiped ? 44 : 22)) return;
    const dir = Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up";
    clearTimeout(s.timer);
    Object.assign(s, { x: e.clientX, y: e.clientY, dir, swiped: true });
    step(dir);
    hold(dir, 420);
  });
  const end = () => {
    if (!s) return;
    clearTimeout(s.timer);
    const tap = !s.steps && !s.moved && Date.now() - s.t < 700 ? s.edge || "enter" : null;
    s = null;
    el.classList.remove("held");
    if (tap) step(tap);
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("lostpointercapture", end);
}

/** the one way a phone's command goes to a TV; it says when the TV isn't there (once, not for every press of a run;
    `quiet`: not at all), and answers whether it was sent */
let tvSaidWhy = ["", 0];
function tvSend(id, body, all = "", quiet = false) {
  return post(`/api/tv/${id}/do${all}`, body).then(() => true, (err) => {
    if (!quiet && (err.message !== tvSaidWhy[0] || Date.now() - tvSaidWhy[1] > 4000)) toast(err.message);
    tvSaidWhy = [err.message, Date.now()];
    return false;
  });
}

/** a button of the remote: it acts the moment it is touched (not when the finger lifts), dips so the thumb knows, and
    shakes when the press didn't get to the TV */
function remotePress(b) {
  if (!remote) return;
  const [cmd, arg] = b.dataset.r.split(":"), s = remote.tv?.state;
  navigator.vibrate?.(8);
  b.animate([{ scale: 0.88 }, { scale: 1 }], { duration: 180, easing: "ease-out" });
  if (cmd === "toggle" && s?.kind && remote.tv.online) { s.at = remote.now(); s.playing = !s.playing; remote.heardAt = Date.now(); remote.draw(); } // shown at once; the TV's own word follows
  tvSend(remote.id, cmd === "key" ? { cmd, key: arg } : arg != null ? { cmd, by: +arg } : { cmd }, remote.all).then((ok) => ok || b.animate(R_SHAKE, { duration: 300 }));
}
let remoteDown = null; // the button a pointer is on: its click, when the finger lifts, is not a second press
document.addEventListener("pointerdown", (e) => {
  const b = e.target.closest?.("[data-r]");
  if (!b || e.button > 0) return;
  remoteDown = b;
  remotePress(b);
});
for (const k of ["pointerup", "pointercancel"]) document.addEventListener(k, () => setTimeout(() => { remoteDown = null; }, 0));
document.addEventListener("click", (e) => { // a keyboard's Enter, a screen reader's press
  const b = e.target.closest("[data-r]");
  if (b && remoteDown !== b) remotePress(b);
});
document.addEventListener("keydown", (e) => { // a computer's keyboard works the remote too
  if (!remote || !$("#remote") || e.target.matches("input, textarea, select") || TV_MODE || byId("whoAsk")) return;
  if ((e.key === "Enter" || e.key === " ") && e.target.closest("button, a, summary")) return; // the button itself acts
  const key = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "enter", Escape: "back", Backspace: "back" }[e.key];
  if (key) { e.preventDefault(); tvSend(remote.id, { cmd: "key", key }, remote.all); }
  else if (e.key === " ") { e.preventDefault(); tvSend(remote.id, { cmd: "toggle" }, remote.all); }
});

/* ---------- On TV: a bar on this device's other pages while one of its TVs plays ---------- */

/** one stream while the page is in front and this account has a TV (the remote's page has its own): it keeps tvKnown
    current, and shows what plays with a way to pause it and a way to the remote */
let tvBarFeed = null;
function tvBar() {
  const want = !tvMe && tvKnown.length && !document.hidden && !/^#\/tvs\/\d/.test(location.hash);
  if (!want) {
    tvBarFeed?.close();
    tvBarFeed = null;
    if (byId("tvBar")) byId("tvBar").hidden = true;
    return;
  }
  if (tvBarFeed) return;
  let tvs = [];
  tvBarFeed = new EventSource("/api/tv/events");
  tvBarFeed.onmessage = (e) => {
    const m = JSON.parse(e.data), x = tvs.find((x) => x.id === m.tv);
    if (m.type === "tvs") tvs = m.tvs;
    else if (!x) return;
    else if (m.type === "state") x.state = m.state;
    else if (m.type === "online" || m.type === "offline") x.online = m.type === "online";
    else if (m.type === "gone") tvs = tvs.filter((y) => y !== x);
    else return;
    tvKnown = tvs;
    const on = tvs.find((x) => x.online && x.state?.title);
    if (!byId("tvBar")) document.body.insertAdjacentHTML("beforeend", `<div class="tvbar" id="tvBar" hidden><a>${ICON.tv}<span><b></b><small></small></span></a><button class="icon-btn"></button></div>`);
    const bar = byId("tvBar");
    bar.hidden = !on;
    if (!on) return;
    bar.dataset.tv = on.id;
    $("a", bar).href = `#/tvs/${on.id}`;
    $("b", bar).textContent = on.state.title;
    $("small", bar).textContent = `${on.name} · ${on.state.playing ? "playing" : "paused"}`;
    $("button", bar).innerHTML = on.state.playing ? ICON.pause : ICON.play;
    $("button", bar).setAttribute("aria-label", `${on.state.playing ? "Pause" : "Play"} on ${on.name}`);
  };
  tvBarFeed.onerror = () => { if (tvBarFeed?.readyState === EventSource.CLOSED) tvBarFeed = null; }; // refused: the next page tries again
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("#tvBar button");
  if (b) tvSend(+b.parentElement.dataset.tv, { cmd: "toggle" });
});
document.addEventListener("visibilitychange", tvBar);
addEventListener("hashchange", () => setTimeout(tvBar, 0)); // after the page it led to has drawn

/** Play on TV: straight to the one TV that's on, or a menu of them. A film or a Watch video (`own`: {kind, id, start?})
    can also go to a TV set that plays it in its own player, with no Parlor on its screen: then this device is its
    remote (You → TVs). */
async function tvChoose(anchor, cast, own = null) {
  [tvKnown, tvSets] = await Promise.all([api("/api/tv").then((r) => r.tvs, () => tvKnown),  // afresh: a set added again has a new id
    own ? api("/api/tv/hw").then((r) => r.sets.filter((x) => x.plays), () => tvSets) : tvSets]);
  const on = [...tvKnown.filter((x) => x.online), ...(own ? tvSets.map((x) => ({ ...x, set: true })) : [])];
  if (!on.length) return toast(tvKnown.length ? "None of your TVs is showing Parlor right now" : "Pair a TV first: You → TVs");
  const send = async (x) => {
    if (x.set) {
      toast(`Sending it to ${x.name}…`);
      return post(`/api/tv/hw/${x.id}/play`, own).then((r) => { toast(`Playing ${r.title} on ${x.name}${r.note ? `. ${r.note}` : ""}`); setPref("hw0", x.id); go("#/tvs"); }, (err) => toast(err.message));
    }
    if (await tvSend(x.id, { cmd: "cast", cast: await cast() })) toast(`Playing on ${x.name}`);
  };
  if (on.length === 1) return send(on[0]);
  byId("tvMenu")?.remove();
  const m = document.createElement("div");
  m.className = "popover qmenu";
  m.id = "tvMenu";
  m.setAttribute("role", "menu");
  m.innerHTML = `<strong>Play on</strong>${on.map((x, i) => `<button role="menuitem" data-tv-pick="${i}">${ICON.tv}<span>${esc(x.name)}${x.set ? " (its own player)" : ""}</span></button>`).join("")}`;
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  m.style.top = `${Math.min(r.bottom + 4, innerHeight - m.offsetHeight - 90)}px`;
  m.style.left = `${Math.max(8, Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 8))}px`;
  m.addEventListener("click", (e) => {
    const b = e.target.closest("[data-tv-pick]");
    if (b) { m.remove(); send(on[+b.dataset.tvPick]); }
  });
  setTimeout(() => document.addEventListener("pointerdown", (e) => { if (!m.contains(e.target)) m.remove(); }, { once: true }), 0);
  m.querySelector("button")?.focus();
}

/* Play on TV buttons: films and episodes, YouTube videos (from a player, from where you are, and this one pauses;
   pausing saves a film's place, which a TV showing Parlor resumes from) */
document.addEventListener("click", (e) => {
  const jf = e.target.closest("[data-cast-jf]"), yt = e.target.closest("[data-cast-yt]");
  if (!jf && !yt) return;
  e.preventDefault();
  const v = byId(jf ? "jfVideo" : "video");
  const at = v?.readyState ? v.currentTime : null; // a player still loading isn't at 0 yet: then where it was left
  if (v) v.pause();
  tvChoose(jf || yt, async () => (jf ? { kind: "jf", id: jf.dataset.castJf } : { kind: "yt", id: yt.dataset.castYt, at: at || 0 }),
    { kind: jf ? "jf" : "yt", id: jf ? jf.dataset.castJf : yt.dataset.castYt, ...(at != null ? { start: at } : {}) });  // from where this player is; with none on the page, where it was left
});
// drawn hidden until this account's TVs are known (tvBoot runs alongside the first page)
const tvButton = (attr, id) => (tvMe ? "" : `<button class="ghost ontv" ${attr}="${esc(id)}"${tvKnown.length || tvSets.length ? "" : " hidden"}>${ICON.tv}On TV</button>`);

/** Settings → TVs (admins): every account's TVs, a remote for each, and unpairing; then the TV sets themselves (tvhw.py) */
async function drawTvPanel() {
  const box = $("#tvPanel");
  if (!box) return;
  const [{ tvs }, hw, users] = await Promise.all([api("/api/tv?everyone=true").catch(() => ({ tvs: [] })), api("/api/tv/sets").catch(() => ({ sets: [], kinds: {} })), api("/api/users").catch(() => [])]);
  const members = users.filter((u) => !u.admin);
  const hwRow = (s) => `<li data-hw-set="${s.id}"><span class="dot${s.paired || !s.pairs ? " on" : ""}"></span>
    <div><b>${esc(s.name)}</b><small class="dim">${esc(hw.kinds[s.kind])}${s.host ? ` · ${esc(s.host)}` : ""}${s.pairs ? (s.paired ? " · paired" : " · not paired yet") : ""}</small></div>
    <span class="hwacts"><button class="ghost" data-hw-test>${s.pairs && !s.paired ? "Pair" : "Test"}</button><button class="ghost danger" data-hw-drop>Remove</button></span>
    <div class="hwwho"><span class="dim">Who may use it (admins always can):</span>${members.length ? members.map((u) => `<label class="check"><input type="checkbox" value="${u.id}"${s.users.includes(u.id) ? " checked" : ""}>${esc(u.name)}</label>`).join("") : `<span class="dim">no other accounts yet</span>`}</div></li>`;
  const hwPanel = `<section class="panel" id="hwPanel"><h3>TV sets</h3><p>Power, input and volume on the TV itself, from the remote: a Roku, an LG, a Samsung, an Android TV, or anything on an HDMI-CEC adapter plugged into the server. Parlor only connects to addresses on your home network.</p>
    ${hw.sets.length ? `<ul class="tvlist hwlist">${hw.sets.map(hwRow).join("")}</ul>` : ""}
    <form class="actions" id="hwAdd"><label class="field"><span>Type</span><select id="hwKind">${Object.entries(hw.kinds).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("")}</select></label>
      <label class="field"><span>Its name</span><input id="hwName" maxlength="40" placeholder="Living room TV" required></label>
      <label class="field" id="hwHostField"><span>Its address</span><input id="hwHost" maxlength="253" placeholder="192.168.1.40" autocomplete="off" spellcheck="false"></label>
      <label class="field" id="hwMacField" hidden><span>Its MAC, to switch it on (optional)</span><input id="hwMac" maxlength="17" placeholder="a0:b1:c2:d3:e4:f5" autocomplete="off" spellcheck="false"></label>
      <button class="primary small">Add</button></form>
    <form class="actions" id="hwAddr"><label class="field grow"><span>The address TVs reach Parlor at</span><input id="hwAddrText" maxlength="300" value="${esc(hw.address || "")}" placeholder="http://192.168.1.20:8800" autocomplete="off" spellcheck="false">
      <small>Only needed when you open Parlor by a name or an https address a TV can't use: a TV set fetches films, and opens Parlor, at this one. Empty: the address you're using.</small></label>
      <button class="ghost">Save</button></form></section>`;
  box.innerHTML = `<section class="panel"><h3>TVs</h3><p>Every account's paired TVs. Only from here can an admin work someone else's TV.</p>
    ${tvs.length ? `<ul class="tvlist">${tvs.map((x) => `<li><span class="dot${x.online ? " on" : ""}"></span><div><b>${esc(x.name)}</b><small class="dim">${esc(x.owner)}${x.online ? " · on" : ""}</small></div>
      <a class="ghost" href="#/tvs/${x.id}/all">${ICON.tv}Remote</a><button class="ghost danger" data-tv-drop="${x.id}" data-tv-all>Unpair</button></li>`).join("")}</ul>` : `<p class="dim">No TVs are paired.</p>`}</section>${hwPanel}`;
  const kind = byId("hwKind"), fields = () => { byId("hwHostField").hidden = kind.value === "cec"; byId("hwMacField").hidden = !["webos", "tizen"].includes(kind.value); };
  kind.addEventListener("change", fields);
  fields();
  byId("hwAddr").addEventListener("submit", (e) => {
    e.preventDefault();
    put("/api/tv/address", { address: byId("hwAddrText").value }).then((r) => { byId("hwAddrText").value = r.address; toast(r.address ? "Saved" : "Saved: TVs get the address you're using"); }, (err) => toast(err.message));
  });
  byId("hwAdd").addEventListener("submit", (e) => {
    e.preventDefault();
    const cec = kind.value === "cec", wakes = !byId("hwMacField").hidden;
    post("/api/tv/sets", { kind: kind.value, name: byId("hwName").value.trim(), host: cec ? "" : byId("hwHost").value.trim(), mac: wakes ? byId("hwMac").value.trim() : "" })
      .then(() => { toast("Added: now press Pair or Test"); drawTvPanel(); }, (err) => toast(err.status === 422 ? "Check the name, and the MAC (six pairs like a0:b1:c2:d3:e4:f5)" : err.message));
  });
}
document.addEventListener("click", (e) => {
  const li = e.target.closest("[data-hw-set]"), test = e.target.closest("[data-hw-test]");
  if (!li) return;
  if (test) {
    test.disabled = true;
    if (test.textContent === "Pair") toast("Look at the TV: accept Parlor there");
    post(`/api/tv/sets/${li.dataset.hwSet}/test`).then((r) => toast(r.said), (err) => toast(err.message)).then(drawTvPanel);
  } else if (e.target.closest("[data-hw-drop]") && confirm("Remove this TV set? Its pairing goes with it.")) del(`/api/tv/sets/${li.dataset.hwSet}`).then(drawTvPanel, (err) => toast(err.message));
});
document.addEventListener("change", (e) => {
  const li = e.target.closest(".hwwho")?.closest("[data-hw-set]");
  if (li) put(`/api/tv/sets/${li.dataset.hwSet}/users`, { users: [...li.querySelectorAll(".hwwho input:checked")].map((i) => +i.value) }).then(() => toast("Saved"), (err) => { toast(err.message); drawTvPanel(); });
});
