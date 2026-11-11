/* Request → Films & shows: discover, search and request through Seerr, as your own Seerr user; and Your requests, every kind in one list. */

const STATUS_LABEL = { available: "Available", partial: "Partly available", processing: "Processing", pending: "Requested", requested: "Requested",
  approved: "Approved", declined: "Declined", failed: "Failed", completed: "Completed", blocked: "Blocked", deleted: "Deleted", missing: "Not requested" };
const statusOf = (x) => (x.status && x.status !== "unknown" ? x.status : x.requested ? "pending" : null);
const pill = (st, text) => `<span class="pill ${esc(st)}"><span class="dot"></span>${esc(text || STATUS_LABEL[st] || st)}</span>`;
const runtime = (m) => (m ? (m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`) : "");
const mediaData = (x) => esc(JSON.stringify({ type: x.type, id: x.id, title: x.title }));
let seerrOk = null; // null: unknown yet

function pcard(x) {
  if (x.type === "person") {
    return `<a class="person" href="#/person/${esc(x.id)}">${img(x.photo)}<span class="t">${esc(x.name)}</span><span class="s">${esc((x.known_for || []).join(", "))}</span></a>`;
  }
  const href = `#/${x.type}/${esc(x.id)}`;
  const st = statusOf(x);
  const askable = !st || st === "deleted";
  return `<article class="pcard" data-media="${mediaData(x)}">
    <div class="poster"><a href="${href}" tabindex="-1" aria-hidden="true">${x.poster ? `<img src="${esc(x.poster)}" alt="" loading="lazy">` : `<span class="noart">${esc(x.title)}</span>`}</a>
      ${st ? pill(st) : ""}<span class="kindtag">${x.type === "tv" ? "TV" : "FILM"}</span>
      ${askable ? `<button class="req" data-request>Request</button>` : ""}</div>
    <a class="t" href="${href}">${esc(x.title)}</a>
    <span class="s">${esc(x.year || "")}${x.rating ? ` <span class="star">★</span> ${x.rating}` : ""}${x.role ? ` · ${esc(x.role)}` : ""}</span>
  </article>`;
}
const pstrip = (items) => (items?.length ? `<div class="pstrip">${items.map(pcard).join("")}</div>` : "");

/* what Sonarr and Radarr are fetching and what's coming up, as posters */
function dayLabel(iso) {
  // a release date is a calendar day at 00:00 UTC: read it as that day wherever you are
  const d = /T00:00:00(\.0+)?Z$/.test(iso) ? new Date(`${iso.slice(0, 10)}T00:00:00`) : new Date(iso), today = new Date();
  const days = Math.round((new Date(d.toDateString()) - new Date(today.toDateString())) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}
function ucard(x, label, progress) {
  const art = `<div class="poster">${x.image ? `<img src="${esc(x.image)}" alt="" loading="lazy">` : `<span class="noart">${esc(x.title)}</span>`}
    ${x.have ? pill("available") : ""}<span class="kindtag">${x.app === "sonarr" ? "TV" : "FILM"}</span>
    ${progress != null ? `<div class="meter"><i style="width:${(100 * progress).toFixed(1)}%"></i></div>` : ""}</div>`;
  return `<article class="pcard">${x.link ? `<a href="${esc(x.link)}" tabindex="-1" aria-hidden="true">${art}</a>` : art}
    ${x.link ? `<a class="t" href="${esc(x.link)}">${esc(x.title)}</a>` : `<span class="t">${esc(x.title)}</span>`}
    <span class="s">${esc(label)}</span></article>`;
}
async function arrShelves(t) {
  const [q, c] = await Promise.allSettled([api("/api/arr/queue"), api("/api/arr/calendar?days=14")]);
  const box = $("#arrShelves");
  if (!box || t !== ticket) return;
  const video = (r) => (r.status === "fulfilled" ? r.value.items.filter((x) => x.app !== "lidarr") : []);
  const fetching = video(q).map((x) => ucard(x, [x.subtitle, x.state === "downloading" ? `${Math.floor(100 * x.progress)}%` : arrState(x)].filter(Boolean).join(" · "),
    x.state === "downloading" ? x.progress : null));
  const soon = video(c).map((x) => ucard(x, [dayLabel(x.date), x.app === "sonarr" ? x.subtitle : x.what].filter(Boolean).join(" · ")));
  box.innerHTML = shelf("Downloading now", fetching.length ? `<div class="pstrip even">${fetching.join("")}</div>` : "")
    + shelf("Coming up", soon.length ? `<div class="pstrip even">${soon.join("")}</div>` : "");
}
const pgrid = (items) => (items?.length ? `<div class="pgrid">${items.map(pcard).join("")}</div>` : "");

function notConnected(t, reason) {
  paint(t, `<section class="home">${aurora}<h1>Films &amp; shows</h1>
    <p>Find films and shows and request them; Seerr gets them into your library.</p>${subnav()}</section>
    <div class="wrap"><div class="panel"><div class="head"><div><h3>Seerr isn't connected yet</h3>
      <p>${ME?.admin ? "Add your Seerr address and API key (Seerr → Settings → General → API Key)." : "Ask an admin to connect Seerr."} ${reason && reason !== "not set up" ? `(${esc(reason)})` : ""}</p></div>
      ${ME?.admin ? `<a class="primary" href="#/settings">Connect Seerr</a>` : ""}</div></div></div>`);
}

async function checkSeerr() {
  if (seerrOk) return true;
  const s = await api("/api/seerr/status");
  seerrOk = s.connected;
  return s;
}

const mediaChips = () => subnav();

async function showMedia(t, [what = "", ...rest]) {
  if (what === "search") return showMediaSearch(t, rest.join("/"));
  if (what === "more") return showMore(t, rest[0]);
  const s = await checkSeerr();
  if (s !== true && !s.connected) return notConnected(t, s.reason);
  paint(t, `<div class="hero backdrop" style="--tint:#2a2440"><div></div></div><div class="wrap">${skeletons(8, "p", "pstrip")}</div>`);
  const d = await api("/api/seerr/discover");
  const spot = (d.trending || []).filter((x) => x.backdrop && x.type !== "person").slice(0, 6);
  paint(t, `<div id="spotlight">${spot.length ? spotlight(spot[0]) : ""}</div>
    <div class="wrap">${mediaChips("")}
      <nav class="chips page-actions" aria-label="More ways to ask"><a href="#/sets">Collections: a whole franchise, studio or network</a></nav>
      <div id="arrShelves"></div>
      <div id="setShelf"></div>
      ${shelf("Trending this week", pstrip(d.trending), "#/media/more/trending")}
      ${shelf("Popular films", pstrip(d.movies), "#/media/more/movies")}
      ${shelf("Popular shows", pstrip(d.tv), "#/media/more/tv")}
      ${shelf("Coming soon to cinemas", pstrip(d.upcoming), "#/media/more/upcoming")}
      ${shelf("New and upcoming shows", pstrip(d.upcoming_tv), "#/media/more/upcoming_tv")}
    </div>`);
  arrShelves(t);
  api("/api/seerr/sets").then((s) => { const box = $("#setShelf"); if (box && t === ticket) box.innerHTML = shelf("Franchises", s.franchises.length ? `<div class="pstrip">${s.franchises.slice(0, 18).map(setCard).join("")}</div>` : "", "#/sets"); }, () => {});
  // the spotlight cycles through what's trending
  let i = 0;
  const timer = setInterval(() => {
    const box = $("#spotlight");
    if (!box || t !== ticket) return clearInterval(timer);
    if (document.hidden || box.matches(":hover")) return;
    i = (i + 1) % spot.length;
    box.style.transition = "opacity .5s ease"; box.style.opacity = 0;
    setTimeout(() => { box.innerHTML = spotlight(spot[i]); box.style.opacity = 1; }, 500);
  }, 9000);
}

function spotlight(x) {
  const st = statusOf(x);
  return hero({
    kind: x.type === "tv" ? "Trending show" : "Trending film", title: x.title, backdrop: x.backdrop, image: x.poster, poster: true,
    meta: `<span>${esc(x.year || "")}</span>${x.rating ? `<span><span class="star">★</span> ${x.rating}</span>` : ""}${st ? pill(st) : ""}`,
    blurb: x.overview?.length > 260 ? x.overview.slice(0, 257) + "…" : x.overview,
    action: `<a class="primary" href="#/${x.type}/${esc(x.id)}">More info</a>${!st ? `<span data-media="${mediaData(x)}"><button class="ghost" data-request>Request</button></span>` : ""}`,
  });
}

async function showMore(t, which) {
  const titles = { trending: "Trending this week", movies: "Popular films", tv: "Popular shows", upcoming: "Coming soon to cinemas", upcoming_tv: "New and upcoming shows" };
  let pageNo = 1;
  const r = await api(`/api/seerr/discover/${encodeURIComponent(which)}?page=1`);
  paint(t, `<section class="home">${aurora}<h1>${esc(titles[which] || "Discover")}</h1>${mediaChips("")}</section>
    <div class="wrap"><div class="pgrid" id="more">${r.items.map(pcard).join("")}</div>
    <div class="actions" style="justify-content:center;margin-top:30px"><button class="ghost" id="loadMore">Load more</button></div></div>`);
  $("#loadMore").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const next = await api(`/api/seerr/discover/${encodeURIComponent(which)}?page=${++pageNo}`).catch((err) => { toast(err.message); return { items: [] }; });
    $("#more").insertAdjacentHTML("beforeend", next.items.map(pcard).join(""));
    e.target.disabled = !next.items.length;
  });
}

