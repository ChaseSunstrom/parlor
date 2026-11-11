/* What Parlor's page may ask of the desktop app: whether mpv is there, to play a film or episode in it, and to open a retro game in RetroArch. */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("parlorDesktop", {
  mpv: () => ipcRenderer.invoke("mpv"), // { available, playing, position, hwdec }
  play: (item) => ipcRenderer.invoke("play", item), // { id, position, title } -> { ok }
  stop: () => ipcRenderer.invoke("stop"), // close mpv (its place is saved)
  retro: () => ipcRenderer.invoke("retro"), // { available, playing }
  game: (what) => ipcRenderer.invoke("game", what), // { system, file } -> { ok, shared }: the game is open in RetroArch
  onGameClosed: (fn) => ipcRenderer.on("game-closed", (_e, what) => fn(what)), // { system, file, saved }
  onClosed: (fn) => ipcRenderer.on("mpv-closed", (_e, what) => fn(what)), // mpv closed: { id, position }
});
