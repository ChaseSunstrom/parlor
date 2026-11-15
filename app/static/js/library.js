/* Library: what's in Jellyfin, played right here. Films and shows with resume, next up and watched state kept in
   your Jellyfin account; music and audiobooks in the dock (player.js); books in a reader. Streams go through
   /api/jf, which holds the Jellyfin key: the browser never sees it. */

const JF_KINDS = {
  movies: { types: "Movie", title: "Films", blurb: "Every film in your library, ready to play." },
  shows: { types: "Series", title: "Shows", blurb: "Every show, with where you got to." },
  music: { types: "MusicAlbum", title: "Music", blurb: "Albums in Jellyfin. They play in the bar at the bottom while you look around." },
  artists: { types: "MusicArtist", title: "Artists", blurb: "Everyone in your music library." },
  books: { types: "Book,AudioBook", title: "Books", blurb: "Books to read here and audiobooks to listen to." },
};
const JF_SORTS = [["SortName", "A–Z"], ["DateCreated", "Recently added"], ["ProductionYear", "Year"], ["CommunityRating", "Rating"], ["Random", "Shuffle"]];
const JF_FILTERS = [["all", "All"], ["IsUnplayed", "New to you"], ["IsResumable", "In progress"], ["IsFavorite", "Favourites"]];
const SQUARE = new Set(["MusicAlbum", "Audio", "AudioBook", "MusicArtist", "Playlist"]);
const VIDEO = new Set(["Movie", "Episode", "Video", "MusicVideo"]);
const PAGE = 60;
let jfStatus = null, jfViews = null;
const libraryOpen = () => !(ME?.sections && ME.sections.library === false);

async function jfReady(t) {
  if (!jfStatus?.connected || !jfStatus.user) jfStatus = await api("/api/jf/status").catch((err) => ({ error: err.message }));
  if (jfStatus.connected && jfStatus.user) return true;
  const why = jfStatus.error || (jfStatus.configured ? "Parlor can't reach Jellyfin right now." : "Jellyfin isn't set up yet.");
  paint(t, `<section class="home">${aurora}<h1>Library</h1><p>${esc(why)}</p>
    ${ME?.admin && !jfStatus.configured ? `<p>Add Jellyfin's address and an API key in <a href="#/settings">Settings</a>; Parlor's own stack does it for you.</p>` : ""}
    <div class="actions" style="margin-top:22px"><button class="ghost" onclick="jfStatus=null;route()">Try again</button></div></section>`);
  return false;
}

/* ---------- cards ---------- */

function jhref(x, play = false) {
  if (x.type === "CollectionFolder" || x.type === "UserView") return `#/jf/view/${esc(x.id)}`;
  if (play && VIDEO.has(x.type)) return `#/jf/play/${esc(x.id)}`;
  if (x.type === "Audio" && x.album_id) return `#/jf/item/${esc(x.album_id)}`;
  return `#/jf/item/${esc(x.id)}`;
}
const epCode = (x) => (x.season != null && x.episode != null ? `S${x.season}:E${x.episode}` : x.episode != null ? `E${x.episode}` : "");
const left = (x) => (x.runtime && x.position && !x.played ? `${clock(Math.max(0, x.runtime - x.position))} left` : "");
function jsub(x, wide) {
  if (x.type === "Episode") return [wide ? epCode(x) + " · " + x.name : epCode(x), left(x)].filter(Boolean).join(" · ");
  if (x.type === "MusicAlbum" || x.type === "Audio" || x.type === "AudioBook") return [(x.artists || []).join(", "), x.year].filter(Boolean).join(" · ");
  if (x.type === "MusicArtist") return "Artist";
  return [x.year, left(x) || (x.runtime && x.type === "Movie" ? clock(x.runtime) : "")].filter(Boolean).join(" · ");
}
function jcard(x, { wide = false, play = false } = {}) {
  const shape = wide ? "wide" : x.type === "MusicArtist" ? "round" : SQUARE.has(x.type) ? "square" : x.type === "Episode" ? "wide" : "tall";
  const art = shape === "wide" ? x.backdrop || x.image : x.image;
  const href = jhref(x, play || wide);
  const done = x.runtime && x.position && !x.played ? Math.min(1, x.position / x.runtime) : 0;
  return `<article class="jcard ${shape}">
    <a class="jart" href="${href}" tabindex="-1" aria-hidden="true">${art ? `<img src="${esc(art)}" alt="" loading="lazy">` : `<span class="noart">${esc(x.name)}</span>`}
      ${x.played ? `<span class="seen" title="Played">${ICON.ok}</span>` : ""}
      ${done > 0.004 ? `<span class="meter"><i style="width:${(100 * done).toFixed(1)}%"></i></span>` : ""}
      ${VIDEO.has(x.type) || SQUARE.has(x.type) && x.type !== "MusicArtist" ? `<span class="jgo">${ICON.play}</span>` : ""}</a>
    <a class="t" href="${href}">${esc(wide && x.type === "Episode" ? x.series || x.name : x.name)}</a>
    <span class="s">${esc(jsub(x, wide))}</span></article>`;
}
const jstrip = (items, opts) => (items?.length ? `<div class="jstrip${opts?.wide ? " wide" : ""}">${items.map((x) => jcard(x, opts)).join("")}</div>` : "");
const jgrid = (items) => items.map((x) => jcard(x)).join("");
/** a choice that opens another address: one small menu where a row of tabs would be */
const pick = (label, options, now) => `<label class="pick"><span>${label}</span><select data-go aria-label="${label}">${
  options.map(([href, text]) => `<option value="${esc(href)}"${href === now ? " selected" : ""}>${esc(text)}</option>`).join("")}</select></label>`;
document.addEventListener("change", (e) => {
  const s = e.target.closest("select[data-go]");
  if (!s) return;
  go(s.value);
  // the page is drawn again with a new menu: the arrow keys change a closed menu, so the focus goes back on it
  // (it waits as long as a slow list may take: four seconds lost the focus on a busy server)
  const o = new MutationObserver(() => {
    const menu = document.activeElement === document.body && $(`select[aria-label="${s.getAttribute("aria-label")}"]`);
    if (menu && menu !== s) { menu.focus(); o.disconnect(); }
  });
  o.observe(stage, { childList: true, subtree: true });
  setTimeout(() => o.disconnect(), 30000);
});

