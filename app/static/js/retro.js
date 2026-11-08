/* A retro game's own page (games.py's play page): EmulatorJS, set up from the page's JSON, with this person's saves
   kept on Parlor instead of in this browser, so they come back on any device. The game's own save (its battery
   memory) goes up whenever it changes, checked every 10 seconds and when the page is hidden; save states go up
   when saved from the emulator's menu and come back from it. */
(() => {
  const game = JSON.parse(document.getElementById("retro").textContent);
  const H = { "X-Requested-With": "parlor" };
  Object.assign(window, {
    EJS_player: "#game", EJS_core: game.core, EJS_gameUrl: game.rom, EJS_gameName: game.name, EJS_pathtodata: "/api/games/emulator/",
    EJS_threads: game.threads, EJS_startOnLoaded: true, EJS_color: "#e8a33d", EJS_Buttons: { netplay: false },
    EJS_disableDatabases: true, // nothing kept in this browser: saves are Parlor's
  });
  const say = (text) => window.EJS_emulator?.displayMessage?.(text);
  const put = (slot, bytes) => fetch(`${game.save}/${slot}`, { method: "PUT", headers: H, body: bytes });
  /** a save's bytes, or null when there's none yet; anything else (no network, Parlor locked) throws */
  const get = async (slot) => {
    const r = await fetch(`${game.save}/${slot}`, { cache: "no-store" });
    if (r.status === 404) return null;
    if (!r.ok) throw new Error(`Parlor answered ${r.status}`);
    return new Uint8Array(await r.arrayBuffer());
  };

  /** the game's own save, written where the core reads it, then read */
  function loadSram(bytes) {
    const gm = EJS_emulator.gameManager, path = gm.getSaveFilePath();
    let dir = "";
    for (const part of path.split("/").slice(0, -1).filter(Boolean)) {
      dir += `/${part}`;
      if (!gm.FS.analyzePath(dir).exists) gm.FS.mkdir(dir);
    }
    if (gm.FS.analyzePath(path).exists) gm.FS.unlink(path);
    gm.FS.writeFile(path, bytes);
    gm.loadSaveFiles();
  }

  let sent = "", known = false; // known: the save on Parlor was read, so writing ours can't lose it
  const sumOf = (b) => { let h = 2166136261; for (const x of b) h = Math.imul(h ^ x, 16777619); return `${b.length}:${h >>> 0}`; }; // (plain http has no crypto.subtle)
  async function keepSram() {
    if (!known) return;
    const file = window.EJS_emulator?.gameManager?.getSaveFile(); // asks the core to write it out first
    if (!file?.length) return;
    const sum = sumOf(file);
    if (sum === sent) return;
    const r = await put("sram", file).catch(() => null);
    if (r?.ok) sent = sum;
  }

  window.EJS_onGameStart = async () => {
    let sram;
    try {
      sram = await get("sram");
    } catch {  // never write over a save we couldn't read: this time it isn't kept
      return say("Couldn't reach your saves on Parlor: this game won't be saved. Reload to try again", 15000);
    }
    known = true;
    if (sram) {
      loadSram(sram);
      sent = sumOf(sram);
      say("Your save is back");
    }
    setInterval(keepSram, 10000);
    document.addEventListener("visibilitychange", () => { if (document.hidden) keepSram(); });
    addEventListener("pagehide", keepSram);
    window.retroReady = true; // for the browser check
  };
  window.EJS_onSaveState = async ({ state }) => say((await put("state", state).catch(() => null))?.ok ? "Saved on Parlor" : "Couldn't save on Parlor");
  window.EJS_onLoadState = async () => {
    const state = await get("state").catch(() => null);
    if (!state) return say("No saved state yet");
    EJS_emulator.gameManager.loadState(state);
    say("Loaded from Parlor");
  };
  window.EJS_onSaveSave = async ({ save }) => say((await put("sram", save).catch(() => null))?.ok ? "Saved on Parlor" : "Couldn't save on Parlor");
  window.EJS_onLoadSave = async () => {
    const sram = await get("sram").catch(() => null);
    if (sram) loadSram(sram);
    say(sram ? "Your save is back" : "Nothing saved yet");
  };
  window.retroKeep = keepSram;
})();
