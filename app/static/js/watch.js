/* Watch: YouTube without the ads. Feeds, search, channels, playlists and a player
   that streams HD video and audio through this server (YouTube serves them apart). */

const ytThumb = (id) => `https://i.ytimg.com/vi/${id}/mqdefault.jpg`;
const cardData = (v) => esc(JSON.stringify({ id: v.id, title: v.title || "", channel: v.channel || "", channel_id: v.channel_id || "", duration: v.duration ?? null, views: v.views ?? null }));
const chanHref = (v) => (v.channel_id ? `#/channel/${esc(v.channel_id)}` : `#/yt/search/${encodeURIComponent(v.channel || "")}`);
let playQueue = null; // {title, items}: what "Up next" plays after this video

/** "today", "tomorrow" or "in 5 days" until a timestamp */
const until = (ts) => { const d = Math.ceil((ts * 1000 - Date.now()) / 864e5); return d <= 0 ? "today" : d === 1 ? "tomorrow" : `in ${d} days`; };
function vcard(v, { forget = false, remove = false } = {}) {
  const href = `#/watch/${esc(v.id)}`;
  const pct = v.position && v.duration ? Math.min(100, (100 * v.position) / v.duration) : 0;
  const len = v.live === "is_live" ? '<span class="len live">LIVE</span>'
    : v.live === "is_upcoming" ? '<span class="len">Upcoming</span>'
    : v.duration ? `<span class="len">${clock(v.duration)}</span>` : "";
  const meta = [views(v.views), since(v.published || v.watched || v.added)].filter(Boolean).join(" · ");
  return `<article class="vcard" data-video="${cardData(v)}">
    <a class="vthumb" href="${href}" tabindex="-1" aria-hidden="true">
      <img src="${esc(v.thumb || ytThumb(v.id))}" alt="" loading="lazy">${len}
      ${pct ? `<span class="seen"><i style="width:${pct.toFixed(1)}%"></i></span>` : ""}
      ${v.downloaded ? `<span class="tag">${ICON.ok}Saved</span>` : ""}
    </a>
    <div class="vact">
      ${remove ? "" : `<button data-vdl title="Download" aria-label="Download ${esc(v.title)}">${ICON.dl}</button>`}
      <button data-later class="${v.later ? "on" : ""}" title="${v.later ? "In Watch later" : "Watch later"}" aria-label="Watch later">${v.later ? ICON.ok : ICON.clock}</button>
      ${forget ? `<button data-forget title="Remove from history" aria-label="Remove from history">${ICON.x}</button>` : ""}
      ${remove ? `<button data-vkeep class="${v.keep ? "on" : ""}" aria-pressed="${!!v.keep}" title="${v.keep ? "Kept: never deleted automatically" : "Keep: never delete it automatically"}" aria-label="Keep">${v.keep ? ICON.pinned : ICON.pin}</button>
        ${offWorks ? `<button data-voffline title="Save on this device, to watch offline" aria-label="Save ${esc(v.title)} on this device">${ICON.offline}</button>` : ""}
        <button data-vprivate title="Make it private: only you, and people you share it with, can open it" aria-label="Make it private">${ICON.lock}</button>
        <button data-vdelete title="Delete the file" aria-label="Delete the file">${ICON.trash}</button>` : ""}
    </div>
    <div class="vmeta">
      <a class="t" href="${href}" title="${esc(v.title)}">${esc(v.title || "Untitled")}</a>
      ${v.channel ? `<a class="c" href="${chanHref(v)}">${esc(v.channel)}</a>` : ""}
      ${meta || v.size ? `<span class="s">${esc([meta, v.size && bytes(v.size), v.quality && (v.quality === "best" ? "best quality" : v.quality + "p")].filter(Boolean).join(" · "))}</span>` : ""}
      ${v.keep ? `<span class="s kept">${ICON.pinned}Kept</span>` : v.expires ? `<span class="s expires">Deletes ${until(v.expires)}</span>` : ""}
    </div>
  </article>`;
}
const ccard = (c) => `<div class="ccard"><a href="#/channel/${esc(c.id)}">${img(c.avatar)}<span class="t">${esc(c.name)}</span>
  <span class="s">${esc([c.handle, c.followers != null && `${compact.format(c.followers)} subscribers`].filter(Boolean).join(" · "))}</span></a>
  ${subButton(c.id, c.name, c.avatar, c.subscribed)}</div>`;
const lcard = (p) => `<article class="vcard lcard"><a class="vthumb" href="#/ytlist/${esc(p.id)}">${p.thumb ? `<img src="${esc(p.thumb)}" alt="" loading="lazy">` : ""}
  <span class="len">${ICON.play}${p.count ? plural(p.count, "video") : "Playlist"}</span></a>
  <div class="vmeta"><a class="t" href="#/ytlist/${esc(p.id)}">${esc(p.title)}</a>${p.channel ? `<span class="c">${esc(p.channel)}</span>` : ""}</div></article>`;
const hideShorts = (items) => (pref("hideShorts") ? items.filter((v) => !v.short) : items);
const vgrid = (items, opts) => (items.length ? `<div class="vgrid">${items.map((v) => vcard(v, opts)).join("")}</div>` : "");

/* ---------- actions on any video card ---------- */

const QUALITY_CHOICES = [["best", "Best", "up to 4K"], ["1080", "1080p", "Full HD"], ["720", "720p", "HD"], ["480", "480p", "small file"]];
function askDownload(button, v) {
  popover(button, "Download", [
    ...QUALITY_CHOICES.map(([q, label, note]) => [label + (q === pref("quality") ? " ✓" : ""), note, () => queueVideo(v, q, "video", button)]),
    ["Audio to your music library", "tagged, with cover art", () => queueVideo(v, "best", "music", button)],
  ]);
}
async function queueVideo(v, quality, kind, button) {
  try {
    await post("/api/yt/downloads", { id: v.id, quality, kind, title: v.title || "", channel: v.channel || "", sponsorblock: kind === "video" && pref("cutSponsors") });
    toast(kind === "music" ? `Saving “${v.title || "the audio"}” to your music library` : `Downloading “${v.title || "the video"}”`, true);
    if (button?.closest(".vact")) { button.innerHTML = ICON.ok; button.classList.add("on"); }
    poll();
  } catch (e) { toast(`Couldn't add it: ${e.message}`); }
}
async function toggleLater(v, button) {
  const on = button.classList.contains("on");
  try {
    on ? await del(`/api/me/later/${v.id}`) : await post("/api/me/later", v);
    button.classList.toggle("on", !on);
    if (button.closest(".vact")) button.innerHTML = on ? ICON.clock : ICON.ok;
    else button.innerHTML = `${on ? ICON.clock : ICON.ok}<span>${on ? "Watch later" : "Saved for later"}</span>`;
    toast(on ? "Removed from Watch later" : "Saved to Watch later");
  } catch (e) { toast(e.message); }
}
async function toggleSub(button) {
  const { id, name, avatar } = button.dataset;
  const on = button.classList.contains("on");
  try {
    on ? await del(`/api/me/subs/${id}`) : await post("/api/me/subs", { channel_id: id, name, avatar: avatar || null });
    button.classList.toggle("on", !on);
    button.textContent = on ? "Subscribe" : "Subscribed";
    toast(on ? `Unsubscribed from ${name}` : `Subscribed to ${name}. New videos show up in Subscriptions.`);
  } catch (e) { toast(e.message); }
}
const subButton = (id, name, avatar, on) => (id ? `<button class="sub-btn${on ? " on" : ""}" data-sub data-id="${esc(id)}" data-name="${esc(name)}" data-avatar="${esc(avatar || "")}">${on ? "Subscribed" : "Subscribe"}</button>` : "");

