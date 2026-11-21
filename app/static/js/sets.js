/* Whole sets through Seerr: a film collection, a studio's films, a network's shows, a keyword's titles; and asking
   for everything that's missing at once. The server counts first, you confirm, and Downloads shows how it goes. */

const SET_KIND = { collection: "Film collection", studio: "Studio", network: "Network", keyword: "Keyword" };
const SET_ACTIVE = ["queued", "running"];
const setHref = (kind, id) => `#/set/${esc(kind)}/${esc(id)}`;
const setFilters = {}; // what you narrowed each set to, kept while the app is open
const titlesOf = (n, media) => plural(n, media === "tv" ? "show" : "film");

function setCard(a) {
  return `<a class="pcard setcard" href="${setHref(a.kind, a.id)}">
    <div class="poster">${a.poster ? `<img src="${esc(artUrl(a.poster))}" alt="" loading="lazy">` : `<span class="noart">${esc(a.name)}</span>`}
      ${a.have ? `<span class="pill available"><span class="dot"></span>${a.have} in your library</span>` : ""}</div>
    <span class="t">${esc(a.name)}</span></a>`;
}
/* a studio's or network's logo, light on dark and dark on light */
const setLogo = (a) => (a.logo ? `<picture><source media="(prefers-color-scheme: light)" srcset="${esc(a.logo_dark)}"><img src="${esc(a.logo)}" alt="${esc(a.name)}" loading="lazy"></picture>` : `<span>${esc(a.name)}</span>`);
const logoTile = (a) => `<a class="logotile" href="${setHref(a.kind, a.id)}" title="${esc(a.name)}">${setLogo(a)}</a>`;
const setChips = (list) => list.map((a) => `<a href="${setHref(a.kind, a.id)}">${esc(a.name)}</a>`).join("");

async function showSets(t) {
  const s = await checkSeerr();
  if (s !== true && !s.connected) return notConnected(t, s.reason);
  const head = `<section class="home">${aurora}<h1>Collections</h1>
    <p>Whole franchises, a studio's films, a network's shows. Open one to see what you have and request the rest in one go.</p>${subnav()}
    <form class="setfind" id="setFind" role="search"><input class="input" id="setQ" type="search" placeholder="Find a studio or a theme: heist, time travel, based on a novel…" aria-label="Find a studio or a theme"></form>
    <nav class="chips" id="setFound" hidden></nav></section>`;
  paint(t, `${head}<div class="wrap">${skeletons(8, "p", "pgrid")}</div>`);
  const d = await api("/api/seerr/sets");
  if (t !== ticket) return;
  const q = $("#setQ").value;
  paint(t, `${head}<div class="wrap">
      ${shelf("Franchises", d.franchises.length ? `<div class="pgrid">${d.franchises.map(setCard).join("")}</div>` : "")}
      ${shelf("Studios", d.studios.length ? `<div class="logogrid">${d.studios.map(logoTile).join("")}</div>` : "")}
      ${shelf("Networks", d.networks.length ? `<div class="logogrid">${d.networks.map(logoTile).join("")}</div>` : "")}
    </div>`);
  $("#setQ").value = q;
  $("#setFind").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#setQ").value.trim();
    if (!text) return;
    const r = await api(`/api/seerr/sets/search?q=${encodeURIComponent(text)}`).catch((err) => { toast(err.message); return null; });
    const box = $("#setFound");
    if (!r || !box) return;
    box.hidden = false;
    box.innerHTML = setChips([...r.studios, ...r.keywords]) || `<span class="dim">No studio or theme matches “${esc(text)}”.</span>`;
  });
}

