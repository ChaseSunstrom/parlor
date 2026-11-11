/* Request → Games: rows to browse and a title search from Steam's store, a game's page with one Request button, and
   game requests followed through Questarr (gamereq.py). A member's request waits for an admin's yes; a release nobody
   could be sure of waits for an admin to pick it from the list. */

const GREQ_PILL = { pending: "pending", searching: "pending", choose: "requested", downloading: "processing", available: "available", failed: "failed", declined: "declined" };
const GREQ_LABEL = { pending: "Waiting for an admin", searching: "Searching", choose: "Pick a release", downloading: "Downloading", available: "Downloaded", failed: "Stuck", declined: "Declined" };
const GREQ_ACTIVE = new Set(["pending", "searching", "choose", "downloading"]);
// on a card the pill has a poster's width: one word
const GREQ_SHORT = { pending: "Waiting", choose: "Waiting" };
const askable = (st) => !st || st === "failed" || st === "declined";
const gamePill = (st, label) => pill(GREQ_PILL[st] || "pending", label || GREQ_LABEL[st] || st);

function gcard(x) {
  const href = `#/gamereq/game/${esc(x.id)}`;
  return `<article class="pcard" data-game="${esc(x.id)}" data-title="${esc(x.title)}">
    <div class="poster"><a href="${href}" tabindex="-1" aria-hidden="true">${x.poster ? `<img src="${esc(x.poster)}" alt="" loading="lazy">` : `<span class="noart">${esc(x.title)}</span>`}</a>
      ${x.status ? gamePill(x.status, GREQ_SHORT[x.status]) : ""}
      ${askable(x.status) ? `<button class="req" data-ask-game>Request</button>` : ""}</div>
    <a class="t" href="${href}">${esc(x.title)}</a>
    <span class="s">${esc(x.year || "")}${x.rating ? ` <span class="star">★</span> ${esc(x.rating)}%` : ""}</span>
  </article>`;
}
const gstrip = (items) => (items?.length ? `<div class="pstrip">${items.map(gcard).join("")}</div>` : "");

const notReady = (s) => (s.ready ? "" : `<div class="panel"><div class="head"><div><h3>Questarr isn't set up yet</h3>
    <p>You can look around; asking for a game works once ${ME?.admin ? "setup has run: Settings → Services → Set up again." : "an admin has finished setting up."}</p></div>
    ${ME?.admin ? `<a class="primary" href="#/settings">Open Settings</a>` : ""}</div></div>`);

async function showGameReq(t, [what = "", ...rest]) {
  if (what === "search") return showGameSearch(t, rest.join("/"));
  if (what === "game") return showGame(t, rest[0]);
  const head = `<section class="home">${aurora}<h1>Games</h1>
    <p>Find a game and ask for it. Questarr looks for it on the indexers and qBittorrent fetches it, behind the VPN like everything else.</p>${subnav()}</section>`;
  paint(t, `${head}<div class="wrap">${skeletons(8, "p", "pstrip")}</div>`);
  const [s, d] = await Promise.all([api("/api/games/requests/status"), api("/api/games/requests/discover")]);
  paint(t, `${head}<div class="wrap">${notReady(s)}
      ${shelf("Popular now", gstrip(d.popular))}
      ${shelf("New and popular", gstrip(d.new))}
      ${shelf("Coming soon", gstrip(d.upcoming))}
      ${d.popular.length || d.new.length || d.upcoming.length ? "" : `<div class="empty-state"><strong>Steam's store didn't answer</strong>The rows come from it. Try again in a moment, or search for a title above.</div>`}
      ${ME?.admin && s.ready ? `<label class="gauto"><input type="checkbox" id="gameAuto"${s.auto ? " checked" : ""}><span>Take a release without asking when it passes every check
        <small>The whole game, its exact title, from a scene group or a known repacker, with people seeding it. Unticked, every request waits for you to pick the release. Either way a game is a program: scan it before you run it.</small></span></label>` : ""}
    </div>`);
  $("#gameAuto")?.addEventListener("change", (e) => put("/api/games/requests/auto", { on: e.target.checked }).then((r) => toast(r.auto ? "Parlor takes sure releases by itself" : "Every request waits for your pick"), (err) => { e.target.checked = !e.target.checked; toast(err.message); }));
}