document.addEventListener("click", (e) => {
  const card = e.target.closest("[data-video]");
  const v = card && JSON.parse(card.dataset.video);
  const dl = e.target.closest("[data-vdl]");
  if (dl && v) { e.preventDefault(); askDownload(dl, v); }
  const later = e.target.closest("[data-later]");
  if (later && v) { e.preventDefault(); toggleLater(v, later); }
  const sub = e.target.closest("[data-sub]");
  if (sub) toggleSub(sub);
  const forget = e.target.closest("[data-forget]");
  if (forget && v) del(`/api/me/history/${v.id}`).then(() => { forgetLast("homeContinue"); card.remove(); }, (err) => toast(err.message));
  const keep = e.target.closest("[data-vkeep]");
  if (keep && v) {
    keep.disabled = true;
    post(`/api/yt/library/${v.id}/keep`, { keep: !keep.classList.contains("on") })
      .then((r) => { toast(r.keep ? "Kept: it won't be deleted automatically" : "No longer kept"); keepScroll = true; route(); }, (err) => { keep.disabled = false; toast(err.message); });
  }
  const lock = e.target.closest("[data-vprivate]");
  if (lock && v && confirm(`Make “${v.title}” private? It's encrypted with your key and leaves the shared video folder; only you, and people you share it with, can open it.`)) {
    lock.disabled = true;
    post(`/api/yt/library/${v.id}/private`).then(() => { toast("Private now: find it under Private, above"); keepScroll = true; route(); },
      (err) => { lock.disabled = false; toast(err.message); if (/Settings|password/.test(err.message)) go("#/settings"); });
  }
  const rm = e.target.closest("[data-vdelete]");
  if (rm && v && confirm(`Delete “${v.title}” from your video folder?`)) {
    del(`/api/yt/library/${v.id}`).then(() => { card.remove(); toast("Deleted"); }, (err) => toast(err.message));
  }
  const fromList = e.target.closest("[data-queue] .vcard a");
  if (fromList) {
    const box = fromList.closest("[data-queue]");
    playQueue = { title: box.dataset.queue, items: $$(".vcard", box).map((c) => JSON.parse(c.dataset.video)) };
  }
});

/* ---------- the Watch section's pages ---------- */

function watchHead(active, title, blurb) {
  const feeds = ["home", "subs", "rec"].includes(active) || active.startsWith("account");
  const account = ME?.youtube_cookies && feeds
    ? `<a href="#/yt/account/subscriptions"${active === "account-subscriptions" ? ' aria-current="page"' : ""}>Your YouTube feed</a>
       <a href="#/yt/account/later"${active === "account-later" ? ' aria-current="page"' : ""}>YouTube watch later</a>` : "";
  return `<section class="home">${aurora}
    <h1>${esc(title)}</h1>${blurb ? `<p>${blurb}</p>` : ""}
    ${subnav()}${account ? `<div class="chips page-actions">${account}</div>` : ""}
  </section>`;
}

async function showYt(t, [tab = "home", ...rest]) {
  if (tab === "search") return showYtSearch(t, rest.join("/"));
  const pages = { home: ytHome, subs: ytSubs, rec: ytRec, continue: ytContinue, later: ytLater, history: ytHistory, downloads: ytDownloads, channels: ytChannels, account: ytAccount };
  return (pages[tab] || ytHome)(t, rest);
}

/* Home: something to watch without pasting a link. Shelves fill in as they arrive;
   topic shelves load as they scroll into view. */
const vstrip = (items) => (items.length ? `<div class="vstrip">${items.map((v) => vcard(v)).join("")}</div>` : "");
async function ytHome(t) {
  paint(t, watchHead("home", "For you", "Picked from what you watch, the channels you follow and the topics you like.") + `<div class="wrap">
    ${slot("wContinue", "", "")}${slot("wBecause0", "", skeletons(4, "v", "vstrip"))}${slot("wSubs", "", "")}${slot("wBecause1", "", "")}${slot("wRec", "", "")}
    <div id="wTopics"></div></div>`);
  api("/api/me/history?unfinished=true&limit=16").then((r) => fill(t, "wContinue", shelf("Continue watching", vstrip(r.items), "#/yt/continue")), () => fill(t, "wContinue", ""));
  api("/api/yt/feed?limit=24").then((r) => fill(t, "wSubs", shelf("New from your subscriptions", vstrip(hideShorts(r.items).slice(0, 16)), "#/yt/subs")), () => fill(t, "wSubs", ""));
  api("/api/yt/recommended").then((r) => fill(t, "wRec", shelf("Recommended", vstrip(hideShorts(r.items).slice(0, 16)), "#/yt/rec")), () => fill(t, "wRec", ""));
  api("/api/me/history?limit=60").then((h) => {
    const seeds = [], channels = new Set(); // your latest videos, one per channel
    for (const v of h.items) if (seeds.length < 2 && !channels.has(v.channel_id || v.id)) { channels.add(v.channel_id || v.id); seeds.push(v); }
    [0, 1].forEach((n) => (seeds[n]
      ? api(`/api/yt/related/${seeds[n].id}`).then((r) => {
        const title = seeds[n].title || "a video";
        fill(t, `wBecause${n}`, shelf(`Because you watched “${title.length > 50 ? title.slice(0, 48).trim() + "…" : title}”`, vstrip(hideShorts(r.items).slice(0, 16))));
      }, () => fill(t, `wBecause${n}`, ""))
      : fill(t, `wBecause${n}`, "")));
  }, () => fill(t, "wBecause0", ""));
  const { topics, suggested } = await api("/api/me/topics");
  if (t === ticket) drawTopics(t, topics, suggested);
}

function drawTopics(t, topics, suggested) {
  const box = $("#wTopics");
  if (!box) return;
  box.innerHTML = `<div class="shelf-head"><h2>Topics</h2><button class="ghost" data-topics-edit>Edit topics</button></div>
    <p class="dim topics-blurb">This week's most-watched videos about each one.</p>
    ${topics.map((q, i) => `<div class="topic" id="wTopic${i}" data-topic="${esc(q)}"><div class="shelf-head"><h3>${esc(q)}</h3>
      <a class="more" href="#/yt/search/${encodeURIComponent(q)}">Search</a></div>${skeletons(4, "v", "vstrip")}</div>`).join("") ||
      `<div class="empty-state" style="padding-top:0">No topics. Add some to fill this page.</div>`}`;
  box.dataset.topics = JSON.stringify({ topics, suggested });
  const seen = new IntersectionObserver((hits) => hits.forEach((hit) => {
    if (!hit.isIntersecting) return;
    seen.unobserve(hit.target);
    const el = hit.target, q = el.dataset.topic, head = el.firstElementChild.outerHTML;
    api(`/api/yt/topic?q=${encodeURIComponent(q)}`).then(
      (r) => { if (t === ticket) el.innerHTML = head + (vstrip(hideShorts(r.items).slice(0, 18)) || `<p class="dim">Nothing this week.</p>`); },
      (err) => { if (t === ticket) el.innerHTML = head + `<p class="dim">${esc(err.message)}</p>`; });
  }), { rootMargin: "600px 0px" });
  $$(".topic", box).forEach((el) => seen.observe(el));
}

