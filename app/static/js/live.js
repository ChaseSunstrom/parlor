/* Live: YouTube lives and Twitch in Parlor's own player. livePlayer() plays a live playlist from live.py with hls.js
   (or the browser's own HLS): a LIVE badge that turns into "Go live" when you've gone back, a low-latency switch,
   and a hold card while Twitch runs an ad (Parlor takes the ad out, so the picture waits instead). Watch → Live
   lists what's live from your YouTube subscriptions and your Twitch channels; a Twitch channel's page plays it
   with its chat beside it (read-only), its past broadcasts and its clips. */

page("live", showLive, "watch");

/** a live (or, with live false, a past broadcast's) playlist in this video element; returns { destroy } */
function livePlayer(box, video, src, live = true) {
  box.querySelector(".livebar")?.remove();
  box.insertAdjacentHTML("beforeend", `<div class="livebar"><button class="livefull" type="button" title="Full screen" aria-label="Full screen">${ICON.full}</button>${live ? `<button class="livebadge" type="button" title="Back to live">LIVE</button>
    <label class="lowlat" title="Closer to live, more pauses on a weak connection"><input type="checkbox"${pref("lowLatency") ? " checked" : ""}> Low latency</label>` : ""}</div>
    <div class="livehold" role="status" hidden><b>Ad break</b><span>Parlor leaves the ad out. The stream is back in a moment.</span></div>`);
  const badge = box.querySelector(".livebadge"), hold = box.querySelector(".livehold"), s = { hls: null, timer: 0, retry: 0, gone: 0, dead: false };
  video.setAttribute("controlslist", "nofullscreen nodownload"); // full screen is the whole player, so the badge and the ad card come along
  const full = () => (document.fullscreenElement ? document.exitFullscreen() : box.requestFullscreen?.().catch(() => {}));
  video.addEventListener("dblclick", full);
  const edge = () => s.hls?.liveSyncPosition ?? (video.seekable.length ? video.seekable.end(video.seekable.length - 1) : video.currentTime);
  const failed = (text, again) => {
    box.querySelector(".overlay")?.remove();
    box.insertAdjacentHTML("beforeend", `<div class="overlay"><strong>${esc(text)}</strong>${again ? "" : `<p><button class="ghost" data-again>Try again</button> <a class="ghost" href="#/live">Live</a></p>`}</div>`);
    box.querySelector("[data-again]")?.addEventListener("click", () => { box.querySelector(".overlay")?.remove(); start(); });
  };
  async function start() {
    s.hls?.destroy();
    s.hls = null;
    if (!window.Hls) await loadScript("vendor/hls.light.min.js").catch(() => {});
    if (s.dead) return; // left while it loaded
    if (window.Hls?.isSupported()) {
      const low = live && pref("lowLatency");
      s.hls = new Hls({ lowLatencyMode: low, liveSyncDurationCount: low ? 2 : 3, backBufferLength: 90, maxBufferLength: live ? 20 : 40 });
      s.hls.on(Hls.Events.LEVEL_UPDATED, (_e, d) => { s.gone = 0; hold.hidden = !d.details.fragments.slice(-3).some((f) => f.gap); });
      s.hls.on(Hls.Events.ERROR, (_e, d) => {
        if (!d.fatal) return;
        if (d.type === Hls.ErrorTypes.MEDIA_ERROR) return s.hls.recoverMediaError();
        const code = d.response?.code;
        // 404: the stream ended, or Parlor restarted and its addresses changed: a fresh playlist, a few times
        const final = code === 409 || (code === 404 && (!live || ++s.gone > 3));
        failed(code === 409 ? "It isn't live right now" : code === 404 ? "This stream isn't there any more" : "The stream stopped: Parlor tries again", !final);
        if (!final) s.retry = setTimeout(() => { box.querySelector(".overlay")?.remove(); start(); }, 5000);
      });
      s.hls.loadSource(src);
      s.hls.attachMedia(video);
    } else video.src = src; // Safari plays HLS itself
    video.play().catch(() => {});
  }
  box.querySelector(".livefull").addEventListener("click", full);
  if (badge) {
    badge.addEventListener("click", () => { video.currentTime = edge(); video.play().catch(() => {}); });
    box.querySelector(".lowlat input").addEventListener("change", (e) => { setPref("lowLatency", e.target.checked); start(); });
    s.timer = setInterval(() => {
      const behind = edge() - video.currentTime;
      badge.classList.toggle("behind", behind > 12);
      badge.textContent = behind > 12 ? `−${clock(behind)} · Go live` : "LIVE";
    }, 1000);
  }
  start();
  return { destroy() { s.dead = true; clearInterval(s.timer); clearTimeout(s.retry); s.hls?.destroy(); s.hls = null; } };
}

