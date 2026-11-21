/* A phone or a computer and the house's TVs: TVs (#/tvs) to add one by its code, a remote for each, On TV on films,
   episodes, videos, albums and songs, and the TV sets themselves (power, input, volume, their own player) for the
   accounts an admin gave them to. Every command to a TV showing Parlor goes through tvSend. tv.js is the TV's side;
   tv.py, tvhw.py and cast.py are the server's. */

let tvKnown = [], tvSets = []; // this account's paired TVs; the TV sets that play a film in their own player (cast.py)

/** after sign-in, on anything but a TV: which TVs there are, so On TV shows where it can do something */
async function remoteBoot() {
  [tvKnown, tvSets] = await Promise.all([api("/api/tv").then((r) => r.tvs, () => []), api("/api/tv/hw").then((r) => r.sets.filter((x) => x.plays), () => [])]);
  document.querySelectorAll(".ontv").forEach((b) => { b.hidden = !(tvKnown.length || tvSets.length); });
}

page("tvs", showTvs, "settings");

async function showTvs(t, [id, all] = []) {
  if (id && /^\d+$/.test(id)) return showRemote(t, +id, all === "all");
  const { tvs } = await api("/api/tv");
  if (t !== ticket) return;
  tvKnown = tvs;
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
async function showRemote(t, id, everyone) {
  remote?.stream.close();
  paint(t, `<div class="wrap remote" id="remote" data-tv="${id}">
    <header><span class="dot" id="rDot"></span><div><h1 id="rName">TV</h1><small class="dim" id="rStatus">Connecting…</small></div><a class="ghost" href="#/tvs">All TVs</a></header>
    <div class="rnow" id="rNow" aria-live="polite"><div class="rtitle"><b id="rTitle">Nothing playing</b><span class="dim" id="rBy"></span></div>
      <div class="rline"><span id="rAt">0:00</span><i><b id="rBar"></b></i><span id="rLen">0:00</span></div>
      <p class="rwarn" id="rWarn" role="alert" hidden></p></div>
    <div class="stick" id="rStick" tabindex="0" role="group" aria-label="Joystick: drag it for the arrows, hold to keep going, press it for OK. Arrow keys and Enter work too.">
      <span class="notch up">${chev(-90)}</span><span class="notch right">${chev(0)}</span><span class="notch down">${chev(90)}</span><span class="notch left">${chev(180)}</span>
      <span class="knob" id="rKnob" aria-hidden="true"><b>OK</b></span>
      <span class="vh">${[["up", "Up"], ["left", "Left"], ["enter", "OK"], ["right", "Right"], ["down", "Down"]].map(([k, l]) => `<button tabindex="-1" data-r="key:${k}">${l}</button>`).join("")}</span>
    </div>
    <div class="rrow"><button class="ghost" data-r="key:back">Back</button><button class="ghost" data-r="key:home">${ICON.home}Home</button></div>
    <div class="rrow">
      <button class="icon-btn" data-r="seek:-10" aria-label="Back 10 seconds">−10</button>
      <button class="icon-btn big" data-r="toggle" id="rToggle" aria-label="Play or pause">${ICON.play}</button>
      <button class="icon-btn" data-r="seek:30" aria-label="Forward 30 seconds">+30</button>
      <button class="icon-btn" data-r="skip:1" aria-label="Next song">${ICON.next}</button>
    </div>
    <div class="rrow"><button class="ghost" data-r="volume:-0.1" aria-label="Quieter">−</button><span class="dim" id="rVol">Volume</span><button class="ghost" data-r="volume:0.1" aria-label="Louder">+</button></div>
    <div class="rhw" id="rHw" hidden></div>
    <form class="actions" id="rType"><label class="field grow"><span class="vh">Type on the TV</span><input id="rText" maxlength="200" placeholder="Type on the TV (search, a name…)" autocomplete="off"></label><button class="primary small">Send</button></form>
  </div>`);
  const all = everyone ? "?everyone=true" : ""; // an admin's remote from Settings → TVs
  const r = (remote = { id, all, stream: new EventSource(`/api/tv/events${all}`), tv: null });
  const draw = () => {
    if (t !== ticket) return;
    const x = r.tv, s = x?.state || {};
    $("#rName").textContent = x?.name || "This TV isn't paired any more";
    $("#rDot").className = `dot${x?.online ? " on" : ""}`;
    if (s.lag != null) { r.lag = s.lag; r.ack = s.ack; } // the last press the TV answered: its number, and the time from the server to the TV's frame and back
    $("#rStatus").textContent = !x ? "" : x.online ? (s.page ? `On${r.lag == null ? "" : r.lag < 400 ? ` · answers in ${r.lag} ms` : ` · slow to answer (${(r.lag / 1000).toFixed(1)} s)`}` : "On, starting")
      : "Off, or not showing Parlor. It reconnects by itself when it's back.";
    $("#rTitle").textContent = s.title || "Nothing playing";
    $("#rWarn").hidden = !(s.blocked || s.muted);
    $("#rWarn").textContent = s.blocked ? "The TV's browser is holding this back until a key is pressed on the TV itself. A browser left running as a TV needs --autoplay-policy=no-user-gesture-required."
      : s.muted ? "The sound is muted on the TV's page: press + to turn it on." : "";
    $("#rBy").textContent = s.by || "";
    $("#rAt").textContent = clock(s.at || 0);
    $("#rLen").textContent = clock(s.length || 0);
    $("#rBar").style.width = s.length ? `${(100 * s.at) / s.length}%` : "0";
    $("#rToggle").innerHTML = s.playing ? ICON.pause : ICON.play;
    $("#rVol").textContent = `Volume ${Math.round((s.volume ?? 1) * 100)}%`;
  };
  r.stream.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "tvs") r.tv = m.tvs.find((x) => x.id === id) || null;
    else if (m.tv !== id || !r.tv) return;
    else if (m.type === "online") r.tv.online = true;
    else if (m.type === "offline") r.tv.online = false;
    else if (m.type === "state") { r.tv.online = true; r.tv.state = m.state; }
    else if (m.type === "gone") r.tv = null;
    draw();
  };
  r.stream.onerror = () => {
    if (t !== ticket) return;
    $("#rStatus").textContent = "Reconnecting…";
    if (r.stream.readyState === EventSource.CLOSED) setTimeout(() => t === ticket && route(), 5000); // refused: start again
  };
  addEventListener("hashchange", () => r.stream.close(), { once: true });
  joystick(byId("rStick"), (key) => tvSend(id, { cmd: "key", key }, all));
  api("/api/tv/hw").then(({ sets }) => t === ticket && drawHw(id, sets), () => {});
  $("#rType").addEventListener("submit", (e) => {
    e.preventDefault();
    tvSend(id, { cmd: "type", text: $("#rText").value, key: "enter" }, all).then((ok) => { if (ok) { $("#rText").value = ""; toast("Typed on the TV"); } });
  });
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

