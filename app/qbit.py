"""qBittorrent for admins: the torrents list, the everyday actions and speed limits through its API,
and qBittorrent's own page at /qbit/ for everything else, behind Parlor's admin sign-in.

In Parlor's compose qBittorrent lets Parlor in without a login (its setup step whitelists the
containers). Anywhere else, QBITTORRENT_USER and QBITTORRENT_PASSWORD give Parlor a session that
stays on the server: the browser never sees qBittorrent's login, and Parlor's own session cookie
never goes to qBittorrent."""
import os
import re
import time
from typing import List, Optional, Union
from urllib.parse import urlsplit

import anyio
import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import core
import stack
from core import admin

router = APIRouter(prefix="/api/qbit")
page = APIRouter()
base = stack.qbit
_sid: dict = {}  # qBittorrent's session, when it wants a login; "failed": when a login last failed
RETRY_LOGIN = 60  # qBittorrent bans an address after a few failed logins: don't try on every poll

TORRENT = {"name", "state", "progress", "dlspeed", "upspeed", "eta", "size", "category", "added_on", "completion_on", "ratio",
           "num_seeds", "num_leechs", "amount_left", "tags"}
SERVER = {"dl_info_speed", "up_info_speed", "use_alt_speed_limits", "dl_rate_limit", "up_rate_limit", "free_space_on_disk",
          "connection_status", "alltime_dl", "alltime_ul"}
HASH = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# Parlor's word: qBittorrent 5's name first, then qBittorrent 4's
ACTIONS = {"pause": ("stop", "pause"), "resume": ("start", "resume"), "recheck": ("recheck",), "reannounce": ("reannounce",),
           "delete": ("delete",), "category": ("setCategory",)}
EVERY = {"pause", "resume", "recheck", "reannounce"}  # the ones that may take "all"
LOGIN_HELP = ("qBittorrent asks for a login. In Parlor's compose its setup step lets Parlor in; "
              "elsewhere set QBITTORRENT_USER and QBITTORRENT_PASSWORD")


def _send(method, url, headers=None, **kw):
    # qBittorrent refuses changes whose Referer or Origin isn't its own address (its CSRF check)
    headers = {"Referer": base() + "/", "Origin": base(), **(headers or {})}
    cookies = {_sid["name"]: _sid["sid"]} if _sid.get("sid") else None
    return requests.request(method, url, headers=headers, cookies=cookies, allow_redirects=False, timeout=kw.pop("timeout", 30), **kw)


def _login() -> bool:
    user, password = os.environ.get("QBITTORRENT_USER"), os.environ.get("QBITTORRENT_PASSWORD")
    if not (user and password) or time.time() - _sid.get("failed", 0) < RETRY_LOGIN:
        return False
    _sid.pop("sid", None)
    r = _send("POST", base() + "/api/v2/auth/login", data={"username": user, "password": password}, timeout=15)
    # qBittorrent 4 calls it SID; 5 names it after its port (QBT_SID_8080) and answers 204
    found = re.search(r"\b((?:QBT_)?SID(?:_\d+)?)=([^;]+)", r.headers.get("Set-Cookie", ""))
    if r.ok and found:
        _sid.update(name=found[1], sid=found[2], failed=0)
        return True
    _sid["failed"] = time.time()
    return False


def send(method, url, **kw):
    """One request to qBittorrent, signing in once if it asks; trouble comes back in plain words."""
    try:
        r = _send(method, url, **kw)
        if r.status_code == 403 and _login():
            r = _send(method, url, **kw)
    except requests.RequestException as exc:
        raise HTTPException(502, f"qBittorrent didn't answer at {base()} ({type(exc).__name__})") from exc
    if r.status_code == 403:
        r.close()
        raise HTTPException(502, LOGIN_HELP)
    return r


def api(method, path, **kw):
    return send(method, base() + "/api/v2" + path, **kw)


def ok(r):
    if r.status_code >= 400:
        raise HTTPException(502, f"qBittorrent said {r.status_code}: {r.text[:200]}")
    return r


# --- the API Parlor's page uses ---------------------------------------------------------------------

@router.get("/torrents")
def torrents(rid: int = 0, _=Depends(admin)):
    """Everything the first time, then only what changed since `rid` (qBittorrent's own sync)."""
    d = ok(api("GET", "/sync/maindata", params={"rid": max(rid, 0)})).json()
    return {"rid": d.get("rid", 0), "full_update": bool(d.get("full_update")),
            "torrents": {h: {k: v for k, v in t.items() if k in TORRENT} for h, t in (d.get("torrents") or {}).items()},
            "torrents_removed": d.get("torrents_removed") or [],
            # on a partial update, only the categories that changed: the page merges them
            "categories": sorted(d["categories"]) if d.get("categories") is not None else None,
            "categories_removed": d.get("categories_removed") or [],
            "server_state": {k: v for k, v in (d.get("server_state") or {}).items() if k in SERVER}}


class Act(BaseModel):
    hashes: Union[List[str], str]
    delete_files: bool = False
    category: Optional[str] = None


def _hashes(action, hashes) -> str:
    if hashes == "all" and action in EVERY:
        return "all"
    if not isinstance(hashes, list) or not 0 < len(hashes) <= 1000 or not all(isinstance(h, str) and HASH.fullmatch(h) for h in hashes):
        raise HTTPException(422, "Which torrents? (their hashes)")
    return "|".join(hashes)