let liveNow = null; // the player and chat of the page showing, ended when it's left
function liveStop() {
  liveNow?.player?.destroy();
  liveNow?.chat?.close();
  liveNow = null;
}
addEventListener("hashchange", () => { if (!location.hash.startsWith("#/live/tw/")) liveStop(); });

const twCard = (c) => `<a class="vcard tcard" href="#/live/tw/${esc(c.login)}">
    <span class="vthumb">${c.live?.preview ? `<img src="${esc(c.live.preview)}" alt="" loading="lazy">` : c.avatar ? `<img class="tavatar" src="${esc(c.avatar)}" alt="" loading="lazy">` : ""}
      ${c.live ? `<span class="len live">LIVE</span><span class="tviewers">${compact.format(c.live.viewers)} watching</span>` : `<span class="len">Offline</span>`}</span>
    <span class="vmeta"><b>${esc(c.live?.title || c.name)}</b><small class="dim">${esc(c.name)}${c.live?.game ? ` · ${esc(c.live.game)}` : ""}</small></span></a>`;

async function showLive(t, [kind, login, sub, id]) {
  liveStop();
  if (kind === "tw" && login) return showTwitch(t, login, sub, id);
  const [now, follows] = await Promise.all([api("/api/live/now"), api("/api/live/tw/follows").catch(() => ({ channels: [] }))]);
  if (t !== ticket) return;
  const lives = [...now.youtube.map((v) => ({ yt: v })), ...now.twitch.map((c) => ({ tw: c }))];
  paint(t, `${subnav()}<section class="home">${aurora}<h1>Live</h1><p>What's live from your YouTube subscriptions and your Twitch channels, in Parlor's player: no ads, and Twitch without an account.</p></section>
    <div class="wrap livepage">
      <h2>Live now</h2>
      ${lives.length ? `<div class="grid">${lives.map((x) => (x.yt ? vcard(x.yt) : twCard(x.tw))).join("")}</div>`
        : `<p class="dim">Nobody you follow is live right now.${now.followed ? "" : " Follow Twitch channels below; YouTube lives from your subscriptions show here too."}</p>`}
      ${now.upcoming.length ? `<h2>Starting soon</h2><div class="grid">${now.upcoming.map((v) => vcard(v)).join("")}</div>` : ""}
      <section class="panel" id="twPanel"><h2>Twitch</h2>
        <form class="actions" id="twFollow"><label class="field grow"><span>Find or follow a channel</span>
          <input id="twName" maxlength="60" autocomplete="off" placeholder="A name, or a twitch.tv link"></label><button class="primary small">Follow</button></form>
        <div id="twResults" class="tlist"></div>
        ${follows.channels.length ? `<h3>Your channels</h3><div class="tlist">${follows.channels.map((c) => `<div class="trow"><a href="#/live/tw/${esc(c.login)}">
            ${c.avatar ? `<img src="${esc(c.avatar)}" alt="" loading="lazy">` : ""}<span><b>${esc(c.name)}</b><small class="dim">${c.live ? `<span class="dot on"></span>Live · ${esc(c.live.game || c.live.title)}` : "Offline"}</small></span></a>
            <button class="ghost small" data-unfollow="${esc(c.login)}">Unfollow</button></div>`).join("")}</div>` : ""}
        <details class="timport"><summary>Follow many at once</summary><form id="twImport"><label class="field"><span>Channel names or links, separated by spaces, commas or lines</span>
          <textarea id="twMany" rows="3" maxlength="20000"></textarea></label><button class="ghost small">Follow them</button></form></details>
      </section>
    </div>`);
  let typing;
  $("#twName").addEventListener("input", (e) => {
    clearTimeout(typing);
    const q = e.target.value.trim();
    if (q.length < 2 || /twitch\.tv/.test(q)) return ($("#twResults").innerHTML = "");
    typing = setTimeout(async () => {
      const { channels } = await api(`/api/live/tw/search?q=${encodeURIComponent(q)}`).catch(() => ({ channels: [] }));
      if (t !== ticket) return;
      $("#twResults").innerHTML = channels.slice(0, 6).map((c) => `<div class="trow"><a href="#/live/tw/${esc(c.login)}">${c.avatar ? `<img src="${esc(c.avatar)}" alt="" loading="lazy">` : ""}
        <span><b>${esc(c.name)}</b><small class="dim">${c.live ? `<span class="dot on"></span>Live · ${compact.format(c.live.viewers)} watching` : "Offline"}</small></span></a>
        <button class="ghost small" data-follow="${esc(c.login)}">Follow</button></div>`).join("");
    }, 300);
  });
  const follow = async (login) => {
    try { await post("/api/live/tw/follows", { login }); toast(`Following ${login}`); route(); } catch (err) { toast(err.message); }
  };
  $("#twFollow").addEventListener("submit", (e) => { e.preventDefault(); follow($("#twName").value); });
  $("#twImport").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await post("/api/live/tw/follows/import", { logins: $("#twMany").value });
      toast(`Following ${plural(r.added.length, "channel")}${r.unknown.length ? `; not on Twitch: ${r.unknown.slice(0, 5).join(", ")}` : ""}`);
      route();
    } catch (err) { toast(err.message); }
  });
  $("#twPanel").addEventListener("click", async (e) => {
    const f = e.target.closest("[data-follow]"), u = e.target.closest("[data-unfollow]");
    if (f) follow(f.dataset.follow);
    if (u) { try { await del(`/api/live/tw/follows/${u.dataset.unfollow}`); route(); } catch (err) { toast(err.message); } }
  });
}

