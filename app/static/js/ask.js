/* Request. The page of everything to ask for and its one search are at the end. Before them, the Games and Books pages:
   rows to browse and a title search, a page each with one Request button, and the requests followed to the end. Games come from Steam's store and go through Questarr (gamereq.py): a member's
   request waits for an admin's yes, and a release nobody could be sure of waits for an admin to pick it. Books come
   from Open Library and Parlor fetches them itself (bookreq.py): no yes needed, only book files reach the library. */

const ASK = {
  game: { api: "/api/games/requests", at: "#/gamereq", box: "#gameReqs", word: "Game", id: (x) => x.app, again: ["searching", "choose", "failed"], short: { pending: "Waiting", choose: "Waiting" },
    sub: (x) => `${esc(x.year || "")}${x.rating ? ` <span class="star">★</span> ${esc(x.rating)}%` : ""}`, line: (x) => [x.year, "Game", x.by],
    gone: "A download that has started is cancelled in Questarr." },
  book: { api: "/api/books/requests", at: "#/bookreq", box: "#bookReqs", word: "Book", id: (x) => x.work, again: ["searching", "downloading", "failed"], short: { available: "In library" },
    sub: (x) => esc(x.author || x.year || ""), line: (x) => [x.author, x.year, x.by],
    gone: "Its download is removed too; a book that's already in your library stays." },
};
const GREQ_PILL = { pending: "pending", searching: "pending", choose: "requested", downloading: "processing", available: "available", failed: "failed", declined: "declined" };
const GREQ_LABEL = { pending: "Waiting for an admin", searching: "Searching", choose: "Pick a release", downloading: "Downloading", available: "Downloaded", failed: "Stuck", declined: "Declined" };
const GREQ_ACTIVE = new Set(["pending", "searching", "choose", "downloading"]);
const askable = (st) => !st || st === "failed" || st === "declined";
const gamePill = (st, label) => pill(GREQ_PILL[st] || "pending", label || GREQ_LABEL[st] || st);

/** a game or a book on a shelf; on a card the pill has a poster's width, so one or two short words */
function askCard(k, x) {
  const href = `${ASK[k].at}/${k}/${esc(x.id)}`;
  return `<article class="pcard" data-${k}="${esc(x.id)}" data-title="${esc(x.title)}">
    <div class="poster"><a href="${href}" tabindex="-1" aria-hidden="true">${x.poster ? `<img src="${esc(x.poster)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<span class="noart">${esc(x.title)}</span>`}</a>
      ${x.status ? gamePill(x.status, ASK[k].short[x.status]) : ""}
      ${askable(x.status) ? `<button class="req" data-ask="${k}">Request</button>` : ""}</div>
    <a class="t" href="${href}">${esc(x.title)}</a>
    <span class="s">${ASK[k].sub(x)}</span>
  </article>`;
}
const askStrip = (k, items) => (items?.length ? `<div class="pstrip">${items.map((x) => askCard(k, x)).join("")}</div>` : "");
/** the Request button of a game's or a book's own page */
const askButton = (k, x, label) => `<span data-${k}="${esc(x.id)}" data-title="${esc(x.title)}"><button class="primary" data-ask="${k}">${label}</button></span>`;

const notReady = (s) => (s.ready ? "" : `<div class="panel"><div class="head"><div><h3>Questarr isn't set up yet</h3>
    <p>You can look around; asking for a game works once ${ME?.admin ? "setup has run: Settings → Services → Set up again." : "an admin has finished setting up."}</p></div>
    ${ME?.admin ? `<a class="primary" href="#/settings">Open Settings</a>` : ""}</div></div>`);

async function showGameReq(t, [what = "", ...rest]) {
  if (what === "search") return showAskSearch("game", t, rest.join("/"));
  if (what === "game") return showGame(t, rest[0]);
  const head = `<section class="home">${aurora}<h1>Games</h1>
    <p>Find a game and ask for it: it's fetched behind the VPN, like everything else.</p>${subnav()}</section>`;
  paint(t, `${head}<div class="wrap">${skeletons(8, "p", "pstrip")}</div>`);
  const [s, d] = await Promise.all([api("/api/games/requests/status"), api("/api/games/requests/discover")]);
  paint(t, `${head}<div class="wrap">${notReady(s)}
      ${shelf("Popular now", askStrip("game", d.popular))}
      ${shelf("New and popular", askStrip("game", d.new))}
      ${shelf("Coming soon", askStrip("game", d.upcoming))}
      ${d.popular.length || d.new.length || d.upcoming.length ? "" : `<div class="empty-state"><strong>Steam's store didn't answer</strong>The rows come from it. Try again in a moment, or search for a title above.</div>`}
      ${ME?.admin && s.ready ? `<label class="gauto"><input type="checkbox" id="gameAuto"${s.auto ? " checked" : ""}><span>Take a release without asking when it passes every check
        <small>The whole game, its exact title, from a scene group or a known repacker, with people seeding it. Unticked, every request waits for you to pick the release. Either way a game is a program: scan it before you run it.</small></span></label>` : ""}
    </div>`);
  $("#gameAuto")?.addEventListener("change", (e) => put("/api/games/requests/auto", { on: e.target.checked }).then((r) => toast(r.auto ? "Parlor takes sure releases by itself" : "Every request waits for your pick"), (err) => { e.target.checked = !e.target.checked; toast(err.message); }));
}

