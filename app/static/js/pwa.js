/* Parlor as an app on phones and computers: the service worker that keeps the app itself on the device (sw.js),
   Install in Settings, what's shown with no network (the queue and what's saved for offline still play), and a
   notice when a newer Parlor has taken over an open window. Install and offline need a secure address: https, or
   this computer itself. */

const SECURE = window.isSecureContext;
let installPrompt = null;
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); installPrompt = e; drawDevice(); });
addEventListener("appinstalled", () => { installPrompt = null; drawDevice(); });

if (SECURE && !APPED && "serviceWorker" in navigator) { // the app has its page with it already
  const had = !!navigator.serviceWorker.controller; // the first worker taking over isn't an update
  navigator.serviceWorker.register("sw.js").catch(() => {});
  navigator.serviceWorker.addEventListener("controllerchange", () => { if (had) pwaUpdated(); });
}

function pwaUpdated() {
  if ($("#updateBar")) return;
  document.body.insertAdjacentHTML("beforeend", `<div class="updatebar" id="updateBar" role="status"><span>Parlor was updated</span><button class="primary small" id="updateNow">Reload</button></div>`);
  $("#updateNow").addEventListener("click", () => location.reload());
}

/* no network, or the server can't be reached (a phone away from home): a bar says so, Parlor opens on what this
   device has, and it checks every 10 s until the server answers again */
document.body.insertAdjacentHTML("beforeend", `<a class="offbar" id="offBar" href="#/offline">${ICON.offline}<span role="status">You're offline</span><small>Saved music and videos still play</small></a>`);
let pwaProbe = 0;
function serverGone() {
  document.body.classList.add("offline");
  pwaProbe ||= setInterval(pwaCheck, 10000);
}
async function pwaCheck() {
  try { if ((await fetch("/api/health", { cache: "no-store" })).ok) serverBack(); } catch { /* still gone */ }
}
function serverBack() {
  if (!document.body.classList.contains("offline")) return;
  document.body.classList.remove("offline");
  clearInterval(pwaProbe);
  pwaProbe = 0;
  offRun(); // saving for offline carries on
  jfFlush(); // and the places in films watched with no server get to Jellyfin
  if (!document.body.dataset.offlineStart) return;
  delete document.body.dataset.offlineStart; // opened offline: now open it properly
  if ($("#gate .gate-card")) $("#gate").innerHTML = "";
  boot();
}
addEventListener("offline", serverGone);
addEventListener("online", pwaCheck);

/** who's signed in, kept for opening Parlor offline (just enough to draw it: no keys, no cookies) */
function keepMe() {
  try { localStorage.setItem("parlor:me", JSON.stringify({ id: ME.id, name: ME.name, admin: ME.admin, sections: ME.sections })); } catch { /* private mode */ }
}
function forgetMe() { try { localStorage.removeItem("parlor:me"); } catch { /* private mode */ } }

function bootOffline() {
  let me = null;
  try { me = JSON.parse(localStorage.getItem("parlor:me")); } catch { /* nothing kept */ }
  document.body.dataset.offlineStart = "1";
  serverGone();
  if (!me?.id) {
    $("#gate").innerHTML = `<div class="gate">${aurora}<div class="gate-card"><div class="brand"><img src="logo.svg" alt=""><span>Parlor</span></div>
      <h1>You're offline</h1><p>Parlor can't reach its server. Sign in here once while online, and music and videos you save for offline play without it.</p>
      ${APPED ? `<p>Connecting to <strong>${esc(SERVER)}</strong>. <button class="linkish" id="changeServer">Use another Parlor</button></p>` : ""}</div></div>`;
    return;
  }
  ME = me;
  applyAccess();
  $("#account").textContent = (ME.name || "?").trim()[0].toUpperCase();
  dockRestore();
  if (!location.hash.startsWith("#/offline")) history.replaceState(null, "", "#/offline");
  route();
}

/* ---------- the Android and desktop apps: which Parlor they talk to ---------- */