function topicEditor(box, topics, suggested) {
  const ideas = suggested.filter((s) => !topics.some((x) => x.toLowerCase() === s.toLowerCase()));
  box.innerHTML = `<section class="panel topic-editor">
    <div class="head"><div><h3>Your topics</h3><p>Each topic is a shelf of this week's most-watched videos about it. Only you see yours.</p></div></div>
    <div class="chips">${topics.map((x) => `<button class="tchip" data-topic-rm="${esc(x)}" aria-label="Remove ${esc(x)}">${esc(x)}${ICON.x}</button>`).join("") || `<span class="dim">None yet</span>`}</div>
    ${topics.length < 20 ? `<form class="actions" data-topic-form><label class="field grow"><span class="vh">New topic</span><input class="input" name="topic" maxlength="60" placeholder="Add a topic, like woodworking or chess" autocomplete="off"></label><button class="ghost">${ICON.plus}Add</button></form>`
      : `<p class="dim">That's 20, the most there can be. Remove one to add another.</p>`}
    ${ideas.length && topics.length < 20 ? `<div class="chips">${ideas.map((x) => `<button data-topic-idea="${esc(x)}">${ICON.plus}${esc(x)}</button>`).join("")}</div>` : ""}
    <div class="actions"><button class="primary" data-topics-save>Save</button><button class="ghost" data-topics-cancel>Cancel</button></div>
  </section>`;
  box.dataset.editing = JSON.stringify(topics);
  ($("input", box) || $("[data-topics-save]", box)).focus();
}
const editing = (box) => JSON.parse(box.dataset.editing || "[]");
document.addEventListener("click", (e) => {
  const box = e.target.closest("#wTopics");
  if (!box) return;
  const { topics, suggested } = JSON.parse(box.dataset.topics || '{"topics":[],"suggested":[]}');
  const rm = e.target.closest("[data-topic-rm]"), idea = e.target.closest("[data-topic-idea]");
  if (e.target.closest("[data-topics-edit]")) topicEditor(box, topics, suggested);
  else if (rm) topicEditor(box, editing(box).filter((x) => x !== rm.dataset.topicRm), suggested);
  else if (idea) topicEditor(box, [...editing(box), idea.dataset.topicIdea], suggested);
  else if (e.target.closest("[data-topics-cancel]")) drawTopics(ticket, topics, suggested);
  else if (e.target.closest("[data-topics-save]")) {
    const typed = $("[data-topic-form] input", box)?.value.trim(); // typed but not added: keep it too
    put("/api/me/topics", { topics: typed ? [...editing(box), typed] : editing(box) }).then((r) => {
      drawTopics(ticket, r.topics, suggested);
      $("[data-topics-edit]", box)?.focus();
      toast("Topics saved");
    }, (err) => toast(err.message));
  }
});
document.addEventListener("submit", (e) => {
  const form = e.target.closest("[data-topic-form]");
  if (!form) return;
  e.preventDefault();
  const box = form.closest("#wTopics"), name = form.topic.value.trim();
  if (!name) return;
  const { suggested } = JSON.parse(box.dataset.topics);
  const now = editing(box);
  topicEditor(box, now.some((x) => x.toLowerCase() === name.toLowerCase()) ? now : [...now, name], suggested);
});

async function ytSubs(t) {
  paint(t, watchHead("subs", "Subscriptions", "The newest videos from the channels you follow, without ads.") + `<div class="wrap">${skeletons(12)}</div>`);
  const r = await api("/api/yt/feed");
  const items = hideShorts(r.items);
  const failed = r.failed.length ? `<p class="dim">Couldn't read ${esc(r.failed.slice(0, 3).join(", "))}${r.failed.length > 3 ? ` and ${r.failed.length - 3} more` : ""} just now.</p>` : "";
  paint(t, watchHead("subs", "Subscriptions", `The newest videos from ${plural(r.channels, "channel")} you follow, without ads.`) + `<div class="wrap">${failed}${
    r.channels ? vgrid(items) || `<div class="empty-state"><strong>Nothing new</strong>Your channels haven't posted lately.</div>`
    : `<div class="empty-state"><strong>You don't follow any channels yet</strong>Search for a channel and press Subscribe, or bring your subscriptions over from YouTube (Google Takeout), NewPipe, FreeTube or Invidious.
        <div class="actions"><a class="primary" href="#/yt/channels">Import subscriptions</a></div></div>`}</div>`);
}

async function ytRec(t) {
  paint(t, watchHead("rec", "Recommended", "") + `<div class="wrap">${skeletons(12)}</div>`);
  const r = await api("/api/yt/recommended");
  const blurb = { youtube: "YouTube's picks for your account.", local: "Videos like the ones you've watched, and new ones from the channels you watch most.", none: "" }[r.source];
  paint(t, watchHead("rec", "Recommended", blurb) + `<div class="wrap">${vgrid(hideShorts(r.items)) ||
    `<div class="empty-state"><strong>Nothing to recommend yet</strong>Watch a few videos or subscribe to some channels and this fills up. For YouTube's own recommendations, add your YouTube cookies in <a href="#/settings">Settings</a>.</div>`}</div>`);
}

async function ytContinue(t) {
  const r = await api("/api/me/history?unfinished=true&limit=60");
  paint(t, watchHead("continue", "Continue watching", "Videos you started, right where you left off.") + `<div class="wrap">${vgrid(r.items, { forget: true }) || `<div class="empty-state"><strong>Nothing half-watched</strong>Videos you stop partway through show up here.</div>`}</div>`);
}

async function ytLater(t) {
  const r = await api("/api/me/later");
  paint(t, watchHead("later", "Watch later", r.items.length ? `${plural(r.items.length, "video")} saved.` : "") +
    `<div class="wrap" data-queue="Watch later">${vgrid(r.items) || `<div class="empty-state"><strong>Nothing saved</strong>Use the clock button on any video to keep it for later.</div>`}</div>`);
}

async function ytHistory(t) {
  const r = await api("/api/me/history?limit=300");
  paint(t, watchHead("history", "History", r.items.length ? "Everything you've watched here, newest first. Only you can see it." : "",
    r.items.length ? `<span class="sep"></span><button data-clear-history>Clear history</button>` : "") +
    `<div class="wrap">${vgrid(r.items, { forget: true }) || `<div class="empty-state"><strong>No history</strong>Videos you watch show up here.</div>`}</div>`);
}
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-clear-history]") && confirm("Clear your whole watch history? Resume points go too.")) {
    del("/api/me/history").then(route, (err) => toast(err.message));
  }
});

const ruleText = (rule) => (!rule.days ? "Downloads are kept until someone deletes them."
  : rule.watched_only ? `Each video is deleted ${plural(rule.days, "day")} after someone finishes watching it. Press Keep on the ones to hold on to.`
  : `Each video is deleted ${plural(rule.days, "day")} after it's downloaded. Press Keep on the ones to hold on to.`);
async function ytDownloads(t) {
  const [r, rule] = await Promise.all([api("/api/yt/library"), api("/api/yt/retention").catch(() => ({ days: 0 }))]);
  const disk = r.disk ? ` ${bytes(r.used)} used, ${bytes(r.disk.free)} free on the disk.` : "";
  paint(t, watchHead("downloads", "Downloaded", r.items.length ? `${plural(r.items.length, "video")} in your video folder.${disk} ${ruleText(rule)}` : "") +
    `<div class="wrap"><div id="privateShelf"></div></div>
    <div class="wrap" data-queue="Downloaded">${r.items.length ? `<div class="vgrid">${r.items.map((v) => vcard(v, { remove: v.mine || ME?.admin })).join("")}</div>`
      : `<div class="empty-state"><strong>No downloads yet</strong>Use the download button on any video. It's saved with chapters, subtitles and the thumbnail.</div>`}</div>`);
  drawPrivate(t);
}

/* ---------- private items: encrypted with your key, for you and whoever you share them with ---------- */

function privateCard(x) {
  return `<article class="vcard private">
    <a class="vthumb" href="#/private/${esc(x.id)}" tabindex="-1" aria-hidden="true"><div class="locked">${ICON.lock}</div>${x.duration ? `<span class="len">${clock(x.duration)}</span>` : ""}</a>
    <div class="vmeta"><a class="t" href="#/private/${esc(x.id)}">${esc(x.title)}</a>
      <span class="c">${esc(x.channel || "")}</span>
      <span class="s">${esc([bytes(x.size), x.mine ? (x.shared_with.length ? `shared with ${x.shared_with.join(", ")}` : "only you") : "shared with you"].join(" · "))}</span></div>
  </article>`;
}

async function drawPrivate(t) {
  const box = $("#privateShelf");
  let r;
  try { r = await api("/api/private"); } catch (err) {
    if (t === ticket && box && /password/.test(err.message)) box.innerHTML = privateUnlock("Your private videos are locked. Enter your password to open them.");
    return;
  }
  if (t !== ticket || !box || !r.items.length) return;
  box.innerHTML = shelf("Private", `<div class="vgrid">${r.items.map(privateCard).join("")}</div>`);
}

const privateUnlock = (why) => `<form class="actions private-unlock" data-private-unlock><span>${ICON.lock} ${esc(why)}</span>
  <label class="field grow"><span class="vh">Your password</span><input type="password" autocomplete="current-password" placeholder="Your password" required></label>
  <button class="primary small">Open</button></form>`;

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("[data-private-unlock]");
  if (!form) return;
  e.preventDefault();
  try {
    await post("/api/private/unlock", { password: form.querySelector("input").value });
    keepScroll = true; route();
  } catch (err) { toast(err.message); if (/recovery/.test(err.message)) go("#/settings"); }
});

