/* Music: music-fndr's pages, whole (from its static/index.html), under the Music section. */
const credits = (list) => list.map((a) => `<a href="#/artist/${esc(a.id)}">${esc(a.name)}</a>`).join(", ");
const creditText = (list) => list.map((a) => a.name).join(", ");
const KIND = { album: "Album", single: "Single", ep: "EP", compilation: "Compilation" };

/* ---------- building blocks ---------- */

const dlData = (item) => esc(JSON.stringify(item));
const dlButton = (item, label) =>
  `<button class="dl" data-dl="${dlData(item)}" aria-label="Download ${esc(label)}">${ICON.dl}</button>`;
const bigButton = (item, label) =>
  `<button class="primary" data-dl="${dlData(item)}">${ICON.dl}<span>${esc(label)}</span></button>`;
function trackRow(t, i, { art = false, album = true } = {}) {
  const item = { kind: "track", id: t.id, title: t.name, subtitle: creditText(t.artists), image: t.image };
  return `<li class="row">
    ${art ? img(t.image, "thumb") : `<span class="num">${i + 1}</span>`}
    <span class="what">
      <span class="title">${esc(t.name)}${t.explicit ? '<abbr class="e" title="Explicit">E</abbr>' : ""}</span>
      <span class="by">${credits(t.artists)}</span>
    </span>
    <span class="alb">${album && t.album?.name ? `<a href="#/album/${esc(t.album.id)}">${esc(t.album.name)}</a>` : ""}</span>
    <span class="dur">${mmss(t.ms)}</span>
    ${have.has(t.id)
      ? `<span class="have" role="img" aria-label="In your library" title="In your library">${ICON.ok}</span>`
      : dlButton(item, `${t.name} by ${item.subtitle}`)}
  </li>`;
}

function tile(kind, x, sub, downloadable = true) {
  const href = `#/${kind}/${esc(x.id)}`;
  const item = { kind, id: x.id, title: x.name, subtitle: sub, image: x.image };
  return `<article class="tile ${kind}">
    <div class="cover">
      <a href="${href}" tabindex="-1" aria-hidden="true">${img(x.image)}</a>
      ${downloadable ? dlButton(item, x.name) : ""}
    </div>
    <a class="name" href="${href}">${esc(x.name)}</a>
    <span class="sub">${esc(sub)}</span>
  </article>`;
}

const rows = (tracks, opts) => (tracks.length ? `<ol class="rows">${tracks.map((t, i) => trackRow(t, i, opts)).join("")}</ol>` : "");
/* ---------- pages ---------- */

async function showHome(t) {
  input.value = "";
  const intro = `<section class="home">${aurora}
    <h1>Find music for your library</h1>
    <p>Search for an artist, album, song or playlist, or paste a Spotify link. Everything you download goes straight into your music folder.</p>
    ${musicChips("discover")}
  </section>`;
  paint(t, intro + `<div class="wrap">${SPIN}</div>`);
  let feed;
  try { feed = await api("/music/api/home"); } catch { return paint(t, intro); }
  const fresh = feed.new.map((a) => tile("album", a, creditText(a.artists))).join("");
  const lists = feed.playlists.map((p) => tile("playlist", p, p.owner ? `By ${p.owner}` : "Playlist")).join("");
  paint(t, intro + `<div class="wrap">
    ${section("New this week", grid(fresh, "strip"))}
    ${section("Top 10 worldwide", rows(feed.chart, { art: true }))}
    ${section("Playlists", grid(lists))}
  </div>`);
}

async function showSearch(t, q) {
  input.value = q;
  const r = await api(`/music/api/search?q=${encodeURIComponent(q)}`);
  if (r.link) return openLink(t, r.link);
  const albums = r.albums.map((a) => tile("album", a, [a.year, creditText(a.artists)].filter(Boolean).join(", "))).join("");
  const artists = r.artists.map((a) => tile("artist", a, "Artist", false)).join("");
  const lists = r.playlists.map((p) => tile("playlist", p, p.owner ? `By ${p.owner}` : "Playlist")).join("");
  const html =
    section("Songs", rows(r.tracks.slice(0, 8), { art: true })) +
    section("Artists", grid(artists, "strip")) +
    section("Albums", grid(albums)) +
    section("Playlists", grid(lists));
  paint(t, html
    ? `<div class="wrap">${html}</div>`
    : `<div class="note"><strong>Nothing matches “${esc(q)}”</strong>Check the spelling, or try just the artist or album name.</div>`);
}