const APP = { "X-Parlor-App": "1" }; // the app answers /_parlor/server only to the page's own script
/** the app's server, asked of the app itself; none yet: the address screen */
async function appServer() {
  const s = await fetch("/_parlor/server", { headers: APP }).then((r) => r.json()).catch(() => ({}));
  if (s.server) return (SERVER = s.server);
  showServerScreen();
  return "";
}

function showServerScreen(error = "", value = "") {
  ME = null;
  $("#gate").innerHTML = `<div class="gate">${aurora}<form id="serverForm" autocomplete="on">
    <div class="brand"><img src="logo.svg" alt=""><span>Parlor</span></div>
    <h1>Your Parlor</h1><p>The address you open Parlor at in a browser, with its port. On your home network that's often an address like http://192.168.1.20:8800.</p>
    <label class="field"><span>Address</span><input id="serverUrl" type="text" inputmode="url" autocapitalize="off" spellcheck="false" autocomplete="url" required autofocus placeholder="http://parlor.local:8800" value="${esc(value)}"></label>
    <p class="err" id="serverErr" role="alert">${esc(error)}</p>
    <button class="primary">Connect</button></form></div>`;
  $("#serverForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const typed = $("#serverUrl").value.trim(), button = $("#serverForm .primary");
    button.disabled = true;
    const r = await fetch(`/_parlor/server?set=${encodeURIComponent(typed)}`, { headers: APP }).then((x) => x.json()).catch(() => ({}));
    if (!r.server) return showServerScreen(r.detail || "The app couldn't check that address", typed);
    SERVER = r.server;
    $("#gate").innerHTML = "";
    boot();
  });
}

/** Settings → This device in the app: which server, and a way to change it */
function appServerHtml() {
  return APPED ? `<p>Connected to <strong>${esc(SERVER)}</strong>. <button class="linkish" id="changeServer">Use another Parlor</button></p>` : "";
}
document.addEventListener("click", async (e) => {
  if (!e.target.closest("#changeServer") || !confirm("Sign out here and connect to another Parlor?")) return;
  if (ME) await post("/api/auth/logout").catch(() => {});
  await fetch("/_parlor/server?set=", { headers: APP }).catch(() => {}); // the app forgets the address and its cookies
  SERVER = "";
  location.reload();
});

/* ---------- Settings → This device ---------- */

function deviceHtml() {
  const installed = APPED || matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  const apple = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  let text, action = "";
  if (NATIVE) text = "This is Parlor's Android app.";
  else if (DESKTOP) text = "This is Parlor's desktop app. Films and episodes open in mpv when this computer has it, and play in this window when it doesn't.";
  else if (installed) text = "Parlor is installed here, and opens as an app of its own.";
  else if (!SECURE) {
    text = `Installing Parlor and saving music and videos for offline need a secure address, and this one is plain http.
      Put Parlor behind a reverse proxy with a certificate (Caddy gets one on its own), or run <code>tailscale serve</code> on the server,
      and open Parlor at its https address. The Android app works without either.`;
  } else if (installPrompt) {
    text = "Install Parlor to open it like any other app, from the home screen or the dock, with the system's media controls.";
    action = `<div class="actions"><button class="primary small" id="installApp">${ICON.dl}Install Parlor</button></div>`;
  } else if (apple) text = "On iPhone and iPad: the Share button, then Add to Home Screen.";
  else text = "Your browser's menu has Install Parlor (or Add to home screen) when it can install it.";
  const saved = offWorks ? `<p><a href="#/offline">Saved for offline</a>: the music and videos this device keeps, and the space they take.</p>` : "";
  return `<h3>This device</h3><p id="deviceText">${text}</p>${appServerHtml()}${action}${saved}`;
}
function drawDevice() {
  const box = $("#devicePanel");
  if (!box) return;
  box.innerHTML = deviceHtml();
  $("#installApp")?.addEventListener("click", async () => {
    installPrompt.prompt();
    await installPrompt.userChoice.catch(() => {});
    installPrompt = null;
    drawDevice();
  });
}