async function showPrivate(t, [id]) {
  let r;
  try { r = await api("/api/private"); } catch (err) {
    return paint(t, `<div class="wrap">${privateUnlock(err.message)}</div>`);
  }
  const x = r.items.find((i) => i.id === id);
  if (!x) return paint(t, `<div class="note"><strong>Not here</strong>It isn't shared with you, or it's gone.</div>`);
  paint(t, `<div class="wrap private-page">
      <video controls playsinline preload="metadata" src="/api/private/${esc(x.id)}/file"></video>
      <h1>${ICON.lock} ${esc(x.title)}</h1><p class="dim">${esc(x.channel || "")} · ${bytes(x.size)} · encrypted; only the people below can open it</p>
      ${x.mine ? `<section class="panel"><h3>Who has it</h3>
        <ul class="shares">${x.shared_with.map((n) => `<li>${esc(n)} <button class="icon-btn" data-unshare="${esc(n)}" aria-label="Take ${esc(n)} off it">${ICON.x}</button></li>`).join("") || `<li class="dim">Only you</li>`}</ul>
        <form class="actions" id="shareForm"><label class="field grow"><span class="vh">Their name in Parlor</span><input id="shareName" placeholder="Their name in Parlor" required></label>
          <button class="primary small">Share</button></form>
        <p class="dim">They need private items turned on (Settings). Taking someone off re-encrypts the file with a new key, so a large video takes a moment.</p>
        <div class="actions"><button class="ghost" id="unprivate">Back to the shared library</button></div></section>` : ""}
    </div>`);
  $("#shareForm")?.addEventListener("submit", (e) => {
    e.preventDefault();
    post(`/api/private/${x.id}/share`, { name: $("#shareName").value }).then(() => { toast("Shared"); route(); }, (err) => toast(err.message));
  });
  $$("[data-unshare]").forEach((b) => b.addEventListener("click", () => {
    b.disabled = true;
    del(`/api/private/${x.id}/share/${encodeURIComponent(b.dataset.unshare)}`).then(() => { toast("Taken off, and re-encrypted"); route(); }, (err) => { b.disabled = false; toast(err.message); });
  }));
  $("#unprivate")?.addEventListener("click", () => confirm("Put it back in the shared video folder, unencrypted?") &&
    post(`/api/private/${x.id}/unprivate`).then(() => { toast("Back in the library"); go("#/yt/downloads"); }, (err) => toast(err.message)));
}
page("private", showPrivate, "watch");

async function ytChannels(t) {
  const subs = await api("/api/me/subs");
  const row = (s) => `<li>${img(s.avatar)}<div><a class="name" href="#/channel/${esc(s.channel_id)}">${esc(s.name || s.channel_id)}</a>
      <span class="sub">Since ${new Date(s.added * 1000).toLocaleDateString()}</span></div>
    <div class="acts">${subButton(s.channel_id, s.name, s.avatar, true)}</div></li>`;
  paint(t, watchHead("channels", "Channels", subs.length ? `You follow ${plural(subs.length, "channel")}.` : "") + `<div class="wrap">
    <section class="panel">
      <div class="head"><div><h3>Import subscriptions</h3>
        <p>Paste or open an export: Google Takeout's <code>subscriptions.csv</code> (YouTube and YouTube Music → subscriptions), NewPipe or FreeTube <code>.json</code>, an Invidious or FreeTube <code>.opml</code>, or just channel links, one per line.</p></div>
        ${subs.length ? `<a class="ghost" href="/api/me/subs/export" download>Export as OPML</a>` : ""}</div>
      <label class="field"><span class="vh">Subscriptions to import</span><textarea id="subsText" spellcheck="false" placeholder="https://www.youtube.com/@channel"></textarea></label>
      <div class="actions"><button class="primary" data-import-subs>Import</button>
        <label class="ghost" style="cursor:pointer">Open a file<input type="file" id="subsFile" accept=".csv,.json,.opml,.xml,.txt" hidden></label></div>
    </section>
    ${subs.length ? `<ul class="lists">${subs.map(row).join("")}</ul>` : ""}
  </div>`);
  $("#subsFile")?.addEventListener("change", async (e) => { const f = e.target.files[0]; if (f) $("#subsText").value = await f.text(); });
}
document.addEventListener("click", (e) => {
  const imp = e.target.closest("[data-import-subs]");
  if (!imp) return;
  imp.disabled = true;
  imp.textContent = "Importing…";
  post("/api/me/subs/import", { text: $("#subsText").value })
    .then((r) => { toast(`Found ${plural(r.found, "channel")}` + (r.unresolved.length ? `; couldn't find ${r.unresolved.slice(0, 3).join(", ")}` : "")); route(); },
      (err) => { toast(err.message); imp.disabled = false; imp.textContent = "Import"; });
});

async function ytAccount(t, [kind = "subscriptions"]) {
  const title = { subscriptions: "Your YouTube feed", later: "Your YouTube watch later", recommended: "YouTube recommends", history: "Your YouTube history" }[kind] || "YouTube";
  paint(t, watchHead(`account-${kind}`, title, "Straight from your YouTube account.") + `<div class="wrap">${skeletons(12)}</div>`);
  const r = await api(`/api/yt/account/${encodeURIComponent(kind)}`);
  paint(t, watchHead(`account-${kind}`, title, "Straight from your YouTube account.") + `<div class="wrap" data-queue="${esc(title)}">${vgrid(hideShorts(r.items)) || `<div class="empty-state"><strong>Empty</strong></div>`}</div>`);
}

async function showYtSearch(t, q) {
  input.value = q;
  paint(t, `<div class="wrap" style="padding-top:28px">${skeletons(12)}</div>`);
  const r = await api(`/api/yt/search?q=${encodeURIComponent(q)}`);
  if (r.link) return openYtLink(r.link);
  const kinds = (k) => r.items.filter((i) => i.type === k);
  const vids = hideShorts(kinds("video")), chans = kinds("channel"), lists = kinds("playlist");
  const html = (chans.length ? `<div class="strip" style="grid-auto-columns:minmax(150px,170px);margin-top:20px">${chans.map(ccard).join("")}</div>` : "") +
    shelf("Videos", vgrid(vids)) + shelf("Playlists", lists.length ? `<div class="vgrid">${lists.map(lcard).join("")}</div>` : "");
  paint(t, html ? `<div class="wrap">${html}</div>` : `<div class="note"><strong>Nothing matches “${esc(q)}”</strong>Try other words, or paste a YouTube link.</div>`);
}
function openYtLink(link) {
  const to = { video: `#/watch/${link.id}${link.t ? `/${link.t}` : ""}`, playlist: `#/ytlist/${link.id}`, channel: `#/channel/${link.id}` }[link.type];
  history.replaceState(null, "", to);
  route();
}

/* ---------- channel and playlist ---------- */

async function showChannel(t, [id, tab = "videos"]) {
  const c = await api(`/api/yt/channel/${encodeURIComponent(id)}?tab=${encodeURIComponent(tab)}`);
  const tabs = [["videos", "Videos"], ["streams", "Live"], ["shorts", "Shorts"], ["playlists", "Playlists"]];
  const items = tab === "playlists" ? c.items.filter((i) => i.type === "playlist") : c.items.filter((i) => i.type === "video");
  const body = tab === "playlists"
    ? (items.length ? `<div class="vgrid">${items.map(lcard).join("")}</div>` : "")
    : vgrid(items);
  paint(t, `<header class="chanhead"><div class="banner" style="${c.banner ? esc(`--photo:${cssUrl(c.banner)}`) : ""}"></div>
      <div class="row2">${c.avatar ? `<img class="face" src="${esc(c.avatar)}" alt="">` : '<div class="face"></div>'}
        <div class="grow"><h1>${esc(c.name)}</h1>
          <div class="facts">${esc([c.handle, c.followers != null && `${compact.format(c.followers)} subscribers`].filter(Boolean).join(" · "))}</div></div>
        ${subButton(c.id, c.name, c.avatar, c.subscribed)}</div>
      ${c.description ? `<p class="about">${esc(c.description)}</p>` : ""}
    </header>
    <div class="wrap"><nav class="chips">${tabs.map(([k, label]) => `<a href="#/channel/${esc(id)}/${k}"${k === tab ? ' aria-current="page"' : ""}>${label}</a>`).join("")}</nav>
      <div data-queue="${esc(c.name)}" style="margin-top:20px">${body || `<div class="empty-state"><strong>Nothing here</strong></div>`}</div></div>`);
}