async function showAlbum(t, id) {
  const a = await api(`/music/api/album/${id}`);
  const kind = KIND[a.type] || "Album";
  const owned = a.tracks.filter((x) => have.has(x.id)).length;
  const meta = `<span>${credits(a.artists)}</span><span>${esc(a.year || "")}</span><span>${plural(a.total, "song")}, ${minutes(a.tracks)} min</span>` +
    (owned ? `<span>${owned === a.tracks.length ? "All" : owned} in your library</span>` : "");
  const item = { kind: "album", id: a.id, title: a.name, subtitle: creditText(a.artists), image: a.image };
  paint(t, hero({ kind, title: a.name, image: a.image, tintColor: a.color, meta, action: bigButton(item, `Download ${kind.toLowerCase()}`) }) +
    `<div class="wrap">${section("Songs", rows(a.tracks, { album: false }))}${a.label ? `<p class="note" style="padding:24px 0">${esc(a.label)}</p>` : ""}</div>`);
}

async function showPlaylist(t, id) {
  const p = await api(`/music/api/playlist/${id}`);
  const item = { kind: "playlist", id: p.id, title: p.name, subtitle: p.owner ? `By ${p.owner}` : "Playlist", image: p.image };
  const meta = `<span>${p.owner ? `By ${esc(p.owner)}` : ""}</span><span>${plural(p.total, "song")}</span>`;
  const more = p.total > p.tracks.length ? `<small>Showing the first ${p.tracks.length}; the download gets all ${p.total}.</small>` : "";
  paint(t, hero({ kind: "Playlist", title: p.name, image: p.image, tintColor: p.color, meta, blurb: plain(p.description), action: bigButton(item, "Download playlist") + more }) +
    `<div class="wrap">${section("Songs", rows(p.tracks, { art: true }))}</div>`);
}

async function showArtist(t, id) {
  const a = await api(`/music/api/artist/${id}`);
  const releases = a.albums.length + a.singles.length;
  const item = { kind: "artist", id: a.id, title: a.name, subtitle: "Discography", image: a.image };
  const action = releases
    ? bigButton(item, "Download discography") + `<small>${plural(a.albums.length, "album")} and ${plural(a.singles.length, "single")}</small>`
    : "";
  const meta = a.listeners ? `<span>${compact.format(a.listeners)} monthly listeners</span>` : "";
  const byYear = (r) => r.year || "";
  paint(t, hero({ kind: "Artist", title: a.name, image: a.image, round: true, tintColor: a.color, photo: a.header, meta, action }) +
    `<div class="wrap">
      ${section("Popular", rows(a.top, { art: true }))}
      ${section("Albums", grid(a.albums.map((r) => tile("album", r, byYear(r))).join("")))}
      ${section("Singles and EPs", grid(a.singles.map((r) => tile("album", r, [byYear(r), KIND[r.type]].filter(Boolean).join(", "))).join("")))}
      ${section("Compilations", grid(a.compilations.map((r) => tile("album", r, byYear(r))).join("")))}
    </div>`);
}

let have = new Set(); // Spotify ids of songs already in the music folder
let unorganized = 0; // songs not yet in Artist/Album folders
async function loadLibrary() {
  const lib = await api("/music/api/library");
  have = new Set(lib.songs.map((s) => s.id).filter(Boolean));
  unorganized = lib.unorganized;
  return lib.songs;
}

const coverUrl = (s) => `/music/api/library/cover?path=${encodeURIComponent(s.path)}`;
const libHref = (artist, album) => `#/library/${encodeURIComponent(artist)}/${encodeURIComponent(album)}`;

// artist -> albums -> songs, artists A to Z, newest albums first
function shelves(songs) {
  const artists = new Map();
  for (const s of songs) {
    if (!artists.has(s.album_artist)) artists.set(s.album_artist, new Map());
    const albums = artists.get(s.album_artist);
    if (!albums.has(s.album)) albums.set(s.album, { artist: s.album_artist, album: s.album, year: s.year, songs: [] });
    const a = albums.get(s.album);
    a.songs.push(s);
    a.year ||= s.year;
    if (!a.cover && s.art) a.cover = coverUrl(s);
  }
  return [...artists]
    .sort(([x], [y]) => x.localeCompare(y, undefined, { sensitivity: "base" }))
    .map(([name, albums]) => ({ name, albums: [...albums.values()].sort((x, y) => (y.year || 0) - (x.year || 0)) }));
}