async function showBookReq(t, [what = "", ...rest]) {
  if (what === "search") return showAskSearch("book", t, rest.join("/"));
  if (what === "book") return showBook(t, rest[0]);
  const head = `<section class="home">${aurora}<h1>Books</h1>
    <p>Find a book and ask for it: it turns up in <a href="#/jf/books">your Books</a>.</p>${subnav()}</section>`;
  paint(t, `${head}<div class="wrap">${skeletons(8, "p", "pstrip")}</div>`);
  const d = await api("/api/books/requests/discover").catch(() => ({}));
  paint(t, `${head}<div class="wrap">
      ${shelf("Read a lot this week", askStrip("book", d.trending))}
      ${shelf("Science fiction", askStrip("book", d.scifi))}
      ${shelf("Mystery", askStrip("book", d.mystery))}
      ${d.trending?.length || d.scifi?.length || d.mystery?.length ? "" : `<div class="empty-state"><strong>Open Library didn't answer</strong>The rows come from it. Try again in a moment, or search for a title or an author above.</div>`}
    </div>`);
}

async function showAskSearch(k, t, q) {
  input.value = q;
  const r = await api(`${ASK[k].api}/search?q=${encodeURIComponent(q)}`);
  paint(t, r.items.length ? `<div class="wrap">${shelf(`${ASK[k].word}s`, `<div class="pgrid">${r.items.map((x) => askCard(k, x)).join("")}</div>`)}</div>`
    : k === "game" ? `<div class="note"><strong>No game matches “${esc(q)}”</strong>The catalogue is Steam's store: try the title as Steam has it. A game Steam doesn't sell isn't found here.</div>`
    : `<div class="note"><strong>No book matches “${esc(q)}”</strong>The catalogue is Open Library's: try the title, the author, or both.</div>`);
}

