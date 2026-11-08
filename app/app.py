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
import auth
import core
import jf
import library
import music  # music-fndr, whole (vendored by scripts/vendor-music.sh)
import security
import seerr
import qbit
import subsonic
import activity
import stack
import vault
import yt

log = logging.getLogger("parlor")
app = FastAPI(title="Parlor", docs_url=None, redoc_url=None, openapi_url=None)
PUBLIC = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup", "/api/auth/signup"} | security.PUBLIC


@app.middleware("http")
async def guard(request: Request, call_next):
    """Every API call needs a session, and every change needs a header other
    websites can't send without a CORS preflight (which is never granted)."""
    path = request.url.path
    if path.startswith(("/api/", "/music/api/", "/rest/", "/qbit/")) and not security.is_open(path):
        return JSONResponse({"detail": "Parlor is locked: enter the passphrase"}, status_code=423)
    if path.startswith(("/api/", "/music/api/")):
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") not in core.HEADERS:
            return JSONResponse({"detail": f"Missing X-Requested-With: {core.HEADER}"}, status_code=403)
        if path not in PUBLIC:
            user = await anyio.to_thread.run_sync(core.session_user, core.session_cookie(request))
            if not user:
                return JSONResponse({"detail": "Sign in first"}, status_code=401)
            lacking = await anyio.to_thread.run_sync(acl.allowed, user, path)
            if lacking:
                return JSONResponse({"detail": f"{lacking} isn't open to your account; ask an admin"}, status_code=403)
            request.state.user = user
    return await call_next(request)


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
app.include_router(yt.router)
app.include_router(seerr.router)
app.include_router(stack.router)
app.include_router(activity.router)
app.include_router(jf.router)
app.include_router(subsonic.router)  # /rest/*: Subsonic clients, with their own login
app.include_router(qbit.router)
app.include_router(qbit.page)  # /qbit/: qBittorrent's own page, for admins
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
    music.others_busy = yt.busy  # music's yt-dlp update waits for video downloads too
    yt.heal_soon = music.heal_soon  # one yt-dlp healer for both
    yt.restarting = lambda: music.healing.is_set() or music.restart_wanted.is_set()
    music.start_worker()  # a mounted app's own startup hook doesn't run
    yt.start()
    library.start()  # keeps the music index (for /rest) current
    if stack.on():
        stack.read_keys_from_disk()
        stack.start(retries=10)  # what needs no password; the apps may still be starting
        stack.rechecker()  # the weekly search for better copies


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