function localTile(a) {
  const href = libHref(a.artist, a.album);
  return `<article class="tile">
    <div class="cover"><a href="${href}" tabindex="-1" aria-hidden="true">${img(a.cover)}</a></div>
    <a class="name" href="${href}">${esc(a.album || "No album tag")}</a>
    <span class="sub">${esc([a.year, plural(a.songs.length, "song")].filter(Boolean).join(", "))}</span>
  </article>`;
}

const tidyPanel = () => `<section class="tidy">
  <div class="head">
    <div><strong>Organize your music folder</strong>
      <p>${unorganized
        ? `${plural(unorganized, "song")} aren't in Artist / Album folders yet. Organize before using Get missing songs, or those songs download again.`
        : "Moves every song into Artist / Album folders, named the way new downloads are, so nothing gets downloaded twice."} Nothing is overwritten or deleted.</p></div>
    <button class="ghost" data-tidy="check">Check folder</button>
  </div>
  <div id="tidyResult" aria-live="polite"></div>
</section>`;

let librarySongs = [];
const completable = (name) => !/^(various artists|unknown artist)$/i.test(name);

// Find the artist on Spotify (from one of their songs here) and queue the
// discography: songs already in the folder come back "Already there".
async function completeArtist(name) {
  const song = librarySongs.find((s) => s.album_artist === name && s.id);
  const params = new URLSearchParams({ name, ...(song ? { track: song.id } : {}) });
  const artist = await api(`/music/api/library/artist?${params}`);
  await api("/music/api/downloads", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ kind: "artist", id: artist.id, title: artist.name, subtitle: "Missing songs", image: song && coverUrl(song) }),
  });
}

async function completeArtists(names, button) {
  button.disabled = true;
  let queued = 0;
  const missed = [];
  for (const name of names) {
    try { await completeArtist(name); queued++; } catch { missed.push(name); }
    if (names.length > 1) button.textContent = `Queued ${queued + missed.length} of ${names.length}…`;
  }
  button.textContent = names.length > 1 ? `Queued ${plural(queued, "artist")}` : "Queued";
  toast(missed.length
    ? `Queued ${plural(queued, "artist")}. Couldn't find ${missed.slice(0, 3).join(", ")}${missed.length > 3 ? " and others" : ""} on Spotify.`
    : `Queued ${names.length > 1 ? plural(queued, "artist") : names[0]}. Songs you already have are skipped.`, true);
  poll();
}

async function showLibrary(t, [artist, album] = []) {
  paint(t, `<div class="note">${SPIN}<p>Reading your music folder. The first time can take a minute.</p></div>`);
  const songs = (librarySongs = await loadLibrary());
  if (artist !== undefined && album !== undefined) return showLocalAlbum(t, songs, artist, album);
  const all = shelves(songs);
  const albums = all.reduce((n, a) => n + a.albums.length, 0);
  const render = (q) => {
    const needle = q.trim().toLowerCase();
    const has = (text) => (text || "").toLowerCase().includes(needle);
    const hits = all
      .map(({ name, albums }) => ({ name, albums: !needle || has(name) ? albums : albums.filter((a) => has(a.album) || a.songs.some((s) => has(s.name))) }))
      .filter((a) => a.albums.length);
    return hits.length
      ? hits.map(({ name, albums }) => `<div class="shelf-head"><h2>${esc(name)}</h2>${completable(name)
          ? `<button class="ghost" data-complete="${esc(name)}">Get missing songs</button>` : ""}</div>${grid(albums.map(localTile).join(""))}`).join("")
      : `<p class="note" style="padding:24px 0">Nothing in your library matches “${esc(q)}”.</p>`;
  };
  paint(t, `<section class="home">${aurora}
      <h1>Your library</h1>
      <p>${plural(all.length, "artist")}, ${plural(albums, "album")} and ${plural(songs.length, "song")} in your music folder.</p>
      ${musicChips("library")}
      <div class="chips page-actions"><a href="#/playlists">Playlists</a></div>
    </section>
    <div class="wrap">
      ${songs.length ? `<div class="lib-tools">
        <input class="filter" id="filter" type="search" placeholder="Filter by artist, album or song" aria-label="Filter your library">
        <button class="ghost" data-complete-all>Get missing songs for every artist</button>
      </div>` : ""}
      <div id="shelf">${songs.length ? render("") : `<p class="note" style="padding:24px 0">Your music folder is empty. Search for something above and download it.</p>`}</div>
      ${tidyPanel()}
    </div>`);
  $("#filter")?.addEventListener("input", (e) => { $("#shelf").innerHTML = render(e.target.value); });
}