/** one request: a game's or a book's */
function askRow(k, x) {
  const c = ASK[k];
  const b = (what, label, cls = "") => `<button class="ghost small ${cls}" data-greq="${what}" data-kind="${k}" data-id="${esc(x.id)}">${label}</button>`;
  const acts = [
    ME?.admin && x.state === "pending" ? b("approve", "Approve") + b("decline", "Decline", "danger") : "",
    k === "book" && x.state === "available" ? `<a class="ghost small" href="#/jf/books">Open Books</a>` : "",
    (x.mine || ME?.admin) && c.again.includes(x.state) ? b("retry", x.state === "downloading" ? "Try another" : "Search again") : "",
    x.mine || ME?.admin ? b("cancel", x.state === "available" || x.state === "declined" ? "Remove" : "Cancel", "danger") : "",
  ].join("");
  const picks = x.picks?.length ? `<div class="mpicks gpicks">${x.picks.map((p, i) => `<button class="mpick" data-greq="pick" data-kind="${k}" data-id="${esc(x.id)}" data-pick="${i}">
      <span><b>${esc(p.title)}</b><small>${esc([p.size ? bytes(p.size) : "", p.seeders != null ? plural(p.seeders, "seeder") : "", p.indexer, (p.kind || "").replace("_", " "), p.sure ? "passes every check" : ""].filter(Boolean).join(" · "))}</small></span></button>`).join("")}</div>` : "";
  return `<li class="mreq ${esc(x.state)}">${img(x.image)}
    <div><a class="t" href="${c.at}/${k}/${esc(c.id(x))}">${esc(x.title)}</a><span class="s">${esc(c.line(x).filter(Boolean).join(" · "))}</span>
      <div class="s">${gamePill(x.state, x.label)}</div>
      ${x.why ? `<p class="why">${esc(x.why)}</p>` : ""}${x.release ? `<p class="why">Release: ${esc(x.release)}</p>` : ""}${picks}</div>
    <div class="acts">${acts}</div></li>`;
}

/** the game or book requests into their box under `title`, and how many there are; nothing at all when there are none */
async function drawAsked(k, t, title) {
  const box = $(ASK[k].box);
  const r = await api(ASK[k].api).catch(() => null);
  if (!box || t !== ticket) return 0;
  if (!r) return (box.innerHTML = `<p class="dim">The ${k} requests couldn't be loaded just now.</p>`).length;  // said, so the page doesn't claim there are none
  box.dataset.title = title;
  box.innerHTML = r.items.length ? shelf(title || "Your requests", `<ul class="reqs mreqs greqs">${r.items.map((x) => askRow(k, x)).join("")}</ul>`) : "";
  if (r.items.some((x) => GREQ_ACTIVE.has(x.state) && x.state !== "choose")) setTimeout(() => t === ticket && $(ASK[k].box) === box && drawAsked(k, t, title), 15000);
  return r.items.length;
}

/** what a game's page and a book's share: the request under the hero, and a look again while it's moving */
const askedRow = (k, q) => (q ? `<div class="shelf-head"><h2>The request</h2></div><ul class="reqs mreqs greqs">${askRow(k, q)}</ul>` : "");
const askWatch = (t, q) => { if (q && GREQ_ACTIVE.has(q.state) && q.state !== "choose") setTimeout(() => { if (t === ticket) { keepScroll = true; route(); } }, 20000); };

async function showGame(t, id) {
  const x = await api(`/api/games/requests/game/${encodeURIComponent(id)}`);
  const q = x.request;
  const action = [
    q ? "" : x.ready ? askButton("game", x, "Request") : `<small>Asking works once Questarr is set up.</small>`,
    q && askable(q.state) && x.ready ? askButton("game", x, "Request again") : "",
    `<a class="ghost" href="${esc(x.store)}" target="_blank" rel="noopener noreferrer">${ICON.out}On Steam</a>`,
  ].join("");
  const facts = [x.year, x.soon ? "Coming soon" : x.early ? "Early access" : "", x.platforms.map((p) => p.replace(/^PC \((.*)\)$/, "$1")).join(", ")].filter(Boolean);
  const made = [x.developers.length ? `By ${x.developers.join(", ")}` : "", x.publishers.length ? `Published by ${x.publishers.join(", ")}` : "", x.modes.join(", ")].filter(Boolean);
  paint(t, hero({
    kind: "Game", title: x.title, backdrop: x.screenshots[0], image: x.poster, poster: true,
    meta: facts.map((f) => `<span>${esc(f)}</span>`).join("") + (x.rating ? `<span><span class="star">★</span> ${esc(x.rating)}%${x.reviews ? ` of ${esc(x.reviews.toLocaleString())} reviews` : ""}</span>` : "") + (q ? gamePill(q.state, q.label) : ""),
    blurb: x.summary, action,
  }) + `<div class="wrap">
      ${askedRow("game", q)}
      ${x.genres.length ? `<p class="dim">${esc(x.genres.join(" · "))}</p>` : ""}
      ${made.length ? `<p class="dim">${esc(made.join(" · "))}</p>` : ""}
      ${x.screenshots.length ? `<div class="shelf-head"><h2>Screenshots</h2></div><div class="gshots">${x.screenshots.map((s) => `<img src="${esc(s)}" alt="" loading="lazy" referrerpolicy="no-referrer">`).join("")}</div>` : ""}
    </div>`);
  askWatch(t, q);
}

