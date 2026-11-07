"""yeerr: YouTube, movies and TV (Seerr) and your music library (music-fndr) in one app.

Run from the music folder (music-fndr writes relative to the working directory):
    cd /music && uvicorn --app-dir /app app:app
"""

import logging

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import auth
import core
import music  # music-fndr, whole (vendored by scripts/vendor-music.sh)
import seerr
import yt

log = logging.getLogger("yeerr")
app = FastAPI(title="yeerr", docs_url=None, redoc_url=None, openapi_url=None)
PUBLIC = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/setup", "/api/auth/signup"}


@app.middleware("http")
async def guard(request: Request, call_next):
    """Every API call needs a session, and every change needs a header other
    websites can't send without a CORS preflight (which is never granted)."""
    path = request.url.path
    if path.startswith(("/api/", "/music/api/")):
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != core.HEADER:
            return JSONResponse({"detail": f"Missing X-Requested-With: {core.HEADER}"}, status_code=403)
        if path not in PUBLIC:
            user = await anyio.to_thread.run_sync(core.session_user, request.cookies.get(core.COOKIE, ""))
            if not user:
                return JSONResponse({"detail": "Sign in first"}, status_code=401)
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
app.include_router(yt.router)
app.include_router(seerr.router)
app.mount("/music", music.app)  # its API answers at /music/api/...


@app.on_event("startup")
async def startup():
    # each stream to a player holds a worker thread for as long as it plays
    anyio.to_thread.current_default_thread_limiter().total_tokens = 200
    await anyio.to_thread.run_sync(start)


def start():
    core.db()  # creates the tables
    music.others_busy = yt.busy  # music's yt-dlp update waits for video downloads too
    music.start_worker()  # a mounted app's own startup hook doesn't run
    yt.start()


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")
