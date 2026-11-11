"""Parlor: YouTube, movies and TV (Seerr) and your music library (music-fndr) in one app.

Run from the music folder (music-fndr writes relative to the working directory):
    cd /music && uvicorn --app-dir /app app:app
"""

import logging
import threading

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import acl
import api
import auth
import bulk
import core
import jf
import library
import live
import music  # music-fndr, whole (vendored by scripts/vendor-music.sh)
import musicreq
import private
import gamelookup
import games
import pwa
import tv
import tvhw
import security
import seerr
import qbit
import subsonic
import activity
import stack
import vault
import yt

log = logging.getLogger("parlor")
app = FastAPI(title="Parlor", description=api.__doc__, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
              swagger_ui_oauth2_redirect_url=None)  # the docs need a sign-in like the rest of /api
PUBLIC = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup", "/api/auth/signup",
          "/api/tv/code", "/api/tv/claim", "/api/tv/me"} | security.PUBLIC  # a TV pairs before it has a session


@app.middleware("http")
async def guard(request: Request, call_next):
    """Every API call needs a session, and every change needs a header other
    websites can't send without a CORS preflight (which is never granted)."""
    path = request.url.path
    if path.startswith(("/api/", "/music/api/", "/rest/", "/qbit/", "/games/")) and not security.is_open(path):
        return JSONResponse({"detail": "Parlor is locked: enter the passphrase"}, status_code=423)
    if path.startswith(("/api/", "/music/api/")) and (key := request.headers.get("x-api-key") or _bearer(request)):
        # another program, with an API key: no cookies are involved, so no cross-site header is needed either
        user, refused = await anyio.to_thread.run_sync(api.user_for_key, key, request.method, path)
        if refused:
            return JSONResponse({"detail": refused[1]}, status_code=refused[0])
        lacking = await anyio.to_thread.run_sync(acl.allowed, user, path, request.method)
        if lacking:
            return JSONResponse({"detail": f"{lacking} isn't open to your account; ask an admin"}, status_code=403)
        request.state.user = user
        return await call_next(request)
    if path.startswith(("/music/docs", "/music/redoc", "/music/openapi.json")):  # music-fndr's own docs: signed-in people only
        if not await anyio.to_thread.run_sync(core.session_user, core.session_cookie(request)):
            return JSONResponse({"detail": "Sign in first"}, status_code=401)
    if path.startswith(("/api/", "/music/api/")):
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") not in core.HEADERS:
            return JSONResponse({"detail": f"Missing X-Requested-With: {core.HEADER}"}, status_code=403)
        if path not in PUBLIC:
            user = await anyio.to_thread.run_sync(core.session_user, core.session_cookie(request))
            if not user:
                return JSONResponse({"detail": "Sign in first"}, status_code=401)
            lacking = await anyio.to_thread.run_sync(acl.allowed, user, path, request.method)
            if lacking:
                return JSONResponse({"detail": f"{lacking} isn't open to your account; ask an admin"}, status_code=403)
            request.state.user = user
    return await call_next(request)


def _bearer(request: Request) -> str:
    """A Parlor key in Authorization: Bearer; any other bearer token (a proxy's, an SSO's) is left alone."""
    auth = request.headers.get("authorization") or ""
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    return token if token.startswith("pk_") else ""


@app.exception_handler(Exception)
def failed(_: Request, exc: Exception):
    log.exception("request failed")
    return JSONResponse({"detail": "Something went wrong on the server; its log has the details."}, status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(security.router)
app.include_router(acl.router)
app.include_router(api.router)
app.include_router(private.router)
app.include_router(yt.router)
app.include_router(bulk.router)  # /api/seerr/sets: whole collections, studios, networks and keywords (before seerr's own routes)
app.include_router(seerr.router)
app.include_router(stack.router)
app.include_router(activity.router)
app.include_router(musicreq.router)
app.include_router(musicreq.hooks)  # Lidarr's webhook: /hooks/lidarr/<secret>
app.include_router(jf.router)
app.include_router(subsonic.router)  # /rest/*: Subsonic clients, with their own login
app.include_router(qbit.router)
app.include_router(qbit.page)  # /qbit/: qBittorrent's own page, for admins
app.include_router(live.router)  # /api/live: YouTube lives and Twitch, in Parlor's player
app.include_router(games.router)  # /api/games: Steam, retro games, streaming from a PC
app.include_router(gamelookup.router)  # /lookup/games: Questarr's game lookups, answered from Steam's store (its own key, no session)
app.include_router(games.page)  # /games/stream/: the bridge to a gaming PC, behind the sign-in
app.include_router(pwa.router)  # /sw.js: the app kept on devices, for offline
app.include_router(tvhw.router)  # /api/tv/sets, /api/tv/hw: the TV set's own power, input and volume (before tv's /api/tv/{id})
app.include_router(tv.router)  # /api/tv: pairing TVs, casting and the remote
app.include_router(tv.page)  # /tv: Parlor on a TV
app.mount("/music", music.app)  # its API answers at /music/api/...


@app.on_event("startup")
async def startup():
    # each stream to a player holds a worker thread for as long as it plays
    anyio.to_thread.current_default_thread_limiter().total_tokens = 200
    await anyio.to_thread.run_sync(start)


def start():
    if vault.locked():  # the key is sealed by the owner's passphrase: everything waits for it
        log.warning("Parlor is locked: open it in a browser and enter the passphrase")
        security.on_unlock = lambda: threading.Thread(target=start, daemon=True, name="start").start()
        return
    core.db()  # creates the tables (and encrypts an old plain database once)
    vault.lock_down(core.DATA)
    private.sweep()  # what a crash mid-change left behind
    music.others_busy = yt.busy  # music's yt-dlp update waits for video downloads too
    yt.heal_soon = music.heal_soon  # one yt-dlp healer for both
    yt.restarting = lambda: music.healing.is_set() or music.restart_wanted.is_set()
    music.start_worker()  # a mounted app's own startup hook doesn't run
    yt.start()
    library.start()  # keeps the music index (for /rest) current
    bulk.start()  # carries on with whole-set requests a restart interrupted
    musicreq.start()  # follows music requests through Lidarr, YouTube as the fallback
    if stack.on():
        stack.read_keys_from_disk()
        stack.start(retries=10)  # what needs no password; the apps may still be starting
        stack.rechecker()  # the weekly search for better copies


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