/* ---------- home: carry on, then what's new in each library ---------- */

async function showJfHome(t) {
  const [views, resume, next] = await Promise.all([api("/api/jf/views"), api("/api/jf/resume?limit=16").catch(() => []), api("/api/jf/nextup?limit=16").catch(() => [])]);
  jfViews = views;
  const shown = views.filter((v) => v.collection !== "livetv" && v.collection !== "playlists");
  const latest = await Promise.all(shown.map((v) => api(`/api/jf/latest?parent=${v.id}&limit=16`).catch(() => [])));
  if (t !== ticket) return;
  const nextIds = new Set(resume.map((x) => x.id));
  const upNext = next.filter((x) => !nextIds.has(x.id));
  paint(t, `<section class="home">${aurora}<h1>Library</h1>
      <p>Everything in Jellyfin, played right here${jfStatus?.user ? `, as ${esc(jfStatus.user.name)} in Jellyfin` : ""}: where you stop is where you start, on any device.</p>
      ${subnav()}</section>
    <div class="wrap">
      ${shelf("Continue watching", jstrip(resume, { wide: true }))}
      ${shelf("Next up", jstrip(upNext, { wide: true }))}
      ${shown.map((v, i) => shelf(`New in ${v.name}`, jstrip(latest[i]), `#/jf/view/${esc(v.id)}`)).join("")}
      ${shown.length ? shelf("Your libraries", `<div class="jstrip wide">${shown.map((v) => `<article class="jcard wide view">
          <a class="jart" href="#/jf/view/${esc(v.id)}">${v.image ? `<img src="${esc(v.image)}" alt="" loading="lazy">` : ""}<span class="vname">${esc(v.name)}</span></a></article>`).join("")}</div>`)
        : `<div class="empty-state"><strong>Jellyfin has no libraries yet</strong>${ME?.admin ? "Add one in Jellyfin's dashboard (Parlor's stack makes Films, Shows and Music for you)." : "Ask an admin to add one."}</div>`}
    </div>`);
}

/* ---------- a whole library, or every film / show / album / book ---------- */

async function showJfBrowse(t, { title, blurb, query, base, chip }, [sort, filter]) {
  if (!JF_SORTS.some(([k]) => k === sort)) sort = "SortName";  // an address with an order or a filter there isn't: the usual one
  if (!JF_FILTERS.some(([k]) => k === filter)) filter = "all";
  const order = ["DateCreated", "ProductionYear", "CommunityRating"].includes(sort) ? "Descending" : "Ascending";
  const url = (start) => `/api/jf/items?${query}&sort=${encodeURIComponent(sort)}&order=${order}&start=${start}&limit=${PAGE}`
    + (filter !== "all" ? `&filters=${encodeURIComponent(filter)}` : "");
  const r = await api(url(0));
  if (t !== ticket) return;
  const link = (s, f) => `${base}/${s}/${f}`;
  const by = { music: ["#/jf/music", "#/jf/artists"], artists: ["#/jf/music", "#/jf/artists"] }[chip];  // the music, by album or by artist
  paint(t, `<section class="home">${aurora}<h1>${esc(title)}</h1><p>${esc(blurb)} ${r.total ? `${compact.format(r.total)} in all.` : ""}</p>
      ${chip !== undefined ? subnav() : ""}
      <div class="picks">${by ? pick("By", [[by[0], "Album"], [by[1], "Artist"]], by[+(chip === "artists")]) : ""}
        ${pick("Sort", JF_SORTS.map(([k, label]) => [link(k, filter), label]), link(sort, filter))}
        ${pick("Show", JF_FILTERS.map(([k, label]) => [link(sort, k), label]), link(sort, filter))}</div></section>
    <div class="wrap">${r.items.length ? `<div class="jgrid" id="jfMore">${jgrid(r.items)}</div><div class="sentinel" id="jfSentinel">${r.total > PAGE ? SPIN : ""}</div>`
      : `<div class="empty-state"><strong>Nothing here</strong>${filter !== "all" ? `Try <a href="${esc(link(sort, "all"))}">All</a>.` : "Jellyfin hasn't found anything for this yet."}</div>`}</div>`);
  let start = r.items.length, busy = false;
  if (start >= r.total) return;
  const io = new IntersectionObserver(async ([e]) => { // the next page as you reach the bottom
    if (!e.isIntersecting || busy || t !== ticket) return;
    busy = true;
    const more = await api(url(start)).catch((err) => { toast(err.message); return { items: [] }; });
    if (t !== ticket) return io.disconnect();
    $("#jfMore").insertAdjacentHTML("beforeend", jgrid(more.items));
    start += more.items.length;
    busy = false;
    if (!more.items.length || start >= r.total) { io.disconnect(); $("#jfSentinel").innerHTML = ""; }
  }, { rootMargin: "800px" });
  io.observe($("#jfSentinel"));
}

/* ---------- one item ---------- */

const jmeta = (x) => [esc(x.year || ""), x.runtime && VIDEO.has(x.type) ? esc(runtimeText(x.runtime)) : "", esc(x.certification || ""),
  Number.isFinite(x.rating) ? `<span class="star">★</span> ${esc(x.rating.toFixed(1))}` : "", esc((x.genres || []).slice(0, 3).join(", "))]
  .filter(Boolean).map((m) => `<span>${m}</span>`).join("");
const runtimeText = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : s >= 60 ? `${Math.round(s / 60)}m` : `${Math.round(s)}s`);
const toggles = (x, [yes, ask] = ["Watched", "Mark watched"]) => `<button class="ghost${x.played ? " on" : ""}" data-jf-played="${esc(x.id)}" data-on="${x.played ? 1 : 0}">${ICON.ok}<span>${x.played ? yes : ask}</span></button>
  <button class="ghost icon${x.favourite ? " on" : ""}" data-jf-fav="${esc(x.id)}" data-on="${x.favourite ? 1 : 0}" aria-label="${x.favourite ? "Remove from favourites" : "Add to favourites"}" title="Favourite">${ICON.heart}</button>`;
function playButtons(x) {
  if (x.position && !x.played) {
    return `<a class="primary" href="#/jf/play/${esc(x.id)}">${ICON.play}Resume from ${clock(x.position)}</a><a class="ghost" href="#/jf/play/${esc(x.id)}/start">From the start</a>${tvButton("data-cast-jf", x.id)}`;
  }
  return `<a class="primary" href="#/jf/play/${esc(x.id)}">${ICON.play}Play</a>${tvButton("data-cast-jf", x.id)}`;
}
const jcredits = (people) => (people?.length ? `<div class="credits">${people.slice(0, 14).map((p) => `<span><b>${esc(p.name)}</b>${p.role ? ` ${esc(p.role)}` : ""}</span>`).join("")}</div>` : "");

function episodeRows(eps, current) {
  return `<ol class="eps">${eps.map((e) => {
    const done = e.runtime && e.position && !e.played ? Math.min(1, e.position / e.runtime) : 0;
    return `<li${e.id === current ? ' class="now"' : ""}><a href="#/jf/play/${esc(e.id)}">
      <span class="ethumb">${e.image ? `<img src="${esc(e.image)}" alt="" loading="lazy">` : ""}<span class="jgo">${ICON.play}</span>
        ${done > 0.004 ? `<span class="meter"><i style="width:${(100 * done).toFixed(1)}%"></i></span>` : ""}</span>
      <span class="ewhat"><span class="et">${esc(e.episode != null ? `${e.episode}. ` : "")}${esc(e.name)}${e.played ? ` <span class="tick" title="Watched">${ICON.ok}</span>` : ""}</span>
        <span class="es">${esc([e.runtime ? runtimeText(e.runtime) : "", left(e)].filter(Boolean).join(" · "))}</span>
        ${e.overview ? `<span class="eo">${esc(e.overview)}</span>` : ""}</span></a></li>`;
  }).join("")}</ol>`;
}

const dockItem = (s, album) => ({ jf: s.id, title: s.name, by: (s.artists?.length ? s.artists : album?.artists || []).join(", "), album: s.album || album?.name,
  image: s.image || album?.image, href: `#/jf/item/${esc(s.album_id || album?.id || s.id)}`, runtime: s.runtime, keep: s.type === "AudioBook" });

function trackRows(tracks, album) {
  const many = tracks.some((s) => s.artists?.join() !== album.artists?.join());
  return `<ol class="rows jtracks" data-album="${esc(album.id)}">${tracks.map((s, i) => `<li class="row${dock.queue[dock.at]?.jf === s.id ? " now" : ""}" data-track="${i}" data-jf="${esc(s.id)}">
    <span class="num"><span class="n">${esc(s.episode ?? i + 1)}</span><span class="eq" aria-hidden="true"><i></i><i></i><i></i></span></span>
    <span class="what"><button class="title linkish" data-track-play="${i}">${esc(s.name)}</button>${many ? `<span class="by">${esc((s.artists || []).join(", "))}</span>` : ""}</span>
    <span class="alb"></span><span class="dur">${clock(s.runtime)}</span>
    <button class="icon-btn play" data-track-play="${i}" aria-label="Play ${esc(s.name)}">${ICON.play}</button>
    <button class="icon-btn" data-track-more="${i}" aria-label="More for ${esc(s.name)}" aria-haspopup="menu" aria-expanded="false">${ICON.more}</button></li>`).join("")}</ol>`;
}

let jfShown = null; // the film or episode on the page: what Save on this device saves
/** once saved (or saving) it's a link to Saved for offline, where it's removed */
const saveButton = (x) => (offMine().some((e) => e.jf === x.id && !e.error)
  ? `<a class="ghost" href="#/offline">${ICON.dl}<span>${offFind({ jf: x.id }) ? "Saved on this device" : "Saving on this device"}</span></a>`
  : `<button class="ghost" data-jf-save="${esc(x.id)}">${ICON.dl}<span>Save on this device</span></button>`);

async function showJfItem(t, [id, , ...order]) {
  const x = await api(`/api/jf/item/${encodeURIComponent(id)}`);
  if (t !== ticket) return;
  jfShown = x;
  const similar = x.similar?.length ? shelf("More like this", jstrip(x.similar)) : "";
  if (x.type === "Movie" || x.type === "Video" || x.type === "MusicVideo") {
    return paint(t, hero({ kind: x.type === "Movie" ? "Film" : "Video", title: x.name, image: x.image, poster: true, backdrop: x.backdrop, meta: jmeta(x),
      blurb: x.overview, action: playButtons(x) + saveButton(x) + toggles(x) }) + `<div class="wrap">${x.people?.length ? `<h2>Cast and crew</h2>${jcredits(x.people)}` : ""}${similar}</div>`);
  }
  if (x.type === "Series") {
    const n = x.next_up;
    const action = (n ? `<a class="primary" href="#/jf/play/${esc(n.id)}">${ICON.play}${n.position ? "Resume" : "Play"} ${esc(epCode(n))}</a>` : "") + toggles(x);
    return paint(t, hero({ kind: "Show", title: x.name, image: x.image, poster: true, backdrop: x.backdrop, meta: jmeta(x), blurb: x.overview, action }) +
      `<div class="wrap">${n ? `<h2>Next up</h2>${episodeRows([n])}` : ""}${shelf("Seasons", jstrip(x.seasons))}${x.people?.length ? `<h2>Cast and crew</h2>${jcredits(x.people)}` : ""}${similar}</div>`);
  }
  if (x.type === "Season") {
    const next = x.episodes.find((e) => !e.played) || x.episodes[0];
    return paint(t, hero({ kind: "Season", title: x.name, image: x.image, poster: true, backdrop: x.backdrop,
      meta: `<span><a href="#/jf/item/${esc(x.series_id)}">${esc(x.series || "The show")}</a></span><span>${plural(x.episodes.length, "episode")}</span>`, blurb: x.overview,
      action: (next ? `<a class="primary" href="#/jf/play/${esc(next.id)}">${ICON.play}${next.position ? "Resume" : "Play"} ${esc(epCode(next))}</a>` : "") + toggles(x) }) +
      `<div class="wrap"><h2>Episodes</h2>${episodeRows(x.episodes)}</div>`);
  }
  if (x.type === "Episode") {
    const season = x.season_id ? await api(`/api/jf/item/${x.season_id}`).catch(() => null) : null;
    if (t !== ticket) return;
    return paint(t, hero({ kind: `${x.series || ""} · ${epCode(x)}`, title: x.name, image: x.image, backdrop: x.backdrop, meta: jmeta(x), blurb: x.overview,
      action: playButtons(x) + saveButton(x) + toggles(x) + (x.series_id ? `<a class="ghost" href="#/jf/item/${esc(x.series_id)}">The show</a>` : "") }) +
      `<div class="wrap">${season?.episodes?.length ? `<h2>${esc(season.name)}</h2>${episodeRows(season.episodes, x.id)}` : ""}</div>`);
  }
  if (x.type === "MusicAlbum" || x.type === "Playlist") {
    const tracks = x.tracks || [];
    const length = tracks.reduce((n, s) => n + (s.runtime || 0), 0);
    jfAlbum = { album: x, tracks };
    return paint(t, hero({ kind: "Album", title: x.name, image: x.image,
      meta: [`<span>${esc((x.artists || []).join(", "))}</span>`, x.year ? `<span>${x.year}</span>` : "", `<span>${plural(tracks.length, "song")}, ${Math.round(length / 60)} min</span>`].join(""),
      action: tracks.length ? `<button class="primary" data-album-play>${ICON.play}Play</button><button class="ghost" data-album-shuffle>${ICON.shuffle}Shuffle</button>` +
        toggles(x, ["Played", "Mark played"]) + `<button class="icon-btn ghost-round" data-album-more aria-label="Play next or add to the queue" aria-haspopup="menu" aria-expanded="false">${ICON.more}</button>` : "" }) +
      `<div class="wrap">${tracks.length ? trackRows(tracks, x) : `<div class="empty-state"><strong>No songs in this album</strong></div>`}</div>`);
  }
  if (x.type === "MusicArtist") {
    return paint(t, hero({ kind: "Artist", title: x.name, image: x.image, round: true, meta: `<span>${plural((x.albums || []).length, "album")}</span>`, blurb: x.overview, action: "" }) +
      `<div class="wrap">${x.albums?.length ? `<h2>Albums</h2><div class="jgrid">${jgrid(x.albums)}</div>` : ""}</div>`);
  }
  if (x.type === "Audio") return go(x.album_id ? `#/jf/item/${x.album_id}` : "#/jf/music");
  if (x.type === "Book") {
    const at = pref(`book:${x.id}:pct`);
    return paint(t, hero({ kind: "Book", title: x.name, image: x.image, poster: true, meta: jmeta(x), blurb: x.overview,
      action: `<a class="primary" href="#/jf/read/${esc(x.id)}">${ICON.book}${at ? `Carry on reading (${at}%)` : "Read"}</a>` + toggles(x, ["Read", "Mark read"]) }));
  }
  if (x.type === "AudioBook") {
    jfAlbum = { album: x, tracks: [x] };
    return paint(t, hero({ kind: "Audiobook", title: x.name, image: x.image, meta: jmeta(x) + (x.runtime ? `<span>${runtimeText(x.runtime)}</span>` : ""), blurb: x.overview,
      action: `<button class="primary" data-album-play>${ICON.play}${x.position && !x.played ? `Carry on from ${clock(x.position)}` : "Listen"}</button>` + toggles(x, ["Finished", "Mark finished"]) }));
  }
  // a collection, folder or anything else with children: show what's inside
  return showJfBrowse(t, { title: x.name, blurb: x.overview || "", query: `parent=${encodeURIComponent(x.id)}`, base: `#/jf/item/${x.id}/x` }, order);
}
let jfAlbum = null;

document.addEventListener("click", async (e) => {
  const played = e.target.closest("[data-jf-played]"), fav = e.target.closest("[data-jf-fav]");
  const b = played || fav;
  if (b) {
    b.disabled = true;
    const body = played ? { id: b.dataset.jfPlayed, played: b.dataset.on !== "1" } : { id: b.dataset.jfFav, favourite: b.dataset.on !== "1" };
    post("/api/jf/progress", body).then(() => {
      if (!location.hash.startsWith("#/jf/play/")) { keepScroll = true; return route(); }
      const on = b.dataset.on !== "1";
      b.dataset.on = on ? "1" : "0";
      b.classList.toggle("on", on);
      b.disabled = false;
      if (played) {
        $("span", b).textContent = on ? "Watched" : "Mark watched";
        if (jfp?.id === body.id) jfp.marked = on; // watched: no more position saves for it
      }
    }, (err) => { b.disabled = false; toast(err.message); });
    return;
  }
  const save = e.target.closest("[data-jf-save]");
  if (save && jfShown?.id === save.dataset.jfSave) {
    const x = jfShown, ep = x.type === "Episode", span = $("span", save), was = span.textContent;
    span.textContent = "Saving…";
    await offSave([{ jf: x.id, title: ep ? `${x.series || ""} · ${epCode(x)}` : x.name, by: ep ? x.name : String(x.year || ""), image: x.image, runtime: x.runtime, href: `#/jf/item/${x.id}`,
      film: { id: x.id, type: x.type, name: x.name, series: x.series, series_id: x.series_id, season: x.season, episode: x.episode, year: x.year, runtime: x.runtime, overview: x.overview } }], "video");
    if (save.isConnected) { if (offMine().some((e) => e.jf === x.id && !e.error)) save.outerHTML = saveButton(x); else span.textContent = was; } // refused: the button stays
    return;
  }
  if (!jfAlbum) return;
  const one = e.target.closest("[data-track-play]"), more = e.target.closest("[data-track-more], [data-album-more]");
  const queue = () => jfAlbum.tracks.map((s) => dockItem(s, jfAlbum.album));
  if (one) dockPlay(queue(), +one.dataset.trackPlay);
  else if (more) queueMenu(more, () => (more.dataset.trackMore ? [queue()[+more.dataset.trackMore]] : queue()));
  else if (e.target.closest("[data-album-play]")) dockPlay(queue(), 0, false);
  else if (e.target.closest("[data-album-shuffle]")) dockPlay(queue(), Math.floor(Math.random() * jfAlbum.tracks.length), true);
});
// the album you're looking at marks the song that's playing
dockOn("play", () => {
  const id = dock.queue[dock.at]?.jf;
  document.querySelectorAll(".jtracks .row[data-jf]").forEach((r) => r.classList.toggle("now", r.dataset.jf === id)); // the music folder's rows are music.js's
});

/* ---------- the video player ---------- */

/* where you got to (or that you finished) with no server to hear it is kept on this device, one per film, and sent when the server answers */
const jfPendKey = (id) => `${offWho()} ${id}`;
function jfPending() { try { return JSON.parse(localStorage.getItem("parlor:jfpending")) || {}; } catch { return {}; } }
function jfSend(body) {
  return post("/api/jf/progress", body).catch((err) => {
    if (!err.offline) return;
    try { localStorage.setItem("parlor:jfpending", JSON.stringify({ ...jfPending(), [jfPendKey(body.id)]: { ...jfPending()[jfPendKey(body.id)], ...body } })); } catch { /* storage full */ }
  });
}
async function jfFlush() {
  if (!ME) return;
  for (const [k, body] of Object.entries(jfPending())) {
    if (!k.startsWith(`${offWho()} `)) continue; // someone else's on this device: theirs to send
    try { await post("/api/jf/progress", body); } catch (err) { if (![404, 422].includes(err.status)) return; } // kept for the next time unless Jellyfin says it can never take it (the film's gone)
    const left = jfPending();
    if (JSON.stringify(left[k]) === JSON.stringify(body)) delete left[k]; // a newer place made meanwhile stays
    try { localStorage.setItem("parlor:jfpending", JSON.stringify(left)); } catch { /* storage full */ }
  }
}

let jfp = null;
function stopJf() {
  if (!jfp) return;
  const s = jfp;
  jfp = null;
  s.save();
  s.timers.forEach(clearInterval);
  clearTimeout(s.countdown);
  document.removeEventListener("keydown", s.keys);
  removeEventListener("pagehide", s.onHide);
  s.hls?.destroy();
  s.video.pause();
  s.video.removeAttribute("src");
  s.video.load(); // stops the download, and tells Jellyfin to stop converting
  dockKeys();
}
addEventListener("hashchange", () => { if (!location.hash.startsWith("#/jf/play/")) stopJf(); });

async function attach(video, url, at, s) {
  s.hls?.destroy();
  s.hls = null;
  [...video.querySelectorAll("track")].forEach((tr) => tr.remove());
  if (/\.m3u8(\?|$)/.test(url) && !video.canPlayType("application/vnd.apple.mpegurl")) {
    await loadScript("vendor/hls.light.min.js");
    if (s !== jfp) return;
    if (window.Hls?.isSupported()) {
      s.hls = new Hls({ startPosition: at || -1, maxBufferLength: 30 });
      s.hls.on(Hls.Events.ERROR, (_, d) => { if (d.fatal) s.fail(d.details || "the stream stopped"); });
      s.hls.loadSource(url);
      s.hls.attachMedia(video);
      return;
    }
  }
  video.src = url + (at && !/\.m3u8/.test(url) ? `#t=${at}` : "");
}

// mpv closed (the desktop app): leave the "Playing in mpv" page for where the person came from, which shows the new place
// mpv never played it (a file it can't open, a server it can't reach): this window's player tries instead
let mpvDone = ""; // where the "Playing in mpv" page leads when mpv closes: the film's or the show's page
if (DESKTOP) parlorDesktop.onClosed(({ id, position }) => {
  if (location.hash.startsWith(`#/jf/play/${id}`) && !location.hash.endsWith("/here") && $(".mpvnote")) location.replace(position == null ? `#/jf/play/${id}/here` : mpvDone);
});

async function showJfPlay(t, [id, from]) {
  stopJf();
  const saved = offFind({ jf: id }); // this device's copy, if it has one: it plays that first
  const x = await api(`/api/jf/item/${encodeURIComponent(id)}`).catch((err) => { if (saved?.film) return saved.film; throw err; });
  const season = x.type === "Episode" && x.season_id ? await api(`/api/jf/item/${x.season_id}`).catch(() => null) : null;
  if (t !== ticket) return;
  const eps = season?.episodes || [];
  const next = eps[eps.findIndex((e) => e.id === x.id) + 1] || null;
  const title = x.type === "Episode" ? `${x.series || ""} · ${epCode(x)}` : x.name;
  // the desktop app: mpv plays the original file with the computer's own decoder. No mpv, or a copy saved here: this player
  if (DESKTOP && from === "here") await parlorDesktop.stop().catch(() => {});
  if (DESKTOP && !saved && from !== "here" && (await parlorDesktop.play({ id: x.id, position: from === "start" ? 0 : x.position || 0, title }).catch(() => ({}))).ok) {
    if (t !== ticket) return;
    mpvDone = `#/jf/item/${x.type === "Episode" && x.series_id ? x.series_id : x.id}`;
    return paint(t, `<div class="jwatch">
      <div class="player" id="jfBox" style="${esc(`--photo:${cssUrl(x.backdrop || x.image)}`)}"><div class="overlay"><div class="mpvnote">
        <strong>Playing in mpv</strong><span>Its place is saved as it plays.</span><a class="ghost" href="#/jf/play/${esc(x.id)}/here">Play here instead</a></div></div></div>
      <div class="vinfo"><h1>${esc(title)}</h1>${x.overview ? `<p class="jover">${esc(x.overview)}</p>` : ""}
        <div class="actions">${next ? `<a class="ghost" href="#/jf/play/${esc(next.id)}/start">${ICON.next}Next episode</a>` : ""}<a class="ghost" href="#/jf/item/${esc(x.type === "Episode" && x.series_id ? x.series_id : x.id)}">Details</a></div></div>
      ${eps.length ? `<div class="wrap0"><h2>${esc(season.name)}</h2>${episodeRows(eps, x.id)}</div>` : ""}
    </div>`);
  }
  paint(t, `<div class="jwatch">
    <div class="player" id="jfBox" style="${esc(`--photo:${cssUrl(x.backdrop || x.image)}`)}">
      <video id="jfVideo" playsinline controls autoplay preload="auto"${x.backdrop ? ` poster="${esc(x.backdrop)}"` : ""}></video>
      <div class="overlay" id="jfOverlay">${SPIN}</div></div>
    <div class="ptools">
      <select id="jfAudio" aria-label="Audio" hidden></select>
      <select id="jfSubs" aria-label="Subtitles" hidden></select>
      <select id="jfQuality" aria-label="Quality"><option value="">Original quality</option><option value="20000000">20 Mbps</option><option value="8000000">8 Mbps</option><option value="4000000">4 Mbps</option><option value="1500000">1.5 Mbps</option></select>
      <select id="jfChapters" aria-label="Chapters" hidden></select>
      <span class="grow"></span><span class="stat" id="jfStat"></span>
      ${next ? `<a class="ghost" href="#/jf/play/${esc(next.id)}/start" title="Next episode (N)">${ICON.next}Next episode</a>` : ""}
      <button class="ghost icon" id="jfFull" title="Full screen (F)" aria-label="Full screen">${ICON.full}</button>
    </div>
    <div class="vinfo"><h1>${esc(title)}</h1>
      <p class="dim">${[x.type === "Episode" ? `<a href="#/jf/item/${esc(x.series_id)}">${esc(x.name)}</a>` : "", esc(x.year || ""), x.runtime ? runtimeText(x.runtime) : ""].filter(Boolean).join(" · ")}</p>
      ${x.overview ? `<p class="jover">${esc(x.overview)}</p>` : ""}
      <div class="actions">${toggles(x)}${x.type === "Episode" ? "" : `<a class="ghost" href="#/jf/item/${esc(x.id)}">Details</a>`}</div></div>
    ${eps.length ? `<div class="wrap0"><h2>${esc(season.name)}</h2>${episodeRows(eps, x.id)}</div>` : ""}
  </div>`);

  const video = $("#jfVideo"), box = $("#jfBox");
  const s = (jfp = { id, x, video, hls: null, saved: 0, marked: false, timers: [], p: null, tried: false });
  const hevc = !!video.canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"');
  const overlay = (html) => { $("#jfOverlay")?.remove(); if (html) box.insertAdjacentHTML("beforeend", `<div class="overlay" id="jfOverlay">${html}</div>`); };
  s.fail = (why) => {
    if (s !== jfp) return;
    if (s.p?.local && !s.failedLocal) { s.failedLocal = true; return load({ start: video.currentTime || s.p.position }); } // the device's copy won't play: ask Jellyfin
    if (s.p?.method === "direct" && !s.tried) { s.tried = true; return load({ transcode: true, start: video.currentTime || s.p.position }); } // the browser couldn't play the file: let Jellyfin convert it
    overlay(`<p><strong>This won't play</strong><br>${esc(why)}</p><button class="ghost" onclick="route()">Try again</button>`);
  };
  /** playback info made from the saved copy: the film, its subtitle files and where it got to (what's kept for Jellyfin, else what Jellyfin last said) */
  async function local(start) {
    const url = await offBlob(saved);
    if (!url) return null; // the browser cleared it
    const subtitles = [];
    for (const m of saved.subs || []) {
      const e = offList()[m.file], u = e?.done && (await offBlob(e));
      if (u) subtitles.push({ ...m, url: u });
    }
    const kept = jfPending()[jfPendKey(id)];
    return { method: "direct", local: true, url, subtitles, audio: [], chapters: [], runtime: saved.runtime, position: start ?? (kept ? kept.position || 0 : x.position || 0) };
  }
  async function load(opts = {}) {
    let p = !opts.transcode && saved && !s.failedLocal ? await local(opts.start ?? (from === "start" && !s.p ? 0 : undefined)) : null;
    if (!p) {
      try {
        p = await post(`/api/jf/playback/${encodeURIComponent(id)}`, { hevc, ...opts, ...(opts.start === undefined && from === "start" && !s.p ? { start: 0 } : {}) });
      } catch (err) { return s === jfp && overlay(`<p><strong>Jellyfin can't play this here</strong><br>${esc(err.message)}</p>`); }
    }
    if (s !== jfp) return;
    s.p = p;
    $("#jfQuality").disabled = !!p.local; // the device's copy has one quality; picking another would ask a server that may not be there
    $("#jfStat").textContent = p.local ? "Saved on this device" : p.method === "direct" ? "Original file" : "Converted by Jellyfin for this browser";
    const audio = $("#jfAudio"), subs = $("#jfSubs"), ch = $("#jfChapters");
    if (p.audio.length > 1 && !audio.options.length) {
      audio.innerHTML = p.audio.map((a) => `<option value="${a.index}"${a.default ? " selected" : ""}>${esc(a.title || a.language || `Track ${a.index}`)}</option>`).join("");
      audio.hidden = false;
    }
    const usable = p.subtitles.filter((x) => x.url);
    subs.innerHTML = `<option value="">Subtitles off</option>` + usable.map((x, i) => `<option value="${i}">${esc(x.title || x.language || `Subtitles ${i + 1}`)}</option>`).join("");
    subs.hidden = !usable.length;
    if (p.chapters.length > 1 && !ch.options.length) {
      ch.innerHTML = `<option value="">Chapters</option>` + p.chapters.map((c) => `<option value="${c.start}">${esc(clock(c.start))} · ${esc(c.name || "")}</option>`).join("");
      ch.hidden = false;
    }
    await attach(video, p.url, p.position, s);
    if (s !== jfp) return;
    usable.forEach((x, i) => video.insertAdjacentHTML("beforeend", `<track kind="subtitles" src="${esc(x.url)}" srclang="${esc(x.language || "und")}" label="${esc(x.title || x.language || "")}" data-i="${i}">`));
    const def = pref("subtitles") === false ? -1 : usable.findIndex((x) => x.default);
    subs.value = def >= 0 ? String(def) : "";
    showSubs();
    video.addEventListener("loadedmetadata", () => {
      overlay("");
      if (p.position > 1 && Math.abs(video.currentTime - p.position) > 2 && video.duration > p.position) video.currentTime = p.position;
      video.play().catch(() => {});
    }, { once: true });
  }
  const showSubs = () => [...video.textTracks].forEach((tr, i) => { tr.mode = String(i) === $("#jfSubs").value ? "showing" : "disabled"; });
  $("#jfSubs").addEventListener("change", (e) => { showSubs(); setPref("subtitles", e.target.value !== ""); });
  $("#jfAudio").addEventListener("change", (e) => load({ audio_index: +e.target.value, transcode: true, start: video.currentTime }));
  $("#jfQuality").addEventListener("change", (e) => load({ max_bitrate: +e.target.value || undefined, transcode: !!e.target.value, start: video.currentTime, audio_index: +$("#jfAudio").value || undefined }));
  $("#jfChapters").addEventListener("change", (e) => { if (e.target.value !== "") video.currentTime = +e.target.value; e.target.value = ""; });
  const full = () => (document.fullscreenElement ? document.exitFullscreen() : box.requestFullscreen?.().catch(() => {}));
  $("#jfFull").addEventListener("click", full);
  box.addEventListener("dblclick", full);

  s.save = (done = false) => {
    if (!s.p) return;
    const at = video.currentTime || 0, len = video.duration && isFinite(video.duration) ? video.duration : s.p.runtime;
    if (!s.marked && (done || (len && at / len > 0.92))) {
      s.marked = true; // watched: Jellyfin moves it out of Continue watching and on to the next episode
      jfSend({ id, played: true });
    } else if (!s.marked && at > 5 && Math.abs(at - s.saved) >= 3) {
      s.saved = at;
      jfSend({ id, position: Math.floor(at) });
    }
  };
  s.timers.push(setInterval(() => { if (!video.paused) s.save(); }, 10000));
  video.addEventListener("pause", () => s.save());
  video.addEventListener("seeked", () => { if (video.paused) s.save(); });
  video.addEventListener("error", () => s.fail(video.error?.message || "the browser can't play this file"));
  video.addEventListener("ended", () => {
    s.save(true);
    if (!next) return;
    let n = 8;
    overlay(`<p>Up next</p><strong>${esc(epCode(next))} · ${esc(next.name)}</strong><p class="count" id="jfCount">in ${n}</p>
      <div class="actions"><a class="primary" href="#/jf/play/${esc(next.id)}/start">${ICON.play}Play now</a><button class="ghost" id="jfStay">Stay here</button></div>`);
    $("#jfStay").addEventListener("click", () => { clearTimeout(s.countdown); overlay(""); });
    const tick = () => { if (s !== jfp) return; if (--n <= 0) return go(`#/jf/play/${next.id}/start`); $("#jfCount").textContent = `in ${n}`; s.countdown = setTimeout(tick, 1000); };
    s.countdown = setTimeout(tick, 1000);
  });
  s.keys = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
    const act = { " ": () => (video.paused ? video.play() : video.pause()), k: () => (video.paused ? video.play() : video.pause()),
      ArrowLeft: () => (video.currentTime -= 10), ArrowRight: () => (video.currentTime += 10), j: () => (video.currentTime -= 10), l: () => (video.currentTime += 10),
      f: full, m: () => { video.muted = !video.muted; }, N: () => next && go(`#/jf/play/${next.id}/start`) }[e.key];
    if (act && !(e.key === " " && document.activeElement === video)) { e.preventDefault(); act(); }
  };
  document.addEventListener("keydown", s.keys);
  addEventListener("pagehide", (s.onHide = () => s.save()));
  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title, artist: x.type === "Episode" ? x.name : x.year ? String(x.year) : "",
      artwork: x.image ? [{ src: new URL(x.image, location.href).href, sizes: "600x900" }] : [] });
    const ms = navigator.mediaSession;
    ms.setActionHandler("play", () => video.play());
    ms.setActionHandler("pause", () => video.pause());
    ms.setActionHandler("seekbackward", () => (video.currentTime -= 10));
    ms.setActionHandler("seekforward", () => (video.currentTime += 10));
    ms.setActionHandler("nexttrack", next ? () => go(`#/jf/play/${next.id}/start`) : null);
    ms.setActionHandler("previoustrack", null);
  }
  load();
}