async function showGameSearch(t, q) {
  input.value = q;
  const r = await api(`/api/games/requests/search?q=${encodeURIComponent(q)}`);
  paint(t, r.items.length ? `<div class="wrap">${shelf("Games", `<div class="pgrid">${r.items.map(gcard).join("")}</div>`)}</div>`
    : `<div class="note"><strong>No game matches “${esc(q)}”</strong>The catalogue is Steam's store: try the title as Steam has it. A game Steam doesn't sell isn't found here.</div>`);
}

function gameRow(x) {
  const b = (what, label, cls = "") => `<button class="ghost small ${cls}" data-greq="${what}" data-id="${esc(x.id)}">${label}</button>`;
  const acts = [
    ME?.admin && x.state === "pending" ? b("approve", "Approve") + b("decline", "Decline", "danger") : "",
    (x.mine || ME?.admin) && ["searching", "choose", "failed"].includes(x.state) ? b("retry", "Search again") : "",
    x.mine || ME?.admin ? b("cancel", x.state === "available" || x.state === "declined" ? "Remove" : "Cancel", "danger") : "",
  ].join("");
  const picks = x.picks.length ? `<div class="mpicks gpicks">${x.picks.map((p, i) => `<button class="mpick" data-greq="pick" data-id="${esc(x.id)}" data-pick="${i}">
      <span><b>${esc(p.title)}</b><small>${esc([p.size ? bytes(p.size) : "", p.seeders != null ? plural(p.seeders, "seeder") : "", p.indexer, (p.kind || "").replace("_", " "), p.sure ? "passes every check" : ""].filter(Boolean).join(" · "))}</small></span></button>`).join("")}</div>` : "";
  return `<li class="mreq ${esc(x.state)}">${img(x.image)}
    <div><a class="t" href="#/gamereq/game/${esc(x.app)}">${esc(x.title)}</a><span class="s">${esc([x.year, "Game", x.by].filter(Boolean).join(" · "))}</span>
      <div class="s">${gamePill(x.state, x.label)}</div>
      ${x.why ? `<p class="why">${esc(x.why)}</p>` : ""}${x.release ? `<p class="why">Release: ${esc(x.release)}</p>` : ""}${picks}</div>
    <div class="acts">${acts}</div></li>`;
}

/** the game requests into #gameReqs under `title`, and how many there are; nothing at all when there are none */
async function drawGameRequests(t, title) {
  const box = $("#gameReqs");
  const r = await api("/api/games/requests").catch(() => null);
  if (!box || t !== ticket) return 0;
  if (!r) return (box.innerHTML = `<p class="dim">The game requests couldn't be loaded just now.</p>`).length;  // said, so the page doesn't claim there are none
  box.dataset.title = title;
  box.innerHTML = r.items.length ? shelf(title || "Your requests", `<ul class="reqs mreqs greqs">${r.items.map(gameRow).join("")}</ul>`) : "";
  if (r.items.some((x) => GREQ_ACTIVE.has(x.state) && x.state !== "choose")) setTimeout(() => t === ticket && $("#gameReqs") === box && drawGameRequests(t, title), 15000);
  return r.items.length;
}