async function showYtList(t, [id]) {
  const p = await api(`/api/yt/playlist/${encodeURIComponent(id)}`);
  const total = p.items.reduce((n, v) => n + (v.duration || 0), 0);
  const first = p.items[0];
  paint(t, hero({
    kind: "Playlist", title: p.title || "Playlist", image: first && ytThumb(first.id), tintColor: "#4a2630",
    meta: `${p.channel ? `<a href="${chanHref(p)}">${esc(p.channel)}</a>` : ""}<span>${plural(p.items.length, "video")}</span>${total ? `<span>${clock(total)}</span>` : ""}`,
    blurb: p.description?.slice(0, 280),
    action: (first ? `<a class="primary" href="#/watch/${esc(first.id)}" data-play-all>${ICON.play}<span>Play all</span></a>` : "") +
      (p.items.length ? `<button class="ghost" data-dl-all>${ICON.dl}Download all</button>` : ""),
  }) + `<div class="wrap" data-queue="${esc(p.title)}" style="margin-top:28px">${vgrid(p.items)}</div>`);
  $("[data-play-all]")?.addEventListener("click", () => { playQueue = { title: p.title, items: p.items }; });
  $("[data-dl-all]")?.addEventListener("click", (e) => {
    popover(e.currentTarget, `Download ${plural(p.items.length, "video")}`, [
      ...QUALITY_CHOICES.map(([q, label, note]) => [label, note, () => downloadAll(p.items, q, "video")]),
      ["Audio to your music library", "", () => downloadAll(p.items, "best", "music")],
    ]);
  });
}
async function downloadAll(items, quality, kind) {
  let n = 0;
  for (const v of items) {
    try { await post("/api/yt/downloads", { id: v.id, quality, kind, title: v.title, channel: v.channel || "", sponsorblock: kind === "video" && pref("cutSponsors") }); n++; } catch { /* skip */ }
  }
  toast(`Queued ${plural(n, "download")}`, true);
  poll();
}

/* ---------- the player ---------- */

let player = null; // the current watch page's state, torn down on leaving it
const SB_LABEL = { sponsor: "Sponsor", selfpromo: "Self-promotion", interaction: "Subscribe reminder", intro: "Intro", outro: "Outro",
  preview: "Preview/recap", music_offtopic: "Non-music part", filler: "Filler", poi_highlight: "Highlight" };

function playable(el, f) { const r = el.canPlayType(f.mime); return r === "probably" || r === "maybe"; }
function pickVideo(el, list, maxH) {
  const ok = list.filter((f) => playable(el, f));
  const sdr = ok.filter((f) => !f.hdr);
  const pool = sdr.length ? sdr : ok;
  const fit = pool.filter((f) => (f.height || 0) <= maxH);
  const choice = fit.length ? fit : pool.slice(-1);
  if (!choice.length) return null;
  const top = Math.max(...choice.map((f) => f.height || 0));
  const order = top <= 1080 ? ["avc1", "vp09", "vp9", "av01"] : ["vp09", "vp9", "av01", "avc1"];
  const rank = (f) => order.indexOf(f.codec) + 1 || 99;
  return choice.filter((f) => (f.height || 0) === top).sort((a, b) => (b.fps || 0) - (a.fps || 0) || rank(a) - rank(b))[0];
}
const qualityLabel = (f) => `${f.height}p${f.fps > 30 ? Math.round(f.fps) : ""}${f.hdr ? " HDR" : ""}`;

function linkify(text, seekable) {
  const times = (part) => (!seekable ? part : part.replace(/(^|[\s(])((?:\d{1,2}:)?\d{1,2}:\d{2})(?=[\s).,]|$)/gm, (m, pre, ts) => {
    const s = ts.split(":").reduce((n, x) => n * 60 + +x, 0);
    return `${pre}<button class="ts" data-seek="${s}">${ts}</button>`;
  }));
  // links and plain text alternate; timestamps are only looked for in the text
  return esc(text).split(/(https?:\/\/[^\s<]+)/g)
    .map((part, i) => (i % 2 ? `<a href="${part}" target="_blank" rel="noopener noreferrer">${part}</a>` : times(part))).join("");
}

async function showWatch(t, [id, at]) {
  paint(t, `<div class="watch"><div><div class="player"><div class="overlay">${SPIN}</div></div><div class="skel line" style="height:28px;margin-top:20px"></div><div class="skel line short"></div></div><aside class="aside">${skeletons(5, "v", "upnext")}</aside></div>`);
  const [v, segments] = await Promise.all([api(`/api/yt/video/${encodeURIComponent(id)}`), api(`/api/yt/segments/${encodeURIComponent(id)}`).catch(() => [])]);
  if (t !== ticket) return;
  const card = { id: v.id, title: v.title, channel: v.channel, channel_id: v.channel_id, duration: v.duration, views: v.views };
  const published = v.published ? new Date(v.published * 1000).toLocaleDateString(undefined, { dateStyle: "medium" }) : "";
  const full = segments.find((s) => s.action === "full");
  paint(t, `<div class="watch${pref("theater") ? " theater" : ""}" id="watch">
    <div>
      <div class="player-wrap"><div class="player" id="player" style="${esc(`--photo:${cssUrl(v.thumbnail)}`)}">
        <video id="video" playsinline controls controlslist="nofullscreen nodownload" poster="${esc(v.thumbnail)}"></video>
        <audio id="audio" preload="auto"></audio>
      </div></div>
      <div class="seekmap" id="seekmap" title="Seek"></div>
      <div class="ptools">
        <select id="quality" aria-label="Quality"></select>
        <select id="captions" aria-label="Captions"><option value="">Captions off</option>${v.captions.map((c) => `<option value="${esc(c.lang)}">${esc(c.name)}</option>`).join("")}</select>
        <label class="ghost vol" style="gap:8px" title="Volume (↑ ↓, M mutes)">${ICON.note}<input id="volume" type="range" min="0" max="1" step="0.05" style="width:90px;accent-color:var(--accent)" aria-label="Volume"></label>
        <button class="ghost${segments.length ? "" : " vh"}" id="sbToggle" aria-pressed="true" title="Skip sponsors and other marked parts (SponsorBlock)">Skip sponsors</button>
        ${segments.some((s) => s.category === "poi_highlight") ? `<button class="ghost" id="highlight">Jump to highlight</button>` : ""}
        <span class="grow"></span>
        <span class="stat" id="stat"></span>
        <button class="ghost icon" id="theater" title="Theater mode (T)" aria-label="Theater mode">${ICON.wide}</button>
        <button class="ghost icon" id="fullscreen" title="Full screen (F)" aria-label="Full screen">${ICON.full}</button>
      </div>
      <div class="vinfo">
        <h1>${esc(v.title)}</h1>
        <div class="chan">
          <div class="who"><a href="${chanHref(v)}">${esc(v.channel)}${v.verified ? " ✓" : ""}</a><span>${v.followers != null ? `${compact.format(v.followers)} subscribers` : ""}</span></div>
          ${subButton(v.channel_id, v.channel, null, v.subscribed)}
          <div class="btns" data-video="${cardData(card)}">
            ${v.live === "is_live" || v.live === "is_upcoming" ? "" : `${v.downloaded ? `<span class="ghost on">${ICON.ok}Downloaded</span>` : `<button class="ghost" data-vdl>${ICON.dl}Download</button>`}
            <button class="ghost" data-tomusic>${ICON.note}Save audio to music</button>`}
            <button class="ghost${v.later ? " on" : ""}" data-later>${v.later ? ICON.ok : ICON.clock}<span>${v.later ? "Saved for later" : "Watch later"}</span></button>
            <button class="ghost" data-copy title="Copy a link at the current time">${ICON.link}Copy link</button>
            ${tvButton("data-cast-yt", v.id)}
            <a class="ghost" href="https://www.youtube.com/watch?v=${esc(v.id)}" target="_blank" rel="noopener noreferrer" title="Open on YouTube">${ICON.out}</a>
          </div>
        </div>
        ${full ? `<p class="pill pending" style="margin-top:14px"><span class="dot"></span>${esc(SB_LABEL[full.category] || full.category)}: marked for the whole video</p>` : ""}
        ${v.music ? `<p style="margin-top:14px"><a class="ghost" href="#/music/search/${encodeURIComponent([v.music.artist, v.music.track].filter(Boolean).join(" "))}">${ICON.disc}Find “${esc(v.music.track)}” in Music</a></p>` : ""}
        <div class="desc" id="desc">
          <div class="stats"><span>${views(v.views)}</span><span>${esc(published)}</span>${v.likes != null ? `<span>${compact.format(v.likes)} likes</span>` : ""}${v.category ? `<span class="dim">${esc(v.category)}</span>` : ""}</div>
          <div class="text">${linkify(v.description, true) || '<span class="dim">No description.</span>'}</div>
          ${v.description.length > 300 || v.description.split("\n").length > 4 ? `<button class="toggle" data-desc>Show more</button>` : ""}
        </div>
        ${v.chapters.length ? `<h2>Chapters</h2><ol class="chapters" id="chapters">${v.chapters.map((c) => `<li><button data-seek="${c.start}"><span class="at">${clock(c.start)}</span><span>${esc(c.title)}</span></button></li>`).join("")}</ol>` : ""}
        <h2>Comments${v.comments ? ` <span class="dim" style="font-weight:500">${compact.format(v.comments)}</span>` : ""}</h2>
        <div id="comments"><button class="ghost" data-comments>Show comments</button></div>
      </div>
    </div>
    <aside class="aside" id="aside"></aside>
  </div>`);
  setupPlayer(v, card, segments, at);
  fillAside(v);
}