function showLocalAlbum(t, songs, artist, album) {
  const list = songs
    .filter((s) => s.album_artist === artist && s.album === album)
    .sort((a, b) => a.disc - b.disc || (a.track ?? 1e9) - (b.track ?? 1e9) || a.name.localeCompare(b.name));
  if (!list.length) {
    return paint(t, `<div class="note"><strong>That album isn't in your library any more</strong>It may have been moved or renamed. <a href="#/library">Back to your library</a></div>`);
  }
  const art = list.find((s) => s.art);
  const year = list.find((s) => s.year)?.year;
  const folder = list[0].path.split("/").slice(0, -1).join("/") || "The top of your music folder";
  const meta = `<span><a href="#/library">${esc(artist)}</a></span><span>${esc(year || "")}</span><span>${plural(list.length, "song")}, ${minutes(list)} min</span>`;
  const row = (s, i) => `<li class="row" data-src="${esc(localSrc(s))}">
    <span class="num"><span class="n">${esc(s.track ?? "")}</span><span class="eq" aria-hidden="true"><i></i><i></i><i></i></span></span>
    <span class="what"><button class="title linkish" data-local-play="${i}">${esc(s.name)}</button><span class="by">${esc(s.artists.join(", "))}</span></span>
    <span class="alb" title="${esc(s.path)}">${esc(s.path.split("/").pop())}</span>
    <span class="dur">${mmss(s.ms)}</span><button class="icon-btn play" data-local-play="${i}" aria-label="Play ${esc(s.name)}">${ICON.play}</button>
    <button class="icon-btn" data-local-more="${i}" aria-label="More for ${esc(s.name)}" aria-haspopup="menu" aria-expanded="false">${ICON.more}</button>
  </li>`;
  localAlbum = list.map((s) => ({ src: localSrc(s), title: s.name, by: s.artists.join(", "), album, image: s.art ? coverUrl(s) : art && coverUrl(art),
    href: libHref(artist, album), runtime: (s.ms || 0) / 1000 }));
  paint(t, hero({ kind: "In your library", title: album || "Songs without an album tag", image: art && coverUrl(art), meta,
      action: `<button class="primary" data-local-play="0">${ICON.play}Play</button><button class="ghost" data-local-shuffle>${ICON.shuffle}Shuffle</button>` +
        `<button class="icon-btn ghost-round" data-local-more="all" aria-label="Play next or add to the queue" aria-haspopup="menu" aria-expanded="false">${ICON.more}</button><small>${esc(folder)}</small>` }) +
    `<div class="wrap">${section("Songs", `<ol class="rows jtracks">${list.map(row).join("")}</ol>`)}</div>`);
  markPlaying();
}
/* songs in your music folder play in the dock, straight from the disk */
const localSrc = (s) => "/api/jf/music/" + s.path.split("/").map(encodeURIComponent).join("/");
let localAlbum = [];
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-local-play]"), more = e.target.closest("[data-local-more]");
  if (b) dockPlay(localAlbum, +b.dataset.localPlay, false);
  else if (more) queueMenu(more, () => (more.dataset.localMore === "all" ? localAlbum : [localAlbum[+more.dataset.localMore]]));
  else if (e.target.closest("[data-local-shuffle]")) dockPlay(localAlbum, Math.floor(Math.random() * localAlbum.length), true);
});
function markPlaying() {
  const src = dock.queue[dock.at]?.src;
  document.querySelectorAll(".jtracks .row[data-src]").forEach((r) => r.classList.toggle("now", !!src && r.dataset.src === src));
}
dockOn("play", markPlaying);