async function showTwitch(t, login, sub, id) {
  const c = await api(`/api/live/tw/channel/${encodeURIComponent(login)}`);
  if (t !== ticket) return;
  const what = sub === "vod" ? c.videos.find((v) => v.id === id) || { id, title: "Past broadcast" } : sub === "clip" ? c.clips.find((x) => x.slug === id) || { slug: id, title: "Clip" } : null;
  const showing = what || (c.live ? { title: c.live.title } : null);
  paint(t, `${subnav()}<div class="watch twatch${showing && !what ? " withchat" : ""}">
    <div class="tplayer">
      ${showing ? `<div class="player-wrap"><div class="player" id="player"><video id="video" playsinline controls poster="${esc(c.live?.preview || what?.thumb || "")}"></video></div></div>`
        : `<div class="player-wrap"><div class="player toff"><div class="overlay">${c.avatar ? `<img class="tbig" src="${esc(c.avatar)}" alt="">` : ""}<strong>${esc(c.name)} is offline</strong><p>Their past broadcasts and clips are below.</p></div></div></div>`}
    </div>
    ${showing && !what ? `<aside class="tchat" aria-label="Chat"><h3>Chat <small class="dim">read-only</small></h3><ol id="chat" aria-live="off"></ol></aside>` : ""}
    <div class="tinfo">
      <div class="vinfo"><h1>${esc(showing?.title || c.name)}</h1>
        <div class="chan"><div class="who"><a href="#/live/tw/${esc(c.login)}">${esc(c.name)}</a><span>${c.live ? `${compact.format(c.live.viewers)} watching${c.live.game ? ` · ${esc(c.live.game)}` : ""}` : "Twitch"}</span></div>
          <button class="${c.followed ? "ghost on" : "primary"}" id="twToggle">${c.followed ? `${ICON.ok}Following` : "Follow"}</button>
          ${what ? `<a class="ghost" href="#/live/tw/${esc(c.login)}">${c.live ? "Back to live" : "Channel"}</a>` : ""}</div>
        ${c.about ? `<p class="dim tabout">${esc(c.about)}</p>` : ""}
        ${c.videos.length ? `<h2>Past broadcasts</h2><div class="grid">${c.videos.map((v) => `<a class="vcard tcard" href="#/live/tw/${esc(c.login)}/vod/${esc(v.id)}">
          <span class="vthumb">${v.thumb ? `<img src="${esc(v.thumb)}" alt="" loading="lazy">` : ""}<span class="len">${clock(v.seconds)}</span></span>
          <span class="vmeta"><b>${esc(v.title)}</b><small class="dim">${compact.format(v.views)} views · ${since(Date.parse(v.at) / 1000)}</small></span></a>`).join("")}</div>` : ""}
        ${c.clips.length ? `<h2>Clips</h2><div class="grid">${c.clips.map((x) => `<a class="vcard tcard" href="#/live/tw/${esc(c.login)}/clip/${esc(x.slug)}">
          <span class="vthumb">${x.thumb ? `<img src="${esc(x.thumb)}" alt="" loading="lazy">` : ""}<span class="len">${clock(x.seconds)}</span></span>
          <span class="vmeta"><b>${esc(x.title)}</b><small class="dim">${compact.format(x.views)} views</small></span></a>`).join("")}</div>` : ""}
      </div>
    </div>
  </div>`);
  $("#twToggle").addEventListener("click", async (e) => { // only the button changes: the stream plays on
    try {
      c.followed ? await del(`/api/live/tw/follows/${c.login}`) : await post("/api/live/tw/follows", { login: c.login });
      c.followed = !c.followed;
      e.currentTarget.className = c.followed ? "ghost on" : "primary";
      e.currentTarget.innerHTML = c.followed ? `${ICON.ok}Following` : "Follow";
    } catch (err) { toast(err.message); }
  });
  if (!showing) return;
  const video = $("#video"), box = $("#player");
  liveNow = {};
  if (sub === "clip") {
    const clip = await api(`/api/live/tw/clip/${encodeURIComponent(id)}`).catch((err) => ({ error: err.message }));
    if (t !== ticket) return;
    if (clip.error) return box.insertAdjacentHTML("beforeend", `<div class="overlay"><strong>${esc(clip.error)}</strong></div>`);
    video.src = clip.src;
    video.play().catch(() => {});
    return;
  }
  liveNow.player = livePlayer(box, video, sub === "vod" ? `/api/live/tw/vod/${encodeURIComponent(id)}.m3u8` : `/api/live/tw/${encodeURIComponent(c.login)}.m3u8`, sub !== "vod");
  if (sub === "vod") return;
  const list = $("#chat");
  const chat = (liveNow.chat = new EventSource(`/api/live/tw/${encodeURIComponent(c.login)}/chat`));
  chat.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (!m.name && !m.error) return;
    const stuck = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.insertAdjacentHTML("beforeend", m.error ? `<li class="dim">${esc(m.error)}</li>`
      : `<li><b style="${/^#[0-9a-f]{6}$/i.test(m.color || "") ? `color:color-mix(in oklab, ${m.color} 55%, var(--text))` : ""}">${esc(m.name)}</b> ${esc(m.text)}</li>`);
    while (list.children.length > 200) list.firstElementChild.remove();
    if (stuck) list.scrollTop = list.scrollHeight;
  };
}
