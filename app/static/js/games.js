/* Games: each person's Steam library (games.py asks Steam with an admin's key), retro games from the server's GAMES
   folder played right here (each opens on its own page, retro.js), and a gaming PC streamed to this browser or TV
   (the Moonlight Web bridge at /games/stream/, behind Parlor's sign-in). */

page("games", showGames, "games");

const hours = (min) => (min >= 60 ? `${Math.round(min / 60).toLocaleString()} h` : `${min} min`);
const SYS_HUE = { nes: 2, snes: 268, n64: 145, gb: 95, gbc: 175, gba: 230, nds: 205, genesis: 215, megadrive: 215, sms: 10, gamegear: 30,
  psx: 220, dos: 45, arcade: 320, pce: 25, atari2600: 35, c64: 250 };

function steamCard(g, wide = false) {
  const played = g.minutes_2weeks != null ? `${hours(g.minutes_2weeks)} in the last 2 weeks` : g.minutes ? `${hours(g.minutes)} played` : "Not played yet";
  return `<div class="gcard steam${wide ? " wide" : ""}"><a href="https://store.steampowered.com/app/${g.appid}/" target="_blank" rel="noopener" title="${esc(g.name)} on Steam's store">
    <span class="gart"><b>${esc(g.name)}</b><img src="${esc(g.art)}" alt="" loading="lazy" onerror="this.remove()"></span>
    <span class="gname">${esc(g.name)}</span></a><small class="dim">${played} · <a href="steam://rungameid/${g.appid}" title="Starts it on a PC with Steam">Play in Steam</a></small></div>`;
}

function retroCard(sys, g) {
  const hue = SYS_HUE[sys.id] ?? 200;
  return `<a class="gcard retro" href="${esc(g.play)}" style="--hue:${hue}" title="Play ${esc(g.name)}">
    <span class="gart">${g.art ? `<img src="${esc(g.art)}" alt="" loading="lazy">` : `<b>${esc(g.name)}</b>`}</span>
    <span class="gname">${esc(g.name)}</span><small class="dim">${ICON.play}Play</small></a>`;
}