function movesList(items, total) {
  const more = total > items.length ? `<li>…and ${total - items.length} more</li>` : "";
  return `<ul class="moves">${items.map((m) => typeof m === "string"
    ? `<li>${esc(m)}</li>`
    : `<li><span>${esc(m.from)}</span><span class="to">to ${esc(m.to)}</span></li>`).join("")}${more}</ul>`;
}

let previewed = ""; // the plan the user saw; "Move" applies exactly that
async function organize(apply, button) {
  const out = $("#tidyResult");
  button.disabled = true;
  out.innerHTML = `<p>${apply ? "Moving songs…" : "Reading every song's tags…"}</p>`;
  try {
    const r = await api(`/music/api/organize?apply=${apply}&plan=${apply ? previewed : ""}`, { method: "POST" });
    previewed = r.plan;
    const n = r.counts;
    if (apply) {
      toast(`Moved ${plural(r.moved, "song")} into artist and album folders`);
      return route();
    }
    let html = n.moves
      ? `<p><strong>${plural(n.moves, "song")} will move</strong>, ${r.in_place} already in the right place.</p>
         <details><summary>See where they go</summary>${movesList(r.moves, n.moves)}</details>
         <div class="actions"><button class="primary" data-tidy="apply">Move ${plural(n.moves, "song")}</button></div>`
      : `<p><strong>Everything is already in the right place.</strong></p>`;
    if (n.conflicts) html += `<details><summary>${plural(n.conflicts, "song")} would land on a file that's already there (probably a duplicate), so ${n.conflicts === 1 ? "it stays" : "they stay"} put</summary>${movesList(r.conflicts, n.conflicts)}</details>`;
    if (n.untagged) html += `<details><summary>${plural(n.untagged, "file")} with missing or unusable tags ${n.untagged === 1 ? "stays" : "stay"} where ${n.untagged === 1 ? "it is" : "they are"}</summary>${movesList(r.untagged, n.untagged)}</details>`;
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<p>${esc(e.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

function ago(ts) {
  if (!ts) return "not synced yet";
  const mins = Math.round((Date.now() / 1000 - ts) / 60);
  if (mins < 2) return "synced just now";
  if (mins < 90) return `synced ${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 36 ? `synced ${plural(hours, "hour")} ago` : `synced ${plural(Math.round(hours / 24), "day")} ago`;
}

async function showPlaylists(t) {
  const { playlists, jellyfin, sync_hours } = await api("/music/api/playlists");
  const where = jellyfin ? "in Jellyfin" : "as a playlist file in your music folder (Playlists/), which Jellyfin, Navidrome and most players pick up";
  const every = sync_hours > 0 ? `Every ${plural(sync_hours, "hour")}, new songs download and the playlist updates ${where}.` : `Use Sync now to update them ${where}.`;
  const row = (p) => {
    const status = jellyfin ? p.jellyfin : p.file && `Playlist file: ${p.file}`;
    const bad = status && status.startsWith("Couldn't");
    return `<li>
      ${img(p.image)}
      <div>
        <a class="name" href="#/playlist/${esc(p.id)}">${esc(p.name || "Playlist")}</a>
        <span class="sub">${esc([p.owner && `By ${p.owner}`, p.songs ? plural(p.songs, "song") : "", ago(p.synced)].filter(Boolean).join(", "))}</span>
        ${status ? `<span class="sub where${bad ? " bad" : ""}">${esc(status)}</span>` : ""}
      </div>
      <div class="acts">
        <button class="ghost" data-sync="${esc(p.id)}">Sync now</button>
        <button class="ghost" data-forget="${esc(p.id)}" data-name="${esc(p.name || "this playlist")}">Stop syncing</button>
      </div>
    </li>`;
  };
  paint(t, `<section class="home">${aurora}
      <h1>Your playlists</h1>
      <p>Spotify playlists, kept in sync. ${every}</p>
      ${musicChips("playlists")}
    </section>
    <div class="wrap">
      <div class="lib-tools">
        <button class="ghost" data-import-open>Import links</button>
        ${playlists.length ? `<a class="ghost" href="/music/api/playlists/export" download>Export playlists</a>` : ""}
      </div>
      <section class="tidy" id="importPanel" hidden>
        <strong>Import links</strong>
        <p>Paste Spotify links, one per line, or open a text file. Playlists, albums, artists and songs all work. Playlists are kept in sync.</p>
        <textarea id="importText" spellcheck="false" placeholder="https://open.spotify.com/playlist/…"></textarea>
        <div class="actions">
          <button class="primary" data-import>Download them</button>
          <label class="ghost" style="cursor:pointer">Open a text file<input type="file" accept=".txt,text/plain" id="importFile" hidden></label>
        </div>
      </section>
      ${playlists.length
        ? `<ul class="lists">${playlists.map(row).join("")}</ul>`
        : `<p class="note" style="padding:32px 0">No playlists yet. Open one on Spotify search and choose Download playlist, or import a list of links.</p>`}
    </div>`);
  $("#importFile")?.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (file) $("#importText").value = await file.text();
  });
}

// Album, artist and playlist links open their page; a song link asks first.
function openLink(t, link) {
  if (link.type !== "track") {
    history.replaceState(null, "", `#/${link.type}/${link.id}`);
    return route();
  }
  paint(t, `<div class="note"><strong>That's a link to a song</strong>
    <div class="actions">${bigButton({ kind: "track", id: link.id }, "Download song")}</div></div>`);
}

/* ---------- downloads ---------- */

async function queueDownload(item, button) {
  if (button) button.disabled = true;
  try {
    await api("/music/api/downloads", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(item) });
    if (button?.classList.contains("primary")) $("span", button).textContent = "Added to downloads";
    else if (button) { button.classList.add("added"); button.innerHTML = ICON.ok; }
    toast(item.title ? `Added “${item.title}” to downloads` : "Added to downloads", true);
    poll();
  } catch (e) {
    if (button) button.disabled = false;
    toast(`Couldn't add it: ${e.message}`);
  }
}

document.addEventListener("click", (e) => {
  const dl = e.target.closest("[data-dl]");
  if (dl) queueDownload(JSON.parse(dl.dataset.dl), dl);
  if (e.target.closest("[data-import-open]")) {
    $("#importPanel").hidden = false;
    $("#importText").focus();
  }
  const imp = e.target.closest("[data-import]");
  if (imp) {
    imp.disabled = true;
    api("/music/api/import", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: $("#importText").value }) })
      .then((r) => {
        toast(`Queued ${plural(r.queued, "download")}` + (r.ignored.length ? `. ${plural(r.ignored.length, "line")} weren't Spotify links.` : ""), true);
        poll();
        route();
      }, (err) => toast(err.message))
      .finally(() => { imp.disabled = false; });
  }
  const sync = e.target.closest("[data-sync]");
  if (sync) {
    sync.disabled = true;
    api(`/music/api/playlists/${sync.dataset.sync}/sync`, { method: "POST" }).then(
      () => { sync.textContent = "Syncing"; toast("Syncing. Only new songs download.", true); poll(); },
      (err) => { sync.disabled = false; toast(err.message); });
  }
  const forget = e.target.closest("[data-forget]");
  if (forget && confirm(`Stop syncing ${forget.dataset.name}? Songs and the playlist itself stay where they are.`)) {
    api(`/music/api/playlists/${forget.dataset.forget}`, { method: "DELETE" }).then(route, (err) => toast(err.message));
  }
  const again = e.target.closest("[data-retry]");
  if (again) {
    again.disabled = true;
    api(`/music/api/downloads/${again.dataset.retry}/retry`, { method: "POST" }).then(
      () => { toast("Queued again. Songs already saved are skipped."); poll(); },
      (err) => { again.disabled = false; toast(err.message); });
  }
  const one = e.target.closest("[data-complete]");
  if (one && (!unorganized || confirm(`${plural(unorganized, "song")} in your folder aren't organized yet, so some could download again. Continue anyway?`))) {
    completeArtists([one.dataset.complete], one);
  }
  const all = e.target.closest("[data-complete-all]");
  if (all) {
    const names = [...new Set(librarySongs.map((s) => s.album_artist))].filter(completable);
    const warn = unorganized ? `\n\n${plural(unorganized, "song")} aren't organized yet and would download again. Organize your folder first.` : "";
    if (confirm(`Queue the full discography of ${plural(names.length, "artist")}? Songs you already have are skipped, so only missing ones download.${warn}`)) completeArtists(names, all);
  }
  const tidy = e.target.closest("[data-tidy]");
  if (tidy) organize(tidy.dataset.tidy === "apply", tidy);
  const rm = e.target.closest("[data-remove]");
  if (rm) {
    rm.disabled = true;
    api(`/music/api/downloads/${rm.dataset.remove}`, { method: "DELETE" }).then(
      (r) => { if (r.stopping) toast("Stopping after the songs in progress"); poll(); },
      (err) => { rm.disabled = false; toast(err.message); });
  }
});