function setupPlayer(v, card, segments, at) {
  stopPlayer();
  const video = $("#video"), audio = $("#audio"), box = $("#player");
  const state = { v, card, segments, video, audio, saved: 0, allowed: new Set(), timers: [], raf: 0, waitingForAudio: false, mode: "stream" };
  player = state;
  const fmtOf = (list, id) => list.find((f) => f.id === id);
  const audioFmt = v.audio.find((f) => playable(audio, f));
  const want = { best: 4320, 2160: 2160, 1440: 1440, 1080: 1080, 720: 720, 480: 480, 360: 360 }[pref("quality")] || 1080;
  const first = pickVideo(video, v.video, want);
  const choices = v.video.filter((f) => playable(video, f) && (!f.hdr || !v.video.some((g) => !g.hdr && g.height === f.height)));
  const seen = new Set();
  const opts = choices.filter((f) => { const k = qualityLabel(f); if (seen.has(k)) return false; seen.add(k); return f === pickVideo(video, v.video.filter((g) => qualityLabel(g) === k), 9999); });
  $("#quality").innerHTML = (v.downloaded ? `<option value="file">Downloaded copy (${esc(v.downloaded.quality === "best" ? "best" : v.downloaded.quality + "p")})</option>` : "") +
    opts.map((f) => `<option value="${esc(f.id)}">${qualityLabel(f)} · ${f.codec.replace("vp09", "vp9")}</option>`).join("") +
    (v.muxed.length ? `<option value="muxed:${esc(v.muxed[0].id)}">${v.muxed[0].height || 360}p (one stream)</option>` : "");

  function overlay(html) {
    $(".overlay", box)?.remove();
    if (html) box.insertAdjacentHTML("beforeend", `<div class="poster-bg"></div><div class="overlay">${html}</div>`);
  }
  if (v.live === "is_upcoming") {
    return overlay(`<strong>This hasn't started yet</strong><p>${v.published ? `It starts ${esc(new Date(v.published * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }))}. ` : ""}Come back then: it plays here.</p>`);
  }
  if (v.live === "is_live") { // through Parlor, in hls.js: go back over its last hour, LIVE takes you to now (live.js)
    for (const el of ["#quality", "#captions", "#sbToggle", "#highlight", "#seekmap", ".ptools .vol"]) { const x = $(el); if (x) x.hidden = true; } // the player's own controls do these
    $("#theater").addEventListener("click", () => { const on = !pref("theater"); setPref("theater", on); $("#watch").classList.toggle("theater", on); });
    state.mode = "live";
    state.live = livePlayer(box, video, `/api/live/yt/${encodeURIComponent(v.id)}.m3u8`);
    $("#fullscreen").addEventListener("click", () => (document.fullscreenElement ? document.exitFullscreen() : box.requestFullscreen?.().catch(() => {})));
    return;
  }

  // the server streams video and audio apart; keep the audio element in step with the video
  const sync = () => { if (state.mode === "stream" && Math.abs(audio.currentTime - video.currentTime) > 0.3) audio.currentTime = video.currentTime; };
  const on = (el, ev, fn) => el.addEventListener(ev, fn);
  on(video, "play", () => { if (state.mode === "stream") { sync(); audio.play().catch(() => {}); } });
  on(video, "pause", () => { if (!state.waitingForAudio) audio.pause(); save(); });
  on(video, "seeking", () => { if (state.mode === "stream") audio.currentTime = video.currentTime; });
  on(video, "waiting", () => { if (state.mode === "stream") audio.pause(); });
  on(video, "playing", () => { if (state.mode === "stream") { sync(); if (audio.paused) audio.play().catch(() => {}); } });
  on(video, "ratechange", () => { audio.playbackRate = video.playbackRate; setPref("speed", video.playbackRate); });
  on(audio, "waiting", () => {
    if (state.mode !== "stream" || video.paused) return;
    state.waitingForAudio = true;
    video.pause();
    clearTimeout(state.audioTimer); // slow audio: after 8 s carry on rather than hang
    state.audioTimer = setTimeout(() => {
      if (state.waitingForAudio) { state.waitingForAudio = false; video.play().catch(() => {}); toast("The sound is slow to load"); }
    }, 8000);
  });
  on(audio, "canplay", () => { clearTimeout(state.audioTimer); if (state.waitingForAudio) { state.waitingForAudio = false; video.play().catch(() => {}); } });
  on(video, "play", () => { if (state.waitingForAudio) { state.waitingForAudio = false; clearTimeout(state.audioTimer); } });
  on(audio, "error", () => {
    if (state.mode !== "stream" || !audio.getAttribute("src")) return;
    clearTimeout(state.audioTimer);
    state.waitingForAudio = false;
    const failed = audio.src.split("/").pop();
    (state.badAudio ||= new Set()).add(failed);
    const next = v.audio.find((f) => playable(audio, f) && !state.badAudio.has(f.id));
    if (next) {
      const resumeAt = video.currentTime;
      audio.src = `/api/yt/stream/${v.id}/${next.id}`;
      audio.currentTime = resumeAt;
      if (!video.paused) audio.play().catch(() => {});
      toast("Switched to another sound stream");
    } else {
      video.pause();
      overlay(`<strong>The sound wouldn't load</strong><p>${esc(audio.error?.message || "YouTube refused the audio stream.")}</p><button class="primary" data-retry-quality>Try again</button>`);
    }
  });
  on(video, "ended", () => { audio.pause(); save(true); upNext(); });
  on(video, "timeupdate", () => { sponsorCheck(); chapterMark(); });
  on(video, "error", () => {
    if (state.mode === "file" || !video.currentSrc) return;
    overlay(`<strong>This stream wouldn't play</strong><p>${esc(video.error?.message || "Your browser couldn't play it.")}</p>
      <button class="primary" data-retry-quality>Try another quality</button>`);
  });
  state.timers.push(setInterval(() => { if (!video.paused && !video.seeking) sync(); }, 1000));
  state.timers.push(setInterval(() => { if (!video.paused) save(); }, 10000));

  const volume = $("#volume");
  const setVolume = (x) => {
    x = Math.min(1, Math.max(0, x));
    audio.volume = video.volume = x; volume.value = x; setPref("volume", x);
  };
  setVolume(pref("volume"));
  on(volume, "input", () => { audio.muted = video.muted = false; setVolume(+volume.value); });
  on(video, "volumechange", () => { audio.muted = video.muted; });

  function load(choice, keepTime) {
    const t0 = keepTime ? video.currentTime : startAt();
    const wasPlaying = keepTime && !video.paused;
    overlay("");
    if (choice === "file") {
      state.mode = "file"; audio.removeAttribute("src"); audio.load();
      video.src = `/api/yt/file/${v.id}`;
    } else if (choice?.startsWith("muxed:")) {
      state.mode = "file"; audio.removeAttribute("src"); audio.load();
      video.src = `/api/yt/stream/${v.id}/${choice.slice(6)}`;
    } else {
      const f = fmtOf(v.video, choice);
      if (!f || !audioFmt) return overlay(`<strong>Your browser can't play these streams</strong><p>Download the video instead, and it plays from here.</p>`);
      state.mode = "stream";
      video.src = `/api/yt/stream/${v.id}/${f.id}`;
      if (!audio.src.endsWith(`/${audioFmt.id}`)) audio.src = `/api/yt/stream/${v.id}/${audioFmt.id}`;
      $("#stat").textContent = `${qualityLabel(f)} · ${f.codec.replace("vp09", "vp9")} + ${audioFmt.codec} ${Math.round(audioFmt.abr || 0)}k`;
    }
    if (state.mode === "file") $("#stat").textContent = choice === "file" ? "Playing your downloaded copy" : "One combined stream";
    video.playbackRate = audio.playbackRate = pref("speed");
    video.addEventListener("loadedmetadata", () => {
      if (t0) video.currentTime = t0;
      if (state.mode === "stream") audio.currentTime = video.currentTime;
      if (wasPlaying || !keepTime) video.play().catch(() => {});
    }, { once: true });
  }
  function startAt() {
    if (at) return +at;
    const p = v.position;
    if (p > 15 && (!v.duration || p < v.duration - 30)) {
      toast(`Picking up at ${clock(p)}`);
      return p;
    }
    return 0;
  }
  const initial = v.downloaded ? "file" : first ? first.id : v.muxed[0] ? `muxed:${v.muxed[0].id}` : null;
  if (!initial) overlay(`<strong>Your browser can't play this video's streams</strong><p>Download it instead, and it plays from here.</p>`);
  else { $("#quality").value = initial; load(initial); }
  on($("#quality"), "change", (e) => load(e.target.value, true));
  box.addEventListener("click", (e) => { if (e.target.closest("[data-retry-quality]")) { const sel = $("#quality"); sel.selectedIndex = Math.min(sel.options.length - 1, sel.selectedIndex + 1); load(sel.value, true); } });

  // captions
  const capSel = $("#captions");
  const showCaptions = (lang) => {
    $$("track", video).forEach((tr) => tr.remove());
    if (!lang) return;
    const tr = document.createElement("track");
    Object.assign(tr, { kind: "subtitles", srclang: lang, label: lang, src: `/api/yt/captions/${v.id}/${encodeURIComponent(lang)}`, default: true });
    video.append(tr);
    tr.addEventListener("load", () => { tr.track.mode = "showing"; });
    tr.track.mode = "showing";
  };
  const wantedLang = pref("captions");
  const match = wantedLang && v.captions.find((c) => c.lang === wantedLang || c.lang.startsWith(wantedLang + "-"));
  if (match) { capSel.value = match.lang; showCaptions(match.lang); }
  on(capSel, "change", () => { setPref("captions", capSel.value.replace(/-orig$/, "")); showCaptions(capSel.value); });

  // SponsorBlock: skip what you chose to skip, and offer to undo
  const sbBtn = $("#sbToggle");
  let sbOn = true;
  on(sbBtn, "click", () => { sbOn = !sbOn; sbBtn.setAttribute("aria-pressed", sbOn); sbBtn.classList.toggle("on", sbOn); toast(sbOn ? "Skipping sponsors" : "Not skipping sponsors"); });
  sbBtn.classList.add("on");
  function sponsorCheck() {
    if (!sbOn) return;
    const now = video.currentTime;
    for (const s of segments) {
      if (s.action !== "skip" && s.action !== "mute") continue;
      if (!pref("skip").includes(s.category) || state.allowed.has(s)) continue;
      if (s.action === "mute") {
        const inside = now >= s.start && now < s.end, sound = state.mode === "stream" ? audio : video;
        if (inside && !sound.muted) { sound.muted = true; state.sbMuted = true; }
        else if (!inside && state.sbMuted) { sound.muted = false; state.sbMuted = false; }
        continue;
      }
      if (now >= s.start && now < s.end - 0.4) {
        video.currentTime = s.end;
        skipped(s);
        break;
      }
    }
  }
  function skipped(s) {
    $(".skip-toast", box)?.remove();
    box.insertAdjacentHTML("beforeend", `<div class="skip-toast">Skipped ${esc((SB_LABEL[s.category] || s.category).toLowerCase())}<button data-unskip>Undo</button></div>`);
    const el = $(".skip-toast", box);
    $("[data-unskip]", el).onclick = () => { state.allowed.add(s); video.currentTime = s.start; el.remove(); };
    setTimeout(() => el.remove(), 5000);
  }
  $("#highlight")?.addEventListener("click", () => { const h = segments.find((s) => s.category === "poi_highlight"); if (h) video.currentTime = h.start; });

  // the seek map: "most replayed" heat, chapters, SponsorBlock segments and a live playhead
  const map = $("#seekmap");
  const dur = () => video.duration || v.duration || 1;
  const heat = v.heatmap.length ? (() => {
    const max = Math.max(...v.heatmap.map((h) => h[2])) || 1;
    const pts = v.heatmap.map(([s, e, val]) => `${(((s + e) / 2) / (v.duration || 1)) * 1000},${34 - (val / max) * 28}`);
    return `<svg class="heat" viewBox="0 0 1000 34" preserveAspectRatio="none" aria-hidden="true"><path d="M0,34 L${pts.join(" L")} L1000,34 Z"/></svg>`;
  })() : "";
  const pct = (x) => `${(100 * x) / (v.duration || 1)}%`;
  map.innerHTML = heat + v.chapters.slice(1).map((c) => `<span class="chap" style="left:${pct(c.start)}"></span>`).join("") +
    segments.filter((s) => s.end > s.start).map((s) => `<span class="seg sb-${esc(s.category)}" title="${esc(SB_LABEL[s.category] || s.category)}" style="left:${pct(s.start)};width:${pct(s.end - s.start)}"></span>`).join("") +
    `<span class="done"></span><span class="head"></span><span class="hover" hidden></span>`;
  const head = $(".head", map), done = $(".done", map), tip = $(".hover", map);
  const frame = () => {
    const x = (100 * video.currentTime) / dur();
    head.style.left = `${x}%`; done.style.width = `${x}%`;
    state.raf = requestAnimationFrame(frame);
  };
  state.raf = requestAnimationFrame(frame);
  const timeAt = (e) => { const r = map.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * dur(); };
  on(map, "click", (e) => { video.currentTime = timeAt(e); });
  on(map, "mousemove", (e) => {
    const s = timeAt(e);
    const ch = [...v.chapters].reverse().find((c) => c.start <= s);
    tip.hidden = false; tip.textContent = clock(s) + (ch ? ` · ${ch.title}` : "");
    tip.style.left = `${Math.min(92, Math.max(8, (100 * s) / dur()))}%`;
  });
  on(map, "mouseleave", () => { tip.hidden = true; });

  function chapterMark() {
    const list = $$("#chapters button");
    if (!list.length) return;
    let idx = -1;
    v.chapters.forEach((c, i) => { if (video.currentTime >= c.start) idx = i; });
    list.forEach((b, i) => b.classList.toggle("now", i === idx));
  }

  // theater, full screen, keys
  on($("#theater"), "click", () => { const on_ = !pref("theater"); setPref("theater", on_); $("#watch").classList.toggle("theater", on_); });
  const fullscreen = () => (document.fullscreenElement ? document.exitFullscreen() : box.requestFullscreen?.().catch(() => {}));
  on($("#fullscreen"), "click", fullscreen);
  on(video, "dblclick", fullscreen);
  state.keys = (e) => {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    const act = {
      " ": () => (video.paused ? video.play() : video.pause()), k: () => (video.paused ? video.play() : video.pause()),
      j: () => (video.currentTime -= 10), l: () => (video.currentTime += 10), ArrowLeft: () => (video.currentTime -= 5), ArrowRight: () => (video.currentTime += 5),
      ArrowUp: () => setVolume(video.volume + 0.05), ArrowDown: () => setVolume(video.volume - 0.05),
      m: () => { video.muted = !video.muted; audio.muted = video.muted; toast(video.muted ? "Muted" : "Sound on"); },
      f: fullscreen, t: () => $("#theater").click(),
      c: () => { capSel.selectedIndex = capSel.selectedIndex ? 0 : Math.min(1, capSel.options.length - 1); capSel.dispatchEvent(new Event("change")); },
      ">": () => { video.playbackRate = Math.min(3, video.playbackRate + 0.25); toast(`${video.playbackRate}×`); },
      "<": () => { video.playbackRate = Math.max(0.25, video.playbackRate - 0.25); toast(`${video.playbackRate}×`); },
      N: () => upNext(true),
    }[k] || (/^[0-9]$/.test(k) ? () => (video.currentTime = (dur() * +k) / 10) : null);
    if (act) { e.preventDefault(); act(); }
  };
  document.addEventListener("keydown", state.keys);

  // the system's media controls and lock screen
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: v.title, artist: v.channel, artwork: [{ src: v.thumbnail, sizes: "1280x720" }] });
    const ms = navigator.mediaSession;
    ms.setActionHandler("play", () => video.play());
    ms.setActionHandler("pause", () => video.pause());
    ms.setActionHandler("seekbackward", () => (video.currentTime -= 10));
    ms.setActionHandler("seekforward", () => (video.currentTime += 10));
    ms.setActionHandler("nexttrack", () => upNext(true));
  }

  function save(ended) {
    const position = ended ? v.duration || video.currentTime : video.currentTime;
    if (!position || Math.abs(position - state.saved) < 2) return;
    state.saved = position;
    post("/api/me/history", { video: card, position }).catch(() => {});
  }
  state.save = save;
  addEventListener("pagehide", state.onHide = () => save());
}