async function showBook(t, id) {
  const x = await api(`/api/books/requests/book/${encodeURIComponent(id)}`);
  const q = x.request;
  const action = [
    q ? "" : askButton("book", x, "Request"),
    q?.state === "failed" ? askButton("book", x, "Request again") : "",
    q?.state === "available" ? `<a class="primary" href="#/jf/books">Open Books</a>` : "",
    `<a class="ghost" href="${esc(x.store)}" target="_blank" rel="noopener noreferrer">${ICON.out}On Open Library</a>`,
  ].join("");
  const facts = [x.author, x.year && `First published ${x.year}`, x.editions > 1 && `${x.editions.toLocaleString()} editions`].filter(Boolean);
  paint(t, hero({
    kind: "Book", title: x.title, image: x.poster?.replace("-M.jpg", "-L.jpg"), poster: true,
    meta: facts.map((f) => `<span>${esc(f)}</span>`).join("") + (x.rating ? `<span><span class="star">★</span> ${esc(x.rating)} of 5</span>` : "") + (q ? gamePill(q.state, q.label) : ""),
    blurb: x.summary, action,
  }) + `<div class="wrap">
      ${askedRow("book", q)}
      ${x.subjects.length ? `<p class="dim">${esc(x.subjects.join(" · "))}</p>` : ""}
      <p class="dim">Parlor takes the release whose name is this title and this author and little else, and brings only the book files into your library (EPUB first: it opens in Parlor's reader). If it's the wrong book, Try another fetches the next one.</p>
    </div>`);
  askWatch(t, q);
}

document.addEventListener("click", async (e) => {
  const ask = e.target.closest("[data-ask]");
  if (ask) {
    const k = ask.dataset.ask, { title, [k]: key } = ask.closest(`[data-${k}]`).dataset;
    ask.disabled = true;
    try {
      const r = await post(`${ASK[k].api}/${k}/${encodeURIComponent(key)}`);
      toast(r.state === "pending" ? `Asked for “${title}”; it's waiting for an admin` : r.mine ? `Asked for “${title}”; Parlor is looking for it` : `“${title}” was already asked for`);
      if (location.hash.startsWith(`${ASK[k].at}/${k}/`)) { keepScroll = true; route(); } else ask.outerHTML = gamePill(r.state, ASK[k].short[r.state]);
    } catch (err) {
      ask.disabled = false;
      toast(err.message);
    }
    return;
  }
  const b = e.target.closest("[data-greq]");
  if (!b) return;
  const what = b.dataset.greq, c = ASK[b.dataset.kind], at = `${c.api}/${encodeURIComponent(b.dataset.id)}`;
  if (what === "cancel" && !confirm(`Remove this request? ${c.gone}`)) return;
  if (what === "pick" && !confirm(`Download “${b.querySelector("b").textContent}”?`)) return;
  b.disabled = true;
  try {
    await (what === "cancel" ? del(at) : post(`${at}/${what}`, what === "pick" ? { pick: +b.dataset.pick, title: b.querySelector("b").textContent } : undefined));
    toast({ approve: "Approved: Parlor is looking for it", decline: "Declined", retry: "Searching again", cancel: "Removed", pick: "Downloading that one" }[what]);
    const box = $(c.box);
    if (box) drawAsked(b.dataset.kind, ticket, box.dataset.title); else { keepScroll = true; route(); }
  } catch (err) {
    b.disabled = false;
    toast(err.message);
  }
});

/* ---------- Request: everything you can ask for on one page, a row a kind, and one search box over all of them ---------- */