async function showGame(t, id) {
  const x = await api(`/api/games/requests/game/${encodeURIComponent(id)}`);
  const q = x.request;
  const action = [
    q ? "" : x.ready ? `<span data-game="${esc(x.id)}" data-title="${esc(x.title)}"><button class="primary" data-ask-game>Request</button></span>` : `<small>Asking works once Questarr is set up.</small>`,
    q && askable(q.state) && x.ready ? `<span data-game="${esc(x.id)}" data-title="${esc(x.title)}"><button class="primary" data-ask-game>Request again</button></span>` : "",
    `<a class="ghost" href="${esc(x.store)}" target="_blank" rel="noopener noreferrer">${ICON.out}On Steam</a>`,
  ].join("");
  const facts = [x.year, x.soon ? "Coming soon" : x.early ? "Early access" : "", x.platforms.map((p) => p.replace(/^PC \((.*)\)$/, "$1")).join(", ")].filter(Boolean);
  const made = [x.developers.length ? `By ${x.developers.join(", ")}` : "", x.publishers.length ? `Published by ${x.publishers.join(", ")}` : "", x.modes.join(", ")].filter(Boolean);
  paint(t, hero({
    kind: "Game", title: x.title, backdrop: x.screenshots[0], image: x.poster, poster: true,
    meta: facts.map((f) => `<span>${esc(f)}</span>`).join("") + (x.rating ? `<span><span class="star">★</span> ${esc(x.rating)}%${x.reviews ? ` of ${esc(x.reviews.toLocaleString())} reviews` : ""}</span>` : "") + (q ? gamePill(q.state, q.label) : ""),
    blurb: x.summary, action,
  }) + `<div class="wrap">
      ${q ? `<div class="shelf-head"><h2>The request</h2></div><ul class="reqs mreqs greqs">${gameRow(q)}</ul>` : ""}
      ${x.genres.length ? `<p class="dim">${esc(x.genres.join(" · "))}</p>` : ""}
      ${made.length ? `<p class="dim">${esc(made.join(" · "))}</p>` : ""}
      ${x.screenshots.length ? `<div class="shelf-head"><h2>Screenshots</h2></div><div class="gshots">${x.screenshots.map((s) => `<img src="${esc(s)}" alt="" loading="lazy">`).join("")}</div>` : ""}
    </div>`);
  if (q && GREQ_ACTIVE.has(q.state) && q.state !== "choose") setTimeout(() => { if (t === ticket) { keepScroll = true; route(); } }, 20000);
}

document.addEventListener("click", async (e) => {
  const ask = e.target.closest("[data-ask-game]");
  if (ask) {
    const { game, title } = ask.closest("[data-game]").dataset;
    ask.disabled = true;
    try {
      const r = await post(`/api/games/requests/game/${encodeURIComponent(game)}`);
      toast(r.state === "pending" ? `Asked for “${title}”; it's waiting for an admin` : r.mine ? `Asked for “${title}”; Parlor is looking for it` : `“${title}” was already asked for`);
      if (location.hash.startsWith("#/gamereq/game/")) { keepScroll = true; route(); } else ask.outerHTML = gamePill(r.state, GREQ_SHORT[r.state]);
    } catch (err) {
      ask.disabled = false;
      toast(err.message);
    }
    return;
  }
  const b = e.target.closest("[data-greq]");
  if (!b) return;
  const what = b.dataset.greq, at = `/api/games/requests/${encodeURIComponent(b.dataset.id)}`;
  if (what === "cancel" && !confirm("Remove this request? A download that has started is cancelled in Questarr.")) return;
  if (what === "pick" && !confirm(`Download “${b.querySelector("b").textContent}”?`)) return;
  b.disabled = true;
  try {
    await (what === "cancel" ? del(at) : post(`${at}/${what}`, what === "pick" ? { pick: +b.dataset.pick, title: b.querySelector("b").textContent } : undefined));
    toast({ approve: "Approved: Parlor is looking for it", decline: "Declined", retry: "Searching again", cancel: "Removed", pick: "Downloading that one" }[what]);
    const box = $("#gameReqs");
    if (box) drawGameRequests(ticket, box.dataset.title); else { keepScroll = true; route(); }
  } catch (err) {
    b.disabled = false;
    toast(err.message);
  }
});

page("gamereq", showGameReq, "gamereq");