@router.post("/torrents/{action}")
def act(action: str, body: Act, _=Depends(admin)):
    if action not in ACTIONS:
        raise HTTPException(404, "No such action")
    form = {"hashes": _hashes(action, body.hashes)}
    if action == "delete":
        form["deleteFiles"] = "true" if body.delete_files else "false"
    if action == "category":
        if body.category is None or len(body.category) > 100 or re.search(r"[\x00-\x1f\\]", body.category):
            raise HTTPException(422, "Which category?")
        form["category"] = body.category
    for name in ACTIONS[action]:
        r = api("POST", f"/torrents/{name}", data=form)
        if r.status_code != 404:  # 404: an older qBittorrent, which has the next name
            ok(r)
            return {"ok": True}
    ok(r)


class Limits(BaseModel):
    alt: Optional[bool] = None
    down: Optional[int] = None  # bytes a second; 0 is no limit
    up: Optional[int] = None


@router.put("/limits")
def limits(body: Limits, _=Depends(admin)):
    if any(v is not None and not 0 <= v <= 10**10 for v in (body.down, body.up)):
        raise HTTPException(422, "A speed limit is bytes a second, 0 for none")  # before anything changes
    if body.alt is not None and (ok(api("GET", "/transfer/speedLimitsMode")).text.strip() == "1") != body.alt:
        ok(api("POST", "/transfer/toggleSpeedLimitsMode"))
    for value, name in ((body.down, "setDownloadLimit"), (body.up, "setUploadLimit")):
        if value is not None:
            ok(api("POST", f"/transfer/{name}", data={"limit": str(value)}))
    return {"ok": True}


def refused(said: str) -> bool:
    """Did qBittorrent say no to a torrent it was given? 4 says so with a 200 "Fails."; 5.3 answers
    {"success_count": 0, "failure_count": 1, …} (seen live)."""
    return said == "Fails." or bool(re.search(r'"success_count":\s*0\b', said) and re.search(r'"failure_count":\s*[1-9]', said))


class Add(BaseModel):
    magnet: str
    category: Optional[str] = None


@router.post("/add")
def add(body: Add, _=Depends(admin)):
    link = body.magnet.strip()
    if len(link) > 8000 or not re.match(r"(magnet:\?|https?://)", link, re.I):
        raise HTTPException(422, "A magnet link (magnet:?…) or a link to a .torrent file")
    form = {"urls": link}
    if body.category:
        form["category"] = body.category[:100]
    if refused(ok(api("POST", "/torrents/add", data=form)).text.strip()):
        raise HTTPException(409, "qBittorrent didn't take that link: it's already there, or it isn't a torrent")
    return {"ok": True}


# --- qBittorrent's own page at /qbit/ ----------------------------------------------------------------

FORWARD = ("content-type", "accept", "accept-language", "if-none-match", "if-modified-since")
BACK = ("content-type", "content-length", "cache-control", "etag", "last-modified", "content-disposition", "content-security-policy",
        "x-frame-options", "x-content-type-options", "referrer-policy")


@page.get("/qbit", include_in_schema=False)
def to_page():
    return RedirectResponse("/qbit/", 307)


@page.api_route("/qbit/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def proxy(path: str, request: Request):
    user = await anyio.to_thread.run_sync(core.session_user, core.session_cookie(request))
    if not user:
        raise HTTPException(401, "Sign in first")
    if not user["admin"]:
        raise HTTPException(403, "Only an admin can do that")
    # Parlor speaks to qBittorrent as its own page, so the check qBittorrent would do happens here: a change must
    # come from this site (Origin, or a browser's Sec-Fetch-Site); qBittorrent takes changes only as POST
    if request.method == "POST":
        origin = request.headers.get("origin")
        same = (origin and urlsplit(origin).netloc == request.headers.get("host")) or request.headers.get("sec-fetch-site") == "same-origin"
        if not same:
            raise HTTPException(403, "Not from this site")
    if path.startswith("api/") and request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Not from this site")  # an old qBittorrent may still change things on a GET
    if path.startswith("/") or any(c in path for c in "\\%?#@") or ".." in path.split("/"):
        raise HTTPException(404, "No such page")
    if int(request.headers.get("content-length") or 0) > 50_000_000:
        raise HTTPException(413, "Too big")
    body = await request.body()
    if len(body) > 50_000_000:
        raise HTTPException(413, "Too big")
    # identity: requests would unzip a gzipped page, and qBittorrent's Content-Length would then be wrong
    headers = {**{k: v for k, v in request.headers.items() if k.lower() in FORWARD}, "Accept-Encoding": "identity"}
    r = await anyio.to_thread.run_sync(lambda: send(request.method, f"{base()}/{path}", params=list(request.query_params.multi_items()),
                                                    data=body or None, headers=headers, stream=True))
    out = {k: v for k, v in r.headers.items() if k.lower() in BACK}  # never its Set-Cookie: its session is Parlor's
    location = r.headers.get("Location")
    if location:
        out["Location"] = "/qbit" + location if location.startswith("/") else location
    return StreamingResponse(r.iter_content(64 * 1024), status_code=r.status_code, headers=out, background=BackgroundTask(r.close))