/* ---------- the reader: EPUB with epub.js, PDF in the browser's own viewer ---------- */

let jfBook = null, jfBookKeys = null, jfBlob = null;
function closeBook() {
  jfBook?.destroy();
  document.removeEventListener("keyup", jfBookKeys);
  if (jfBlob) URL.revokeObjectURL(jfBlob);
  jfBook = jfBookKeys = jfBlob = null;
}
async function showJfRead(t, [id]) {
  closeBook();
  const x = await api(`/api/jf/item/${encodeURIComponent(id)}`);
  if (t !== ticket) return;
  paint(t, `<div class="reader" id="reader">
    <header class="rbar"><a class="ghost icon" href="#/jf/item/${esc(x.id)}" aria-label="Back to the book">${ICON.x}</a>
      <strong class="rtitle">${esc(x.name)}</strong><span class="grow"></span>
      <select id="rToc" aria-label="Contents" hidden></select>
      <button class="ghost icon" id="rSmaller" aria-label="Smaller text">A−</button><button class="ghost icon" id="rBigger" aria-label="Larger text">A+</button>
      <span class="stat" id="rAt"></span></header>
    <div class="rstage"><button class="rturn prev" id="rPrev" aria-label="Previous page">‹</button><div class="rpage" id="rPage"><div class="note">${SPIN}<p>Opening the book…</p></div></div><button class="rturn next" id="rNext" aria-label="Next page">›</button></div>
    <div class="rline"><i id="rLine"></i></div></div>`);
  const r = await fetch(`/api/jf/stream/Items/${encodeURIComponent(id)}/Download`, { credentials: "same-origin" });
  if (!r.ok) {
    const why = r.status === 403 ? "Your Jellyfin account isn't allowed to download, and reading here needs that. Ask an admin." : `Jellyfin answered ${r.status}.`;
    return paint(t, `<div class="note"><strong>Couldn't open this book</strong>${esc(why)}</div>`);
  }
  const buf = await r.arrayBuffer();
  if (t !== ticket) return;
  const head = new TextDecoder().decode(new Uint8Array(buf, 0, Math.min(5, buf.byteLength)));
  const page = $("#rPage");
  if (head.startsWith("%PDF")) {
    $("#reader").classList.add("pdf");
    const url = (jfBlob = URL.createObjectURL(new Blob([buf], { type: "application/pdf" })));
    page.innerHTML = `<iframe title="${esc(x.name)}" src="${url}"></iframe>`;
    post("/api/jf/progress", { id: x.id, position: 1 }).catch(() => {});
    return;
  }
  if (!head.startsWith("PK")) {
    return paint(t, `<div class="note"><strong>Parlor reads EPUB and PDF books</strong>This one is in another format; open it in Jellyfin or a reader app.</div>`);
  }
  await loadScript("vendor/jszip.min.js");
  await loadScript("vendor/epub.min.js");
  if (t !== ticket) return;
  page.innerHTML = "";
  const book = (jfBook = ePub(buf));
  const rendition = book.renderTo(page, { width: "100%", height: "100%", flow: "paginated", spread: "auto", allowScriptedContent: false });
  const css = getComputedStyle(document.body);
  const bg = css.getPropertyValue("--bg").trim();
  rendition.themes.default({ html: { background: bg, "color-scheme": css.getPropertyValue("color-scheme").trim() || "dark" },
    body: { color: css.getPropertyValue("--text").trim(), background: bg, "font-family": "Georgia, 'Iowan Old Style', serif", "line-height": "1.65" },
    a: { color: css.getPropertyValue("--link").trim() }, "img, svg": { "max-width": "100%" } });
  let size = pref("bookSize") || 100;
  rendition.themes.fontSize(`${size}%`);
  const resize = (d) => { size = Math.min(180, Math.max(70, size + d)); setPref("bookSize", size); rendition.themes.fontSize(`${size}%`); };
  $("#rSmaller").addEventListener("click", () => resize(-10));
  $("#rBigger").addEventListener("click", () => resize(10));
  $("#rPrev").addEventListener("click", () => rendition.prev());
  $("#rNext").addEventListener("click", () => rendition.next());
  const keys = (e) => { if (e.key === "ArrowLeft") rendition.prev(); if (e.key === "ArrowRight" || e.key === " ") rendition.next(); };
  document.addEventListener("keyup", (jfBookKeys = keys));
  rendition.on("keyup", keys);
  let x0 = null; // swipe to turn
  rendition.on("touchstart", (e) => { x0 = e.changedTouches[0].screenX; });
  rendition.on("touchend", (e) => { const dx = e.changedTouches[0].screenX - x0; if (Math.abs(dx) > 40) dx < 0 ? rendition.next() : rendition.prev(); });
  let finished = false;
  rendition.on("relocated", (loc) => {
    setPref(`book:${id}`, loc.start.cfi);
    const pct = book.locations.length() ? Math.round(100 * book.locations.percentageFromCfi(loc.start.cfi)) : null;
    if (pct != null) { setPref(`book:${id}:pct`, pct); $("#rAt").textContent = `${pct}%`; $("#rLine").style.width = `${pct}%`; }
    if (loc.atEnd && !finished) { finished = true; post("/api/jf/progress", { id: x.id, played: true }).catch(() => {}); }
  });
  book.loaded.navigation.then((nav) => {
    const flat = [];
    const walk = (items, depth) => items.forEach((i) => { flat.push([i.href, "  ".repeat(depth) + i.label.trim()]); walk(i.subitems || [], depth + 1); });
    walk(nav.toc, 0);
    if (!flat.length) return;
    const toc = $("#rToc");
    toc.innerHTML = `<option value="">Contents</option>` + flat.map(([href, label]) => `<option value="${esc(href)}">${esc(label)}</option>`).join("");
    toc.hidden = false;
    toc.addEventListener("change", () => { if (toc.value) rendition.display(toc.value); toc.value = ""; });
  });
  await rendition.display(pref(`book:${id}`) || undefined);
  book.ready.then(() => book.locations.generate(1200)).then(() => {
    const loc = rendition.currentLocation();
    if (loc?.start) rendition.emit("relocated", loc);
  }).catch(() => {});
}
addEventListener("hashchange", () => { if (!location.hash.startsWith("#/jf/read/")) closeBook(); });