/* ---------- Parlor: the Music section's routes ---------- */

const musicChips = () => subnav();

// marks songs you already have; the first scan of a big folder can take a while,
// so pages wait for it at most 1.5 s
let libraryOnce;
function withLibrary(fn) {
  return async (t, parts) => {
    libraryOnce ||= loadLibrary().catch(() => { libraryOnce = null; });
    await Promise.race([libraryOnce, sleep(1500)]);
    return fn(t, parts);
  };
}
page("music", withLibrary((t, parts) => (parts[0] === "search" && parts[1] ? showSearch(t, parts.slice(1).join("/")) : showHome(t))), "music");
page("album", withLibrary((t, parts) => showAlbum(t, parts.join("/"))), "music");
page("playlist", withLibrary((t, parts) => showPlaylist(t, parts.join("/"))), "music");
page("artist", withLibrary((t, parts) => showArtist(t, parts.join("/"))), "music");
page("library", (t, parts) => showLibrary(t, parts), "music");
page("playlists", (t) => showPlaylists(t), "music");

/* ---------- requests through Lidarr: it finds, downloads and keeps the best copy ---------- */

function lidarrCard(x) {
  const album = x.type === "album";
  const ask = x.have ? "Search again" : album ? "Request album" : "Request artist";
  return `<article class="tile ${album ? "album" : "artist"}">
    <div class="cover">${img(x.image)}</div>
    <span class="name">${esc(album ? x.title : x.name)}</span>
    <span class="sub">${esc(album ? [x.artist, x.year].filter(Boolean).join(", ") : x.have ? "In Lidarr" : "Artist")}</span>
    <button class="ghost lreq" data-lreq="${esc(JSON.stringify({ type: x.type, id: x.id }))}" aria-label="${esc(`${ask}: ${album ? x.title : x.name}`)}">${esc(ask)}</button>
  </article>`;
}
async function showLidarr(t, parts) {
  const q = parts.join("/");
  paint(t, `<section class="home">${aurora}<h1>Request music</h1>
      <p>Ask for one album, or an artist and everything they make. Lidarr fetches the best copy; if it can't, Parlor gets it from YouTube.</p>
      ${musicChips("lidarr")}
      <form id="lidarrForm" class="actions"><label class="field grow"><span class="vh">Artist or album</span><input id="lidarrQ" type="search" placeholder="Artist or album" value="${esc(q)}"></label><button class="primary">Search</button></form>
    </section><div class="wrap" id="lidarrOut">${q ? SPIN : ""}</div><div class="wrap" id="musicReqs"></div>`);
  $("#lidarrForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const v = $("#lidarrQ").value.trim();
    if (v) go(`#/lidarr/${encodeURIComponent(v)}`);
  });
  const out = (html) => { if (t === ticket) $("#lidarrOut").innerHTML = html; };
  const { apps } = await api("/api/arr/apps").catch(() => ({ apps: [] }));
  if (!apps.includes("lidarr")) {
    return out(`<div class="note"><strong>Lidarr isn't connected</strong>Music requests go to Lidarr. In the full stack Parlor sets it up for you; for a Lidarr elsewhere, set LIDARR_URL and LIDARR_API_KEY.</div>`);
  }
  if (!q) { $("#lidarrQ").focus(); return drawRequests(t); }
  let r;
  try { r = await api(`/api/arr/music/search?q=${encodeURIComponent(q)}`); } catch (err) {
    return out(`<div class="note"><strong>Lidarr didn't answer</strong>${esc(err.message)}</div>`);
  }
  const artists = r.items.filter((x) => x.type === "artist").map(lidarrCard).join("");
  const albums = r.items.filter((x) => x.type === "album").map(lidarrCard).join("");
  out((artists || albums) ? section("Artists", grid(artists, "strip")) + section("Albums", grid(albums))
    : `<div class="note"><strong>Nothing matches “${esc(q)}”</strong>Try just the artist, or the album's name.</div>`);
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-lreq]");
  if (!b) return;
  b.disabled = true;
  try {
    const r = await post("/api/arr/music/request", JSON.parse(b.dataset.lreq));
    b.textContent = "Requested";
    toast(`${r.already ? "Lidarr is searching again for" : "Lidarr is looking for"} ${r.title}. It shows in Downloads once it's found.`);
  } catch (err) { b.disabled = false; toast(err.message); }
});