/** the remote's joystick: drag the knob for an arrow (held, it keeps going, quicker and quicker), press it for OK;
    it springs back to the middle when let go */
function joystick(el, send) {
  const knob = el.querySelector(".knob");
  let s = null; // the drag under way
  const step = (key) => {
    const sent = send(key);
    navigator.vibrate?.(key === "enter" ? 18 : 8);
    if (key !== "enter") { el.classList.remove("step"); void el.offsetWidth; el.classList.add("step"); } // the notch blinks
    return sent;
  };
  const repeat = async (dir, wait) => { // the next step waits for the last one to be sent: a slow network never queues presses up
    const held = s, sent = step(dir);
    await Promise.all([sent, new Promise((ok) => { held.timer = setTimeout(ok, wait); })]);
    if (s === held && s.dir === dir) repeat(dir, Math.max(110, wait * 0.72));
  };
  el.addEventListener("pointerdown", (e) => {
    if (e.button > 0 || e.target.closest(".vh")) return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    el.focus({ preventScroll: true });
    s = { x: e.clientX, y: e.clientY, t: Date.now(), far: 0, dir: null, timer: 0 };
    el.classList.add("held");
  });
  el.addEventListener("pointermove", (e) => {
    if (!s) return;
    let dx = e.clientX - s.x, dy = e.clientY - s.y;
    const max = (el.offsetWidth - knob.offsetWidth) / 2, d = Math.hypot(dx, dy);
    s.far = Math.max(s.far, d);
    if (d > max) { dx *= max / d; dy *= max / d; }
    knob.style.translate = `${dx}px ${dy}px`;
    const dir = d < max * 0.42 ? null : Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : dy > 0 ? "down" : "up";
    if (dir === s.dir) return;
    clearTimeout(s.timer);
    s.dir = dir;
    el.dataset.dir = dir || "";
    if (dir) repeat(dir, 420);
  });
  const end = () => {
    if (!s) return;
    clearTimeout(s.timer);
    if (s.far < 12 && Date.now() - s.t < 700) { // a press, not a drag: OK
      step("enter");
      el.classList.remove("pressed");
      void el.offsetWidth;
      el.classList.add("pressed");
    }
    s = null;
    knob.style.translate = "";
    el.dataset.dir = "";
    el.classList.remove("held");
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
  el.addEventListener("lostpointercapture", end);
}

/** the one way a phone's command goes to a TV; it says when the TV isn't there, and answers whether it was sent */
function tvSend(id, body, all = "") {
  return post(`/api/tv/${id}/do${all}`, body).then(() => true, (err) => { toast(err.message); return false; });
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-r]");
  if (!b || !remote) return;
  const [cmd, arg] = b.dataset.r.split(":");
  tvSend(remote.id, cmd === "key" ? { cmd, key: arg } : arg != null ? { cmd, by: +arg } : { cmd }, remote.all);
});
document.addEventListener("keydown", (e) => { // a computer's keyboard works the remote too
  if (!remote || !$("#remote") || e.target.matches("input, textarea, select") || TV_MODE) return;
  if ((e.key === "Enter" || e.key === " ") && e.target.closest("button, a")) return; // the button itself acts
  const key = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right", Enter: "enter", Escape: "back", Backspace: "back" }[e.key];
  if (key) { e.preventDefault(); tvSend(remote.id, { cmd: "key", key }, remote.all); }
  else if (e.key === " ") { e.preventDefault(); tvSend(remote.id, { cmd: "toggle" }, remote.all); }
});

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