function stopPlayer() {
  if (!player) return;
  player.live?.destroy();
  player.save?.();
  player.timers.forEach(clearInterval);
  clearTimeout(player.audioTimer);
  cancelAnimationFrame(player.raf);
  document.removeEventListener("keydown", player.keys);
  removeEventListener("pagehide", player.onHide);
  player.video.pause(); player.audio.pause();
  player.video.removeAttribute("src"); player.audio.removeAttribute("src");
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  player = null;
}
addEventListener("hashchange", () => { if (!location.hash.startsWith("#/watch/")) stopPlayer(); });

async function fillAside(v) {
  const aside = $("#aside");
  if (!aside) return;
  let title, items;
  const at = playQueue?.items.findIndex((i) => i.id === v.id) ?? -1;
  if (at >= 0) {
    title = `Up next in ${playQueue.title}`;
    items = playQueue.items.slice(at + 1).concat(playQueue.items.slice(0, at)).slice(0, 30);
  } else {
    playQueue = null;
    title = "Up next";
    items = (await api(`/api/yt/related/${v.id}`).catch(() => ({ items: [] }))).items.filter((i) => i.id !== v.id).slice(0, 24);
    if (!items.length && v.channel_id) { // YouTube's list couldn't be read: the channel's other videos instead
      title = `More from ${v.channel}`;
      items = (await api(`/api/yt/channel/${v.channel_id}`).catch(() => ({ items: [] }))).items.filter((i) => i.type === "video" && i.id !== v.id).slice(0, 20);
    }
  }
  if (!$("#aside")) return;
  aside.innerHTML = items.length ? `<h2>${esc(title)}</h2><div class="upnext"${playQueue ? ` data-queue="${esc(playQueue.title)}"` : ""}>${items.map((i) => vcard(i)).join("")}</div>` : "";
  aside.dataset.next = items[0]?.id || "";
}