/* ---------- your requests: followed through Lidarr, and fetched from YouTube when Lidarr can't ---------- */

const REQ_PILL = { searching: "pending", downloading: "processing", importing: "processing", fetching: "processing", expanding: "pending",
  needs_look: "requested", imported: "available", failed: "failed" };
const ACTIVE = new Set(["searching", "downloading", "importing", "fetching", "expanding"]);
function requestRow(x) {
  const acts = [
    x.state === "failed" ? `<button class="ghost small" data-mreq="retry" data-id="${x.id}">Retry</button>` : "",
    ["searching", "downloading"].includes(x.state) ? `<button class="ghost small" data-mreq="youtube" data-id="${x.id}" title="Don't wait for Lidarr">Get it from YouTube</button>` : "",
  ].join("");
  const picks = x.state === "needs_look" && x.candidates.length ? `<div class="mpicks">${x.candidates.map((c) => `<button class="mpick" data-mreq="choose" data-id="${x.id}" data-spotify="${esc(c.id)}">
      ${img(c.image)}<span><b>${esc(c.name)}</b><small>${esc([c.artist, c.year, c.tracks ? plural(c.tracks, "song") : ""].filter(Boolean).join(" · "))}</small></span></button>`).join("")}</div>` : "";
  return `<li class="mreq ${esc(x.state)}">${img(x.image)}
    <div><span class="t">${esc(x.title || "An album")}</span><span class="s">${esc(x.by || "")}${x.mine === false ? " · from Lidarr's wanted list" : ""}</span>
      <div class="s">${pill(REQ_PILL[x.state] || "pending", x.label)}${x.via === "youtube" ? `<span class="via">via YouTube</span>` : ""}</div>
      ${x.why ? `<p class="why">${esc(x.why)}</p>` : ""}${picks}</div>
    <div class="acts">${acts}</div></li>`;
}
/** the music requests into #musicReqs, and how many there are. On Request → Music the list is headed "Your requests" and an
    empty one says how to ask; on Your requests it is headed by `title` and an empty one shows nothing. */
