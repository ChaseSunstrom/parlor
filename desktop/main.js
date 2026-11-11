/* Parlor's desktop app: the same page as in a browser, in a window of its own, with films and episodes played by mpv.

   The page is the app's own (parlor://app, bundled in www/) and the server is elsewhere, as in the Android app. This
   passes the page's requests for /api, /music and /rest on to the Parlor the person chose, with the cookies the app
   keeps for it (never shown to the page), bodies and Range included, so sign-in, pictures, streams and saving for
   offline work as if the server had served the page.
   /_parlor/server reads the address; ?set=ADDRESS checks it's a Parlor and keeps it; ?set= forgets it and its cookies.

   Play on a film or episode starts mpv on the original file (no converting), with hardware decoding when the machine
   has it. The session cookie reaches mpv over its control socket, never its command line (which other programs can
   read). Every ten seconds, and when mpv closes, the place goes back to Jellyfin through Parlor. No mpv: the page's
   own player plays it. */
const { app, BrowserWindow, protocol, session, ipcMain, shell } = require("electron");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const ORIGIN = "parlor://app";
const WWW = path.join(__dirname, "www");
const PASS = ["/api/", "/music/", "/rest/"];
const SEND = ["range", "accept", "accept-language", "content-type", "x-requested-with", "x-api-key", "if-none-match", "if-range"];
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
  ".png": "image/png", ".json": "application/json", ".webmanifest": "application/manifest+json", ".woff2": "font/woff2", ".wasm": "application/wasm",
  ".ico": "image/x-icon", ".jpg": "image/jpeg", ".mp3": "audio/mpeg", ".map": "application/json", ".txt": "text/plain; charset=utf-8" };
const REPORT_EVERY = 10_000; // ms between the places mpv's playback is saved at

protocol.registerSchemesAsPrivileged([{ scheme: "parlor", privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, allowServiceWorkers: true } }]);
if (process.env.PARLOR_DATA) app.setPath("userData", process.env.PARLOR_DATA); // another profile (the checks use it)
if (!app.requestSingleInstanceLock()) app.exit(0); // already open: that window comes forward ("second-instance" below)
const TV = process.argv.includes("--tv") || process.env.PARLOR_TV === "1"; // the ten-foot layout, full screen (a Pi on a TV)

let upstream; // the session whose cookie jar is the server's: the page's own session never sees those cookies
let win;
const stateFile = () => path.join(app.getPath("userData"), "server.json");
let server = "";
const json = (status, body, headers = {}) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": "no-store", ...headers } });
const offline = () => json(504, { detail: "Can't reach Parlor" }, { "X-Parlor-Offline": "1" }); // the page treats it like no network

/* ---------- the page's files ---------- */

function file(pathname) {
  let rel;
  try { rel = decodeURIComponent(pathname); } catch { return new Response(null, { status: 400 }); }
  const full = path.join(WWW, path.normalize(rel === "/" || rel === "/tv" ? "/index.html" : rel));
  if (!full.startsWith(WWW + path.sep)) return new Response(null, { status: 404 });
  try {
    return new Response(fs.readFileSync(full), { headers: { "content-type": TYPES[path.extname(full).toLowerCase()] || "application/octet-stream", "cache-control": "no-cache" } });
  } catch { return new Response(null, { status: 404 }); }
}

/* ---------- which Parlor ---------- */

async function serverRoute(u) {
  if (!u.searchParams.has("set")) return json(200, { server });
  const set = u.searchParams.get("set").trim();
  if (!set) {
    await stopMpv();
    await forget();
    return json(200, { server: "" });
  }
  let url = set.replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(url)) url = "http://" + url;
  if (!/^https?:\/\/[A-Za-z0-9._\-[\]:]+$/i.test(url)) return json(400, { detail: "That isn't an address: use something like http://192.168.1.20:8800" });
  try {
    const r = await upstream.fetch(url + "/api/auth/state", { signal: AbortSignal.timeout(6000), credentials: "omit" });
    if (r.status !== 200 || !(await r.text()).slice(0, 4096).includes('"setup"')) return json(400, { detail: "Something answered there, but it isn't Parlor" });
  } catch { return json(400, { detail: "Nothing answered at that address. Is it right, and is this computer on the same network?" }); }
  if (url !== server) await forget();
  server = url;
  fs.writeFileSync(stateFile(), JSON.stringify({ server }), { mode: 0o600 });
  return json(200, { server });
}