async function showMediaSearch(t, q) {
  input.value = q;
  const s = await checkSeerr();
  if (s !== true && !s.connected) return notConnected(t, s.reason);
  const r = await api(`/api/seerr/search?q=${encodeURIComponent(q)}`);
  const people = r.items.filter((x) => x.type === "person"), titles = r.items.filter((x) => x.type !== "person");
  const html = shelf("Films and shows", pgrid(titles)) + shelf("People", people.length ? `<div class="people">${people.map(pcard).join("")}</div>` : "");
  paint(t, html ? `<div class="wrap">${html}</div>` : `<div class="note"><strong>Nothing matches “${esc(q)}”</strong>Try the original title, or fewer words.</div>`);
}

async function showTitle(t, kind, [id]) {
  const x = await api(`/api/seerr/${kind}/${encodeURIComponent(id)}`);
  const st = statusOf(x);
  const missing = (x.seasons || []).filter((s) => s.status === "missing");
  const canAsk = x.can_request && (kind === "movie" ? !st || st === "deleted" : missing.length > 0);
  const people = (list) => list.map((p) => `<a class="person" href="#/person/${esc(p.id)}">${img(p.photo)}<span class="t">${esc(p.name)}</span><span class="s">${esc(p.role || "")}</span></a>`).join("");
  const crew = x.crew.length ? `<p class="dim">${x.crew.map((c) => `${esc(c.role)}: <a href="#/person/${esc(c.id)}">${esc(c.name)}</a>`).join(" · ")}</p>` : "";
  const actions = [
    st ? pill(st) : "",
    canAsk ? `<span data-media="${mediaData(x)}"><button class="primary" data-request>${kind === "tv" && st ? "Request more seasons" : "Request"}</button></span>` : "",
    !x.can_request && !st ? `<small>Your account isn't linked to Seerr yet; ask an admin.</small>` : "",
    x.trailer ? `<a class="ghost" href="#/watch/${esc(x.trailer.id)}">${ICON.play}Watch the trailer</a>` : "",
    x.jellyfin && libraryOpen() ? `<a class="primary" href="#/jf/${kind === "movie" ? "play" : "item"}/${esc(x.jellyfin)}">${ICON.play}${kind === "movie" ? "Play" : "Watch"}</a>`
      : /^https?:\/\//.test(x.url || "") ? `<a class="ghost" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${ICON.out}Watch in your library</a>` : "",
  ].join("");
  const seasons = kind === "tv" && x.seasons.length ? `<h2>Seasons</h2><ul class="seasons" id="seasons">${x.seasons.map((s) => `<li>
      <input type="checkbox" value="${s.number}" ${s.status === "missing" ? "" : "disabled checked"} aria-label="${esc(s.name)}">
      ${img(s.poster)}<div><span class="t">${esc(s.name)}</span><span class="s">${esc([s.air, s.episodes && plural(s.episodes, "episode")].filter(Boolean).join(" · "))}</span></div>
      ${pill(s.status)}</li>`).join("")}</ul>
    ${missing.length && x.can_request ? `<div class="actions"><button class="primary" id="askSeasons" disabled>Request seasons</button><button class="ghost" id="allSeasons">Select all missing</button></div>` : ""}` : "";
  const reqs = x.requests.length ? `<h2>Requests</h2><ul class="lists">${x.requests.map((r) => `<li><div class="blank" style="display:grid;place-items:center">${ICON.film}</div><div>
      <span class="name">${esc(r.by || "Someone")}</span><span class="sub">${esc([r.seasons.length ? `Seasons ${r.seasons.join(", ")}` : "", r.created && since(Date.parse(r.created) / 1000), r.is4k ? "4K" : ""].filter(Boolean).join(" · "))}</span></div>
      <div class="acts">${pill(r.status)}</div></li>`).join("")}</ul>` : "";
  const sets = [...(x.studios || []).map((a) => ({ ...a, kind: "studio" })), ...(x.networks || []).map((a) => ({ ...a, kind: "network" })), ...(x.keywords || []).map((a) => ({ ...a, kind: "keyword" }))];
  const facts = [x.year, runtime(x.runtime), x.certification, x.seasons_count && plural(x.seasons_count, "season"), x.network || x.studio].filter(Boolean);
  paint(t, hero({
    kind: kind === "tv" ? `TV series${x.show_status ? ` · ${x.show_status}` : ""}` : "Film", title: x.title, backdrop: x.backdrop, image: x.poster, poster: true, tagline: x.tagline,
    meta: facts.map((f) => `<span>${esc(f)}</span>`).join("") + (x.rating ? `<span><span class="star">★</span> ${x.rating}</span>` : "") + (x.genres.length ? `<span>${esc(x.genres.join(", "))}</span>` : ""),
    blurb: x.overview, action: actions,
  }) + `<div class="wrap">
      ${x.watch.length ? `<div class="shelf-head"><h2>Streaming on</h2></div><div class="providers">${x.watch.map((p) => `<img src="${esc(p.logo)}" alt="${esc(p.name)}" title="${esc(p.name)}">`).join("")}</div>` : ""}
      ${x.collection ? `<a class="partof" href="${setHref("collection", x.collection.id)}">${ICON.film}<span>Part of <strong>${esc(x.collection.name)}</strong><small>See every film in it and request the ones you're missing</small></span></a>` : ""}
      ${seasons}${reqs}
      ${x.cast.length ? `<h2>Cast</h2><div class="people">${people(x.cast)}</div>${crew}` : crew}
      ${shelf("If you like this", pstrip(x.recommended))}
      ${sets.length ? `<div class="shelf-head"><h2>More from</h2></div><nav class="chips wrapped">${setChips(sets)}</nav>` : ""}
    </div>`);
  const boxes = $$("#seasons input:not([disabled])");
  const askBtn = $("#askSeasons");
  const update = () => { const n = boxes.filter((b) => b.checked).length; if (askBtn) { askBtn.disabled = !n; askBtn.textContent = n ? `Request ${plural(n, "season")}` : "Request seasons"; } };
  boxes.forEach((b) => b.addEventListener("change", update));
  $("#allSeasons")?.addEventListener("click", () => { boxes.forEach((b) => (b.checked = true)); update(); });
  askBtn?.addEventListener("click", () => request({ type: "tv", id: x.id, title: x.title }, askBtn, boxes.filter((b) => b.checked).map((b) => +b.value)));
}

async function request(m, button, seasons) {
  button.disabled = true;
  try {
    const r = await post("/api/seerr/request", { type: m.type, id: m.id, seasons: seasons ?? (m.type === "tv" ? "all" : null) });
    toast(r.status === "approved" ? `Requested “${m.title}”; it's on its way` : `Requested “${m.title}”; it's waiting for approval`);
    button.outerHTML = pill(r.status === "approved" ? "approved" : "pending", r.status === "approved" ? "Approved" : "Requested");
    if (location.hash.startsWith(`#/${m.type}/`)) { keepScroll = true; route(); }
  } catch (e) {
    button.disabled = false;
    toast(e.message);
  }
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-request]");
  if (!b) return;
  const m = JSON.parse(b.closest("[data-media]").dataset.media);
  if (m.type === "tv" && !location.hash.startsWith("#/tv/")) {
    popover(b, m.title, [["All seasons", "", () => request(m, b, "all")], ["Choose seasons…", "", () => go(`#/tv/${m.id}`)]]);
  } else if (m.type === "tv") {
    $("#seasons")?.scrollIntoView({ behavior: "smooth", block: "center" });
  } else {
    request(m, b);
  }
});

