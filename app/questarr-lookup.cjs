/* Loaded into Questarr before its own code, so it finds games by title with no IGDB keys of yours. Parlor's setup
   copies this file into Questarr's folder (stack.init_files) and the compose file names it in NODE_OPTIONS.

   Questarr looks a game up at IGDB, which wants a Twitch application's id and secret, and the two addresses are fixed
   in its code. Here its id is "parlor" (IGDB_CLIENT_ID in the compose file): calls made with that id go to Parlor
   instead (app/gamelookup.py), which answers in IGDB's shape from Steam's public store. Each carries the key Parlor
   wrote beside this file, so Parlor answers nobody else.
   Give Questarr real IGDB keys in its own Settings and its calls go to IGDB again: this only moves the "parlor" id.

   Questarr's pages may load pictures only from the hosts it lists; Steam's picture host is added to that list.

   Written against Questarr 1.6.0 (pinned in the compose file): tests/live_stack.py searches a title for real. */
const fs = require("node:fs");
const http = require("node:http");

const LOOKUP = process.env.PARLOR_LOOKUP || "http://parlor:8800/lookup/games";
const KEY_FILE = process.env.PARLOR_LOOKUP_KEY || "/app/data/parlor-lookup.key";
const MOVED = new Set(["id.twitch.tv", "api.igdb.com"]);
const key = () => { try { return fs.readFileSync(KEY_FILE, "utf8").trim(); } catch { return ""; } }; // read each time: Parlor may write it after Questarr starts

const real = globalThis.fetch;
globalThis.fetch = function (input, init) {
  let url;
  try { url = new URL(typeof input === "string" ? input : input.url); } catch { return real.apply(this, arguments); }
  if (!MOVED.has(url.hostname) || typeof input !== "string") return real.apply(this, arguments);
  const headers = new Headers((init && init.headers) || {});
  const ours = url.hostname === "id.twitch.tv" ? new URLSearchParams(String((init && init.body) || "")).get("client_id") === "parlor" : headers.get("client-id") === "parlor";
  if (!ours) return real.apply(this, arguments); // your own IGDB keys: IGDB itself
  headers.set("x-parlor-lookup", key());
  return real(LOOKUP + url.pathname, { ...init, headers });
};

const setHeader = http.ServerResponse.prototype.setHeader;
http.ServerResponse.prototype.setHeader = function (name, value) {
  if (String(name).toLowerCase() === "content-security-policy" && typeof value === "string") value = value.replace("img-src ", "img-src https://*.steamstatic.com ");
  return setHeader.call(this, name, value);
};