async function showGames(t, [part] = []) {
  const [info, steam, retro] = await Promise.all([api("/api/games"), api("/api/games/steam").catch((e) => ({ error: e.message, status: e.status })), api("/api/games/retro")]);
  if (t !== ticket) return;
  const games = retro.systems.reduce((n, s) => n + s.games.length, 0);
  const steamHtml = () => {
    const keyForm = `<form class="actions" id="steamKey"><label class="field grow"><span>Steam Web API key (steamcommunity.com/dev/apikey)</span>
          <input id="steamKeyIn" maxlength="40" autocomplete="off" spellcheck="false" placeholder="32 letters and digits" required></label><button class="primary small">Save</button></form>
          <p class="dim">The key stays on this server; nobody's browser sees it.</p>`;
    if (steam.error) return `<p class="note-inline">${esc(steam.error)}</p>${ME.admin && steam.status === 502 ? keyForm : ""}<button class="ghost" data-unlink>Unlink Steam</button>`;
    if (!steam.linked) {
      return `${!info.steam_key_set ? (ME.admin ? keyForm : `<p class="dim">An admin adds a Steam Web API key here first.</p>`) : ""}
        <form class="actions" id="steamLink"><label class="field grow"><span>Your Steam profile</span><input id="steamProfile" maxlength="200" autocomplete="off"
          placeholder="steamcommunity.com/id/yourname, or its 17-digit id" required></label><button class="primary small">Link</button></form>`;
    }
    if (steam.private) return `<p class="note-inline">${esc(steam.note)}</p><button class="ghost" data-unlink>Unlink ${esc(steam.profile.name || "Steam")}</button>`;
    const all = part === "steam" ? steam.games : steam.games.slice(0, 12);
    return `${steam.recent.length && part !== "steam" ? `<h3>Recently played</h3><div class="gshelf">${steam.recent.map((g) => steamCard(g, true)).join("")}</div>` : ""}
      <h3>${part === "steam" ? `All ${plural(steam.games.length, "game")}` : "Most played"} <small class="dim">· ${esc(steam.profile.name)}</small></h3>
      ${part === "steam" ? `<label class="field gfilter"><span class="vh">Find a game</span><input id="steamFind" placeholder="Find a game" autocomplete="off"></label>` : ""}
      <div class="ggrid" id="steamGrid">${all.map((g) => steamCard(g)).join("")}</div>
      <div class="actions">${part !== "steam" && steam.games.length > 12 ? `<a class="ghost" href="#/games/steam">All ${plural(steam.games.length, "game")}</a>` : ""}<button class="ghost" data-unlink>Unlink Steam</button></div>`;
  };
  const retroHtml = () => (!games ? `<p class="dim">Put games in <b>${esc(retro.folder)}/&lt;system&gt;/</b> (nes, snes, gb, gba, n64, genesis, psx, dos…), with a picture of the same name beside each if you like. Bring your own: only games you own.</p>`
    : retro.systems.map((s) => `<h3>${esc(s.name)} <small class="dim">· ${plural(s.games.length, "game")}</small></h3>
        <div class="ggrid">${(part === "retro" ? s.games : s.games.slice(0, 8)).map((g) => retroCard(s, g)).join("")}</div>`).join("")
      + (part !== "retro" && retro.systems.some((s) => s.games.length > 8) ? `<a class="ghost" href="#/games/retro">Every retro game</a>` : ""));
  const pcHtml = () => `<div class="gpc"><div class="gpc-art" aria-hidden="true">${ICON.screen}<i></i></div><div>
      <p>Play the games on your own PC from here, on a phone, a laptop or the TV: <b>Sunshine</b> on the PC sends the picture, Parlor's bridge brings it to this browser.</p>
      ${!info.stream ? `<p class="dim">It's off on this server. An admin turns it on (README → Games → Play from your PC).</p>`
        : !info.stream_ready && !ME.admin ? `<p class="dim">An admin opens it once first, to set it up.</p>`
        : `<p class="dim">The first time: add your PC by its address, then type the PIN it shows into Sunshine's page on the PC (Sunshine → PIN).</p>
           <a class="primary" href="/games/stream/">${ICON.play}Open</a>`}</div></div>`;
  const sections = { steam: ["Steam", steamHtml], retro: ["Retro", retroHtml], pc: ["From your PC", pcHtml] };
  const shown = part && sections[part] ? [part] : ["steam", "retro", "pc"];
  paint(t, `<section class="home">${aurora}<h1>${part && sections[part] ? sections[part][0] : "Games"}</h1>
      <p>${part === "retro" ? "Played right here, saves kept on this server for every device." : part === "steam" ? "What you own on Steam, most played first." : part === "pc" ? "Your gaming PC, streamed to whatever you're holding." : "Your Steam library, retro games played right here, and your gaming PC streamed to any screen."}</p></section>
    <div class="wrap gamespage">${shown.map((k) => `<section class="panel gsec" id="g-${k}">${shown.length > 1 ? `<h2><a href="#/games/${k}">${sections[k][0]} <small>See all ›</small></a></h2>` : ""}${sections[k][1]()}</section>`).join("")}</div>`);
  $("#steamKey")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await send("PUT")("/api/games/settings", { steam_key: $("#steamKeyIn").value }); route(); } catch (err) { toast(err.message); }
  });
  $("#steamLink")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await send("PUT")("/api/games/steam", { profile: $("#steamProfile").value }); route(); } catch (err) { toast(err.message); }
  });
  $("#steamFind")?.addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    $("#steamGrid").innerHTML = steam.games.filter((g) => g.name.toLowerCase().includes(q)).map((g) => steamCard(g)).join("");
  });
  document.querySelectorAll("[data-unlink]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm("Unlink your Steam profile from Parlor?")) return;
    try { await del("/api/games/steam"); route(); } catch (err) { toast(err.message); }
  }));
}