async function showSet(t, [kind, id]) {
  const f = (setFilters[`${kind}/${id}`] ??= { media: "movie", year_from: "", year_to: "", rating: 0, released: true });
  const body = () => ({ media: f.media, rating: +f.rating, released: f.released, ...(f.year_from ? { year_from: +f.year_from } : {}), ...(f.year_to ? { year_to: +f.year_to } : {}) });
  const load = (page) => api(`/api/seerr/sets/${encodeURIComponent(kind)}/${encodeURIComponent(id)}?${new URLSearchParams({ ...body(), page })}`);
  let d = await load(1), pageNo = 1;
  const bar = `<form class="setbar" id="setBar">
      ${kind === "keyword" ? `<label><span>Show</span><select class="input" name="media"><option value="movie">Films</option><option value="tv"${f.media === "tv" ? " selected" : ""}>Shows</option></select></label>` : ""}
      <label><span>From</span><input class="input" name="year_from" type="number" inputmode="numeric" min="1870" max="2200" placeholder="year" value="${esc(f.year_from)}"></label>
      <label><span>To</span><input class="input" name="year_to" type="number" inputmode="numeric" min="1870" max="2200" placeholder="year" value="${esc(f.year_to)}"></label>
      <label><span>Rated</span><select class="input" name="rating">${[[0, "Any"], [6, "6 and up"], [7, "7 and up"], [8, "8 and up"]].map(([v, l]) => `<option value="${v}"${+f.rating === v ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="check"><input type="checkbox" name="released"${f.released ? " checked" : ""}> Only what's out</label>
      <span class="grow"></span><strong id="setCount"></strong>
      ${d.can_request ? `<button class="primary" type="button" id="askAll">Request all missing</button>` : `<small>Your account isn't linked to Seerr yet; ask an admin.</small>`}
    </form>`;
  const head = kind === "collection"
    ? hero({ kind: SET_KIND[kind], title: d.name, backdrop: d.backdrop, image: d.poster, poster: true, meta: "", blurb: d.overview, action: "" })
    : `<section class="home">${aurora}<div class="kind">${SET_KIND[kind] || ""}</div>${d.logo ? `<div class="setlogo">${setLogo(d)}</div><h1 class="sr">${esc(d.name)}</h1>` : `<h1>${esc(d.name)}</h1>`}</section>`;
  paint(t, `${head}<div class="wrap">${bar}
      <div class="pgrid" id="setGrid"></div>
      <div class="actions" style="justify-content:center;margin-top:30px"><button class="ghost" id="setMore">Load more</button></div></div>`);
  if (t !== ticket) return;
  const draw = () => {
    $("#setGrid").innerHTML = d.items.map(pcard).join("") || `<div class="empty-state" style="grid-column:1/-1"><strong>Nothing fits</strong>Widen the years or the rating.</div>`;
    $("#setCount").textContent = titlesOf(d.total, d.media);
    $("#setMore").hidden = pageNo >= d.pages;
  };
  draw();
  $("#setBar").addEventListener("change", async (e) => {
    if (!e.target.reportValidity()) return;
    f[e.target.name] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    try { d = await load(pageNo = 1); if (t === ticket) draw(); } catch (err) { toast(err.message); }
  });
  $("#setBar").addEventListener("submit", (e) => e.preventDefault());
  $("#setMore").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const next = await load(++pageNo).catch((err) => { toast(err.message); pageNo--; return null; });
    e.target.disabled = false;
    if (!next || t !== ticket) return;
    $("#setGrid").insertAdjacentHTML("beforeend", next.items.map(pcard).join(""));
    $("#setMore").hidden = pageNo >= next.pages;
  });
  $("#askAll")?.addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await askForSet(await post("/api/seerr/sets/plan", { kind, id: +id, ...body() }), d.media); } catch (err) { toast(err.message); }
    e.target.disabled = false;
  });
}

/* what would be requested, counted by the server; nothing is sent until you confirm */
function askForSet(p, media) {
  const LEFT = { there: "already in your library", partly: "partly there (pick seasons on their pages)", requested: "already requested", blocked: "blocked in Seerr" };
  const left = Object.entries(p.left_out).map(([k, n]) => `${n} ${LEFT[k] || k}`).join(" · ");
  if (!p.count) { toast(left ? `Nothing is missing from ${p.name}: ${left}` : `Nothing in ${p.name} fits`); return; }
  const box = $("#setAsk");
  box.innerHTML = `<form method="dialog">
      <h2>Request ${titlesOf(p.count, media)}?</h2>
      <p>Everything missing from <strong>${esc(p.name)}</strong>, requested as you, one at a time. Seerr's approval and limits apply to each.</p>
      <ul class="asklist">${p.titles.map((x) => `<li>${img(x.poster)}<span>${esc(x.title)}</span><small>${esc(x.year || "")}</small></li>`).join("")}</ul>
      ${left ? `<p class="dim">Left out: ${esc(left)}.</p>` : ""}
      ${p.more ? `<p class="dim">This set is bigger than one go: these are the first ${p.count} missing. Narrow it by year or rating, or come back for the rest.</p>` : ""}
      <div class="actions"><button class="primary" value="go">Request ${titlesOf(p.count, media)}</button><button class="ghost" value="" autofocus>Cancel</button></div>
    </form>`;
  box.returnValue = "";
  box.showModal();
  return new Promise((done) => box.addEventListener("close", async () => {
    if (box.returnValue === "go") {
      try {
        await post("/api/seerr/sets/request", { plan: p.plan });
        toast(`Requesting ${titlesOf(p.count, media)} from ${p.name}`, true);
        pollSets(true);
      } catch (err) { toast(err.message); }
    }
    done();
  }, { once: true }));
}

/* ---------- in Downloads ---------- */

let setJobs = [], setShape = "", setAt = 0;
const setExpanded = new Set();
function setJobState(j) {
  if (SET_ACTIVE.includes(j.status)) return j.done ? `Requested ${j.requested} of ${j.total}${j.skipped ? `, ${j.skipped} already covered` : ""}` : "Waiting to start";
  const not = j.left + j.waiting;
  return [j.status === "stopped" && (j.note || "Stopped"), `${j.requested} requested`, j.skipped && `${j.skipped} already covered`, j.failed && `${j.failed} failed`, not && `${not} not asked for yet`].filter(Boolean).join(" · ");
}
function setJobItem(j) {
  const active = SET_ACTIVE.includes(j.status), notes = j.titles.filter((x) => x.why), again = !active && j.failed + j.left + j.waiting > 0;
  return `<li class="job set" data-k="${esc(j.id)}">
    <div class="blank">${ICON.film}</div>
    <div>
      <strong><a href="${setHref(j.kind, j.set)}">${esc(j.name)}</a></strong>
      <span class="sub">${esc(`Request all · ${plural(j.total, "title")}`)}</span>
      ${active ? `<div class="meter"><i style="width:${(100 * j.done / j.total).toFixed(1)}%"></i></div>` : ""}
      <span class="state ${j.status === "stopped" || j.failed ? "failed" : j.status === "done" ? "done" : ""}">${esc(setJobState(j))}</span>
      ${notes.length ? `<details data-setjob="${esc(j.id)}"${setExpanded.has(j.id) ? " open" : ""}><summary>${plural(notes.length, "title")} not requested</summary>
        <ol class="songs">${notes.map((x) => `<li class="${x.state === "failed" ? "failed" : ""}"><span><a href="#/${esc(x.type)}/${esc(x.id)}">${esc(x.title)}</a></span><span class="st">${esc(x.why)}</span></li>`).join("")}</ol></details>` : ""}
    </div>
    ${active ? `<button class="stop" data-setstop="${esc(j.id)}">Stop</button>`
      : `<span class="job-actions">${again ? `<button class="stop retry" data-setretry="${esc(j.id)}">${j.status === "stopped" ? "Carry on" : "Retry"}</button>` : ""}
          <button class="icon-btn" data-setstop="${esc(j.id)}" aria-label="Remove">${ICON.x}</button></span>`}
  </li>`;
}
function renderSets() {
  $("#setHead").hidden = $("#setJobs").hidden = !setJobs.length;
  const shape = JSON.stringify(setJobs);
  if (shape !== setShape) { setShape = shape; $("#setJobs").innerHTML = setJobs.map(setJobItem).join(""); }
}
// its own clock, like the *arr queue: quick while a set is going or the drawer is open, every half minute otherwise
async function pollSets(now) {
  if (ME?.sections?.media === false || document.hidden) return;
  const busy = setJobs.some((j) => SET_ACTIVE.includes(j.status));
  if (!now && Date.now() - setAt < (busy ? 1500 : drawer.classList.contains("open") ? 4000 : 30000)) return;
  setAt = Date.now();
  try { setJobs = (await api("/api/seerr/sets/jobs")).jobs; } catch { return; }
  renderSets();
  drawCount();
}
$("#setJobs").addEventListener("toggle", (e) => {
  const id = e.target.dataset.setjob;
  if (id) e.target.open ? setExpanded.add(id) : setExpanded.delete(id);
}, true);
document.addEventListener("click", (e) => {
  const stop = e.target.closest("[data-setstop]"), again = e.target.closest("[data-setretry]");
  if (stop) { stop.disabled = true; del(`/api/seerr/sets/jobs/${stop.dataset.setstop}`).then(() => pollSets(true), (err) => { stop.disabled = false; toast(err.message); }); }
  if (again) { again.disabled = true; post(`/api/seerr/sets/jobs/${again.dataset.setretry}/retry`).then(() => pollSets(true), (err) => { again.disabled = false; toast(err.message); }); }
});
$("#clearDone").addEventListener("click", async () => {
  await Promise.all(setJobs.filter((j) => j.status === "done").map((j) => del(`/api/seerr/sets/jobs/${j.id}`).catch(() => {})));
  pollSets(true);
});
setInterval(() => { if (ME) pollSets(); }, 1500);

page("sets", showSets, "media");
page("set", showSet, "media");