let reqTitle; // the heading the list was last drawn under: a press on one of its buttons draws it again the same way
async function drawRequests(t, title) {
  reqTitle = title;
  const box = $("#musicReqs");
  const r = await api("/api/arr/music/requests").catch(() => null);
  if (!box || t !== ticket || !r) return 0;
  const open = r.items.filter((x) => x.state !== "imported"), done = r.items.filter((x) => x.state === "imported").slice(0, 12);
  box.innerHTML = r.items.length ? (open.length ? shelf(title || "Your requests", `<ul class="reqs mreqs">${open.map(requestRow).join("")}</ul>`) : "") +
      (done.length ? shelf(title ? `${title}: arrived lately` : "Arrived lately", `<ul class="reqs mreqs">${done.map(requestRow).join("")}</ul>`) : "")
    : title !== undefined ? (title ? "" : `<div class="empty-state"><strong>No music requested yet</strong>Ask for an album or an artist in <a href="#/lidarr">Music</a>.</div>`)
    : `<div class="empty-state"><strong>Nothing requested yet</strong>Search above. Lidarr looks first; if it finds nothing or stalls, Parlor gets it from YouTube instead, so a request never just sits there.</div>`;
  if (open.some((x) => ACTIVE.has(x.state))) setTimeout(() => t === ticket && $("#musicReqs") === box && drawRequests(t, title), 15000);
  return r.items.length;
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-mreq]");
  if (!b) return;
  b.disabled = true;
  const what = b.dataset.mreq;
  try {
    await post(`/api/arr/music/requests/${b.dataset.id}/${what}`, what === "choose" ? { spotify: b.dataset.spotify } : undefined);
    toast({ retry: "Trying again", youtube: "Getting it from YouTube", choose: "Getting that one from YouTube" }[what]);
    drawRequests(ticket, reqTitle);
  } catch (err) { b.disabled = false; toast(err.message); }
});
page("lidarr", showLidarr, "musicreq");  // a page of Request; its search box asks Lidarr