/* ---------- search ---------- */

async function showJfSearch(t, q) {
  input.value = q;
  const r = await api(`/api/jf/items?search=${encodeURIComponent(q)}&types=Movie,Series,Episode,MusicAlbum,MusicArtist,Audio,Book,AudioBook&limit=120`);
  if (t !== ticket) return;
  const groups = [["Films", ["Movie"]], ["Shows", ["Series"]], ["Episodes", ["Episode"]], ["Artists", ["MusicArtist"]], ["Albums", ["MusicAlbum"]], ["Songs", ["Audio"]], ["Books", ["Book", "AudioBook"]]];
  const html = groups.map(([title, types]) => {
    const items = r.items.filter((x) => types.includes(x.type));
    return items.length ? shelf(title, `<div class="jgrid${types[0] === "Episode" ? " wide" : ""}">${jgrid(items)}</div>`) : "";
  }).join("");
  paint(t, html ? `<div class="wrap">${html}</div>` : `<div class="note"><strong>Nothing in your library matches “${esc(q)}”</strong>Request it in <a href="#/media/search/${encodeURIComponent(q)}">Films &amp; shows</a> or <a href="#/music/search/${encodeURIComponent(q)}">Music</a>.</div>`);
}

const JF_ID = /^[0-9a-f]{32}$/i;
page("jf", async (t, [what = "", ...rest]) => {
  if (["view", "item", "play", "read"].includes(what) && !JF_ID.test(rest[0] || "")) return paint(t, `<div class="note"><strong>Not here</strong>That isn't a link to something in your library.</div>`);
  if (what === "play" && document.body.classList.contains("offline") && offFind({ jf: rest[0] })?.film) return showJfPlay(t, rest); // no Jellyfin to ask: the device's copy
  if (!(await jfReady(t))) return;
  if (JF_KINDS[what]) {
    const k = JF_KINDS[what];
    return showJfBrowse(t, { title: k.title, blurb: k.blurb, query: `types=${k.types}`, base: `#/jf/${what}`, chip: what }, rest);
  }
  if (what === "view") {
    jfViews ||= await api("/api/jf/views").catch(() => []);
    const v = jfViews.find((x) => x.id === rest[0]);
    return showJfBrowse(t, { title: v?.name || "Library", blurb: "", query: `parent=${encodeURIComponent(rest[0])}`, base: `#/jf/view/${rest[0]}` }, rest.slice(1));
  }
  if (what === "item") return showJfItem(t, rest);
  if (what === "play") return showJfPlay(t, rest);
  if (what === "read") return showJfRead(t, rest);
  if (what === "search") return showJfSearch(t, rest.join("/"));
  return showJfHome(t);
}, "library");