const few = (items) => (items || []).slice(0, 14);
// each kind: the section it needs open, its own page, whether it is set up, its row here, its matches, and its own search page
const ASK_KINDS = [
  { key: "films", title: "Films & shows", needs: "media", at: "#/media", off: "Seerr isn't connected yet.", more: (q) => `#/media/search/${q}`,
    up: async () => { const s = await checkSeerr(); return s === true || s.connected; },
    row: async () => pstrip(few((await api("/api/seerr/discover")).trending)),
    find: async (q) => pstrip(few((await api(`/api/seerr/search?q=${q}`)).items.filter((x) => x.type !== "person"))) },
  { key: "games", title: "Games", needs: "games", at: "#/gamereq", more: (q) => `#/gamereq/search/${q}`, up: async () => true,
    row: async () => askStrip("game", few((await api("/api/games/requests/discover")).popular)),
    find: async (q) => askStrip("game", few((await api(`/api/games/requests/search?q=${q}`)).items)) },
  { key: "books", title: "Books", needs: "library", at: "#/bookreq", more: (q) => `#/bookreq/search/${q}`, up: async () => true,
    row: async () => askStrip("book", few((await api("/api/books/requests/discover")).trending)),
    find: async (q) => askStrip("book", few((await api(`/api/books/requests/search?q=${q}`)).items)) },
  { key: "music", title: "Music", needs: "music", at: "#/lidarr", off: "Lidarr isn't connected yet.", more: (q) => `#/lidarr/${q}`,
    up: async () => (await api("/api/arr/apps")).apps.includes("lidarr"),
    row: async () => `<p class="dim">Lidarr has no rows to look through: search above for an artist or an album.</p>`,
    find: async (q) => grid(few((await api(`/api/arr/music/search?q=${q}`)).items).map(lidarrCard).join(""), "strip") },
];
const askHead = (k, href, word) => `<div class="shelf-head"><h2>${esc(k.title)}</h2><a class="more" href="${href}">${word}</a></div>`;
const askNote = (text) => `<p class="dim">${text}</p>`;

async function showRequest(t, [what = "", ...rest]) {
  if (what === "search") return showAskAll(t, rest.join("/"));
  const kinds = ASK_KINDS.filter((k) => !closedTo(k.needs));
  paint(t, `<section class="home">${aurora}<h1>Request</h1>
    <p>Films, shows, music, games and books: search above, or pick from a row.</p>${subnav()}</section>
    <div class="wrap">${kinds.map((k) => `<div id="ask-${k.key}">${askHead(k, k.at, "See all")}${k.key === "music" ? "" : skeletons(8, "p", "pstrip")}</div>`).join("")}</div>`);
  kinds.forEach(async (k) => {
    const body = await k.up().then((up) => (up ? k.row().then((html) => html || askNote("Nothing to show here just now: search above.")) : askNote(k.off)))
      .catch(() => askNote(`${esc(k.title)} didn't load just now.`));
    fill(t, `ask-${k.key}`, askHead(k, k.at, "See all") + body);
  });
}

/** one search over every kind you may ask for: a row each as it arrives, with a link to the kind's own, longer list */
async function showAskAll(t, q) {
  input.value = q;
  const enc = encodeURIComponent(q), kinds = ASK_KINDS.filter((k) => !closedTo(k.needs));
  paint(t, `<div class="wrap">${kinds.map((k) => `<div id="ask-${k.key}">${askHead(k, k.more(enc), "More")}${skeletons(8, "p", "pstrip")}</div>`).join("")}
    <div class="note" id="askNone" hidden><strong>Nothing to ask for matches “${esc(q)}”</strong>Try the title alone, or the name as its maker writes it.</div></div>`);
  const got = await Promise.all(kinds.map(async (k) => {
    const html = await k.up().then((up) => (up ? k.find(enc) : "")).catch(() => askNote(`${esc(k.title)} couldn't be searched just now.`));
    fill(t, `ask-${k.key}`, html ? askHead(k, k.more(enc), "More") + html : "");
    return html;
  }));
  if (t === ticket && !got.some(Boolean)) $("#askNone").hidden = false;
}

page("request", showRequest, "media");
page("gamereq", showGameReq, "gamereq");
page("bookreq", showBookReq, "bookreq");
