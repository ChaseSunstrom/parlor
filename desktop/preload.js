/* What Parlor's page may ask of the desktop app: whether mpv is there, and to play a film or episode in it. */
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("parlorDesktop", {
  mpv: () => ipcRenderer.invoke("mpv"), // { available, playing, position, hwdec }
  play: (item) => ipcRenderer.invoke("play", item), // { id, position, title } -> { ok }
  stop: () => ipcRenderer.invoke("stop"), // close mpv (its place is saved)
  onClosed: (fn) => ipcRenderer.on("mpv-closed", (_e, what) => fn(what)), // mpv closed: { id, position }
});