let autoplayTimer;
function upNext(now) {
  const next = $("#aside")?.dataset.next;
  if (!next || (!now && !pref("autoplay"))) return;
  if (now) return go(`#/watch/${next}`);
  const box = $("#player");
  box?.insertAdjacentHTML("beforeend", `<div class="overlay" id="autoplay"><strong>Up next in 5 seconds</strong><div class="actions" style="justify-content:center">
    <a class="primary" href="#/watch/${esc(next)}">${ICON.play}Play now</a><button class="ghost" style="color:#fff" data-cancel-next>Cancel</button></div></div>`);
  clearTimeout(autoplayTimer);
  autoplayTimer = setTimeout(() => { if ($("#autoplay")) go(`#/watch/${next}`); }, 5000);
  $("[data-cancel-next]")?.addEventListener("click", () => { clearTimeout(autoplayTimer); $("#autoplay")?.remove(); });
}

document.addEventListener("click", (e) => {
  const seek = e.target.closest("[data-seek]");
  if (seek && player) { player.video.currentTime = +seek.dataset.seek; player.video.play().catch(() => {}); scrollTo({ top: 0, behavior: "smooth" }); }
  if (e.target.closest("[data-desc]")) { const d = $("#desc"); d.classList.toggle("open"); e.target.textContent = d.classList.contains("open") ? "Show less" : "Show more"; }
  const tomusic = e.target.closest("[data-tomusic]");
  if (tomusic && player) queueVideo(player.card, "best", "music", tomusic);
  if (e.target.closest("[data-copy]") && player) {
    const at = Math.floor(player.video.currentTime);
    navigator.clipboard?.writeText(`https://youtu.be/${player.v.id}${at > 5 ? `?t=${at}` : ""}`).then(() => toast(at > 5 ? `Copied a link at ${clock(at)}` : "Copied the link"));
  }
  const more = e.target.closest("[data-comments]");
  if (more && player) loadComments(player.v.id, more);
});

async function loadComments(id, button) {
  button.disabled = true;
  button.innerHTML = `${SPIN.replace('class="spin"', 'class="spin" style="width:20px;height:20px"')} Loading comments…`;
  try {
    const r = await api(`/api/yt/comments/${id}`);
    const one = (c) => `<li>${img(c.avatar)}<div>
      <div class="by"><span class="${c.creator ? "creator" : ""}">${esc(c.author)}</span>${c.pinned ? "<small>Pinned</small>" : ""}<small>${since(c.published)}</small></div>
      <div class="text">${linkify(c.text, true)}</div>
      <div class="likes">${c.likes ? `<span>♥ ${compact.format(c.likes)}</span>` : ""}${c.hearted ? "<span>❤ by the creator</span>" : ""}</div>
      ${c.replies.length ? `<details><summary class="dim">${plural(c.replies.length, "reply")}</summary><ul>${c.replies.map(one).join("")}</ul></details>` : ""}
    </div></li>`;
    $("#comments").innerHTML = r.items.length ? `<ul class="comments">${r.items.map(one).join("")}</ul>` : `<p class="dim">No comments.</p>`;
  } catch (e) {
    button.disabled = false;
    button.textContent = "Try loading comments again";
    toast(e.message);
  }
}

page("yt", showYt, "watch");
page("watch", showWatch, "watch");
page("channel", showChannel, "watch");
page("ytlist", showYtList, "watch");