async function showPerson(t, [id]) {
  const p = await api(`/api/seerr/person/${encodeURIComponent(id)}`);
  const life = [p.born && `Born ${p.born}`, p.place, p.died && `Died ${p.died}`].filter(Boolean).join(" · ");
  paint(t, hero({ kind: p.department || "Person", title: p.name, image: p.photo, round: true, tintColor: "#2e2648",
    meta: `<span>${esc(life)}</span>`, blurb: p.bio.length > 600 ? p.bio.slice(0, 597) + "…" : p.bio, action: "" }) +
    `<div class="wrap">${shelf("Known for", pgrid(p.items))}</div>`);
}

const REQ_FILTERS = [["all", "All"], ["pending", "Waiting for approval"], ["approved", "Approved"], ["processing", "Processing"], ["available", "Available"], ["failed", "Failed"]];
// Your requests: every kind in one list (#/requests), or one kind: #/requests/mine and /all are films & shows (yours, everyone's), /music is music, /games is games
const REQ_KINDS = [["", "Everything", () => true], ["mine", "Films & shows", () => !closedTo("media")], ["music", "Music", () => !closedTo("music")], ["games", "Games", () => !closedTo("games")]];
async function showRequests(t, [a = "", filter = "all"]) {
  const kind = a === "music" || a === "games" ? a : a ? "mine" : "", scope = a === "all" ? "all" : "mine";
  const one = (k, section) => (!kind || kind === k) && !closedTo(section);
  const films = one("mine", "media"), music = one("music", "music"), games = one("games", "games");
  if (kind && !(films || music || games)) return go("#/requests");  // a kind that's closed to you: the list of what isn't
  let r = null, why = "";
  if (films) {
    const s = await checkSeerr();
    const asked = () => api(`/api/seerr/requests?scope=${encodeURIComponent(scope)}&filter=${encodeURIComponent(filter)}`);
    if (s !== true && !s.connected) {
      if (kind) return notConnected(t, s.reason);
      why = "Seerr isn't connected yet, so there are no film or show requests.";
    } else if (kind) r = await asked();
    else r = await asked().catch(() => { why = "Seerr didn't answer, so film and show requests aren't listed."; return null; });  // the music below still shows
  }
  const scopes = ME?.admin ? `<a href="#/requests/mine/${filter}"${scope === "mine" ? ' aria-current="page"' : ""}>Yours</a><a href="#/requests/all/${filter}"${scope === "all" ? ' aria-current="page"' : ""}>Everyone's</a><span class="sep"></span>` : "";
  const row = (q) => `<li style="${q.backdrop ? esc(`--bd:${cssUrl(q.backdrop)}`) : ""}">
      ${img(q.poster)}
      <div><a class="t" href="#/${esc(q.type)}/${esc(q.tmdb)}">${esc(q.title)}</a>
        <div class="s"><span>${esc([q.year, q.type === "tv" ? "TV" : "Film", q.seasons.length ? `seasons ${q.seasons.join(", ")}` : ""].filter(Boolean).join(" · "))}</span>
          <span>${esc(q.by || "")} · ${since(Date.parse(q.created) / 1000)}</span></div>
        <div class="s" style="margin-top:6px">${pill(q.status)}${q.media !== "unknown" ? pill(q.media) : ""}</div></div>
      <div class="acts">
        ${ME?.admin && q.status === "pending" ? `<button class="ghost" data-decide="approve" data-id="${q.id}">Approve</button><button class="ghost danger" data-decide="decline" data-id="${q.id}">Decline</button>` : ""}
        ${(q.mine || ME?.admin) && q.status === "pending" ? `<button class="ghost danger" data-cancel-req="${q.id}">Cancel</button>` : ""}
      </div></li>`;
  const list = !r ? (why ? `<p class="dim">${why}</p>` : "")
    : !r.linked ? `<div class="empty-state"><strong>Your account isn't linked to a Seerr user</strong>Ask an admin to link it in Settings → Users.</div>`
    : r.items.length ? `<ul class="reqs">${r.items.map(row).join("")}</ul>`
    : kind ? `<div class="empty-state"><strong>No requests here</strong>Find something in <a href="#/media">Films &amp; shows</a> and press Request.</div>` : "";
  paint(t, `<section class="home">${aurora}<h1>Your requests</h1>
      <p>${kind === "mine" && scope === "all" ? "Every film and show everyone has asked for." : "What you've asked for, and where it's at."}</p>
      ${mediaChips("requests")}
      <nav class="chips" aria-label="Kind">${REQ_KINDS.filter(([, , open]) => open()).map(([k, label]) => `<a href="#/requests${k && "/" + k}"${k === kind ? ' aria-current="page"' : ""}>${label}</a>`).join("")}</nav>
      ${kind === "mine" ? `<nav class="chips" aria-label="Which">${scopes}${REQ_FILTERS.map(([k, label]) => `<a href="#/requests/${scope}/${k}"${k === filter ? ' aria-current="page"' : ""}>${label}</a>`).join("")}</nav>` : ""}</section>
    <div class="wrap">${kind || !list ? list : shelf("Films & shows", list, "#/requests/mine")}</div>
    ${music ? `<div class="wrap" id="musicReqs"></div>` : ""}
    ${games ? `<div class="wrap" id="gameReqs"></div>` : ""}
    <div class="wrap" id="noReqs" hidden><div class="empty-state"><strong>Nothing requested yet</strong>Ask for a film or a show in <a href="#/media">Films &amp; shows</a>, an album in <a href="#/lidarr">Music</a>, or a game in <a href="#/gamereq">Games</a>.</div></div>`);
  const [had, got] = await Promise.all([music ? drawRequests(t, kind ? "" : "Music") : 0, games ? drawGameRequests(t, "Games") : 0]);
  if (t === ticket && kind !== "music" && !had && !got && !list) $("#noReqs").hidden = false;  // music alone says how to ask by itself
}
document.addEventListener("click", (e) => {
  const d = e.target.closest("[data-decide]");
  if (d) { d.disabled = true; post(`/api/seerr/requests/${d.dataset.id}/${d.dataset.decide}`).then(() => { toast(d.dataset.decide === "approve" ? "Approved" : "Declined"); keepScroll = true; route(); }, (err) => { d.disabled = false; toast(err.message); }); }
  const c = e.target.closest("[data-cancel-req]");
  if (c && confirm("Cancel this request?")) del(`/api/seerr/requests/${c.dataset.cancelReq}`).then(() => { toast("Cancelled"); keepScroll = true; route(); }, (err) => toast(err.message));
});

page("media", showMedia, "media");
page("movie", (t, parts) => showTitle(t, "movie", parts), "media");
page("tv", (t, parts) => showTitle(t, "tv", parts), "media");
page("person", showPerson, "media");
page("requests", showRequests, "media");
