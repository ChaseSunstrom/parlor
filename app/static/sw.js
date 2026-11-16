/* Parlor's service worker. It keeps the app itself (its page, styles, scripts and icons) on the device, so Parlor
   opens with no network. Online, the server's copy always wins (so an update shows on the next load); the kept copy
   is for when the server can't be reached, or takes more than 3 s. Everything with your data — /api, streams,
   music apps — goes to the server and is never kept here; music and videos saved for offline are offline.js's.
   The server fills in the version, the files and the off switch (pwa.py). */
const VERSION = "__VERSION__";
const SHELL = __SHELL__;
const OFF = __OFF__; // OFFLINE=off on the server: this worker removes itself and everything it kept
const CACHE = `parlor-shell-${VERSION}`;
const NEVER = ["/api/", "/music/", "/rest/", "/hooks/", "/qbit/", "/sw.js"];

self.addEventListener("install", (e) => {
  self.skipWaiting(); // the page notices the new worker and offers a reload (pwa.js)
  if (!OFF) e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "no-cache" })))));
});

self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k.startsWith("parlor-") && (OFF || k !== CACHE)) await caches.delete(k);
  if (!OFF) return self.clients.claim();
  await self.registration.unregister();
  for (const c of await self.clients.matchAll({ type: "window" })) c.navigate(c.url);
})()));

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (OFF || req.method !== "GET") return;
  if (url.origin === location.origin) {
    if (NEVER.some((p) => url.pathname.startsWith(p))) return; // your data and streams: the network, always
    e.respondWith(fresh(req, req.mode === "navigate" ? new URL("./", location.href).href : req));
  }
});

/** the server's copy (kept for next time); the kept one when the server fails, errs or is slow. The server is asked
    past the browser's own kept copy ("no-cache": unchanged costs a 304), or an old file the browser still thinks good
    would pass for the server's */
async function fresh(req, key) {
  const cache = await caches.open(CACHE), kept = await cache.match(key);
  const net = fetch(req, { cache: "no-cache" }).then((res) => {
    if (res.ok && res.type === "basic") cache.put(key, res.clone()); // only Parlor's own files get here (NEVER keeps data out): bounded
    return res.ok || !kept ? res : kept; // a proxy's 502 while Parlor restarts: the app still opens
  });
  if (!kept) return net.catch(() => Response.error());
  net.catch(() => {});
  return Promise.race([net, new Promise((r) => setTimeout(r, 3000, kept))]).catch(() => kept);
}