async function forget() {
  server = "";
  fs.rmSync(stateFile(), { force: true });
  await upstream.clearStorageData({ storages: ["cookies"] });
}

/* ---------- passing the page's requests on ---------- */

async function pass(request, u) {
  if (!server) return offline();
  const headers = {};
  for (const h of SEND) if (request.headers.get(h)) headers[h] = request.headers.get(h);
  const bodied = !["GET", "HEAD"].includes(request.method);
  try {
    const r = await upstream.fetch(server + u.pathname + u.search, { method: request.method, headers, signal: request.signal,
      body: bodied ? Buffer.from(await request.arrayBuffer()) : undefined, redirect: bodied ? "error" : "follow" }); // a body (a password) never follows a redirect elsewhere
    const out = new Headers(r.headers);
    out.delete("set-cookie"); // the app keeps them; the page has no use for them
    if (out.has("content-encoding")) { out.delete("content-encoding"); out.delete("content-length"); } // already unpacked here
    return new Response([101, 204, 205, 304].includes(r.status) ? null : r.body, { status: r.status, headers: out });
  } catch { return offline(); }
}

/* ---------- mpv ---------- */

let mpv = null; // { id, child, sock, dir, position, percent, hwdec, timer, closed }
function findMpv() {
  if (process.env.PARLOR_MPV !== undefined) return fs.existsSync(process.env.PARLOR_MPV) ? process.env.PARLOR_MPV : "";
  const exe = process.platform === "win32" ? "mpv.exe" : "mpv";
  const dirs = (process.env.PATH || "").split(path.delimiter).concat(path.dirname(process.execPath),
    process.platform === "darwin" ? ["/opt/homebrew/bin", "/usr/local/bin", "/Applications/mpv.app/Contents/MacOS"] : []);
  return dirs.filter(Boolean).map((d) => path.join(d, exe)).find((p) => fs.existsSync(p)) || "";
}

const mpvState = () => ({ available: !!findMpv(), playing: mpv ? mpv.id : null, position: mpv ? mpv.position : null, hwdec: mpv ? mpv.hwdec : null });

function saveProgress(m, closing) {
  if (m.position == null || !server) return Promise.resolve();
  const body = closing && m.percent >= 95 ? { id: m.id, played: true } : { id: m.id, position: Math.floor(m.position) };
  return upstream.fetch(server + "/api/jf/progress", { method: "POST", headers: { "content-type": "application/json", "x-requested-with": "parlor" }, body: JSON.stringify(body) }).catch(() => {});
}

function stopMpv() { // done once mpv is gone and its last place is saved
  if (!mpv) return Promise.resolve();
  mpv.child.kill();
  return mpv.closed;
}

function connect(pipe, tries = 60) { // mpv makes its socket a moment after it starts
  return new Promise((resolve, reject) => {
    const attempt = (left) => {
      const sock = net.connect(pipe);
      sock.once("connect", () => { sock.removeAllListeners("error"); resolve(sock); });
      sock.once("error", () => (left ? setTimeout(() => attempt(left - 1), 100) : reject(new Error("mpv didn't open its control socket"))));
    };
    attempt(tries);
  });
}

async function play({ id, position, title }) {
  const exe = findMpv();
  if (!exe || !server || !/^[0-9a-f]{32}$/i.test(String(id))) return { ok: false };
  await stopMpv();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "parlor-mpv-")); // 0700: only this person can reach the socket
  const pipe = process.platform === "win32" ? `\\\\.\\pipe\\parlor-mpv-${crypto.randomBytes(16).toString("hex")}` : path.join(dir, "sock");
  const extra = (process.env.PARLOR_MPV_ARGS || "").split(" ").filter(Boolean);
  // --tls-verify: the session goes only to the real server; --ytdl=no: the address is a file, nothing to look up
  const child = spawn(exe, ["--idle=once", "--force-window=immediate", "--hwdec=auto", "--tls-verify=yes", "--ytdl=no", `--input-ipc-server=${pipe}`,
    `--force-media-title=${String(title || "Parlor").replace(/[\r\n]/g, " ").slice(0, 200)}`, ...extra], { stdio: "ignore" });
  const m = (mpv = { id, child, dir, position: null, percent: 0, hwdec: null, sock: null, timer: null, closed: null });
  m.closed = new Promise((closed) => {
    const gone = async () => { // "exit", or "error" when it couldn't start at all (then there's no exit)
      if (m.gone) return;
      m.gone = true;
      clearInterval(m.timer);
      if (mpv === m) mpv = null;
      fs.rmSync(dir, { recursive: true, force: true });
      await saveProgress(m, true);
      if (win && !win.isDestroyed()) win.webContents.send("mpv-closed", { id, position: m.position });
      closed();
    };
    child.once("exit", gone);
    child.once("error", gone);
  });
  try {
    const sock = (m.sock = await connect(pipe));
    const send = (...command) => sock.write(JSON.stringify({ command }) + "\n");
    let buffer = "";
    sock.on("data", (chunk) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        let msg;
        try { msg = JSON.parse(line); } catch { continue; }
        if (msg.event !== "property-change" || msg.data == null) continue;
        if (msg.name === "time-pos") m.position = msg.data;
        if (msg.name === "percent-pos") m.percent = msg.data;
        if (msg.name === "hwdec-current") m.hwdec = msg.data; // "no" when the machine decodes in software
      }
    });
    sock.on("error", () => {});
    const cookies = await upstream.cookies.get({ url: server });
    send("set_property", "http-header-fields", [`Cookie: ${cookies.map((c) => `${c.name}=${c.value}`).join("; ")}`]);
    if (position > 1) send("set_property", "start", String(Math.floor(position)));
    send("observe_property", 1, "time-pos");
    send("observe_property", 2, "percent-pos");
    send("observe_property", 3, "hwdec-current");
    send("loadfile", `${server}/api/jf/stream/Items/${id}/Download`);
    if (m.gone) return { ok: false };
    m.timer = setInterval(() => saveProgress(m, false), REPORT_EVERY);
    return { ok: true };
  } catch {
    child.kill();
    return { ok: false };
  }
}

/* ---------- the window ---------- */

const ours = (event) => String(event.senderFrame?.url || "").startsWith(ORIGIN + "/"); // only the app's own page may ask

app.whenReady().then(() => {
  upstream = session.fromPartition("persist:parlor-server");
  upstream.cookies.on("changed", () => upstream.cookies.flushStore().catch(() => {})); // on disk at once: a crash or power cut right after signing in doesn't sign the person out
  try { server = JSON.parse(fs.readFileSync(stateFile(), "utf8")).server || ""; } catch { server = ""; }
  protocol.handle("parlor", (request) => {
    const u = new URL(request.url);
    if (u.host !== "app") return new Response(null, { status: 404 });
    if (u.pathname === "/_parlor/server") return request.headers.get("x-parlor-app") === "1" ? serverRoute(u) : json(403, { detail: "Not for other pages" });
    if (PASS.some((p) => u.pathname.startsWith(p))) return pass(request, u);
    return file(u.pathname);
  });
  ipcMain.handle("mpv", (e) => (ours(e) ? mpvState() : null));
  ipcMain.handle("play", (e, item) => (ours(e) ? play(item || {}) : { ok: false }));
  ipcMain.handle("stop", (e) => (ours(e) ? stopMpv().then(() => true) : false));
  win = new BrowserWindow({ fullscreen: TV, width: 1280, height: 820, minWidth: 360, minHeight: 480, title: "Parlor", backgroundColor: "#15131c", autoHideMenuBar: true,
    icon: path.join(WWW, "icon-512.png"), webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false } });
  /* Other sites, Steam's links, and the server's pages this app can't show itself (qBittorrent's, a gaming PC's stream:
     they need WebSockets, which a passed-on request can't carry) open in the person's browser. */
  const mine = (url) => { const u = new URL(url); return u.origin === ORIGIN && (["/", "/tv"].includes(u.pathname) || PASS.some((p) => u.pathname.startsWith(p))); };
  const outside = (url) => {
    const u = new URL(url);
    if (u.origin === ORIGIN) { if (server) shell.openExternal(server + u.pathname + u.search); } else if (["http:", "https:", "steam:"].includes(u.protocol)) shell.openExternal(url);
  };
  win.webContents.setWindowOpenHandler(({ url }) => { outside(url); return { action: "deny" }; });
  win.webContents.on("will-navigate", (e, url) => { if (!mine(url)) { e.preventDefault(); outside(url); } });
  win.loadURL(ORIGIN + (TV ? "/tv" : "/"));
});

app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.on("window-all-closed", () => app.quit());
app.on("before-quit", (e) => { if (mpv) { e.preventDefault(); stopMpv().then(() => app.quit()); } }); // however it quits: mpv closes and its place is saved
