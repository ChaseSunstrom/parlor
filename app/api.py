"""Parlor's API for other programs (scripts, Home Assistant, a phone shortcut, an AI agent).

Keys: each person makes their own in Settings → API keys (shown once, stored hashed). A key acts as its person, with
their sections, and is either "search" (reads only) or "full" (also downloads and requests); an admin's key reaches
what the admin does. Send it as `X-Api-Key: pk_…` or `Authorization: Bearer pk_…`, on any /api route. A key can never
manage keys, passwords, sign-ins or the server's lock.

The stable part is /api/v1: one search across every source, one way to get anything, and a way to follow it. The
other /api routes work with a key too but are shaped for the page and may change."""

import re
import secrets
import time
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import acl
from core import _digest, db, me, one, rows

router = APIRouter()
READS = ("GET", "HEAD")
NEVER = ("/api/me/api-keys", "/api/me/password", "/api/me/app-passwords", "/api/me/sessions", "/api/auth/", "/api/vault/", "/api/private/")  # not with a key, at all
# what a key may change: downloads, requests and your own lists. Accounts, settings, the stack and the server are for
# people who sign in, admins' keys included, so a leaked key can't make itself an account or repoint the server
WRITES = ("/api/v1/", "/api/yt/", "/api/seerr/request", "/api/seerr/sets/", "/api/arr/music/", "/music/api/", "/api/jf/progress", "/api/jf/playback", "/api/jf/stopped",
          "/api/me/history", "/api/me/later", "/api/me/subs", "/api/me/topics")
SCOPES = ("search", "full")


# --- keys ---------------------------------------------------------------------------------------

def user_for_key(key: str, method: str, path: str) -> tuple:
    """(the person this key acts as, None) or (None, (status, why))."""
    row = one("""SELECT k.id AS key_id, k.scope, k.used, u.id, u.name, u.admin, u.jellyfin_id, u.seerr_id, u.pw IS NOT NULL AS local
                 FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?""", _digest(key.strip()))
    if not row:
        return None, (401, "That API key isn't valid (deleted, or mistyped)")
    if path.startswith(NEVER) or path.rstrip("/") in ("/api/me/api-keys", "/api/private"):
        return None, (403, "An API key can't manage keys, passwords, sign-ins, private items or the server's lock; sign in for that")
    if method not in READS and row["scope"] == "search":
        return None, (403, "This key can only search and read; make a full key to download or request")
    if method not in READS and not path.startswith(WRITES):
        return None, (403, "An API key can download, request and change your lists; accounts, settings and the server need a sign-in")
    now = time.time()
    if not row["used"] or now - row["used"] > 60:  # last use, written at most once a minute
        db().execute("UPDATE api_keys SET used = ? WHERE id = ?", (now, row["key_id"]))
    user = {k: row[k] for k in ("id", "name", "jellyfin_id", "seerr_id")}
    user.update(admin=bool(row["admin"]), local=bool(row["local"]), via_key=row["key_id"])
    return user, None


class NewKey(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    scope: str = "full"


@router.get("/api/me/api-keys")
def keys(user=Depends(me)):
    return [{"id": r["id"], "name": r["name"], "scope": r["scope"], "hint": r["hint"] + "…", "created": r["created"], "used": r["used"]}
            for r in rows("SELECT * FROM api_keys WHERE user_id = ? ORDER BY id DESC", user["id"])]


@router.post("/api/me/api-keys")
def make_key(body: NewKey, user=Depends(me)):
    if body.scope not in SCOPES:
        raise HTTPException(422, "scope is search or full")
    if (one("SELECT COUNT(*) AS n FROM api_keys WHERE user_id = ?", user["id"]) or {"n": 0})["n"] >= 25:
        raise HTTPException(409, "25 keys is the most; delete one you don't use")
    key = "pk_" + secrets.token_urlsafe(32)
    cur = db().execute("INSERT INTO api_keys(user_id, name, scope, hint, key_hash, created) VALUES (?, ?, ?, ?, ?, ?)",
                       (user["id"], body.name.strip(), body.scope, key[:7], _digest(key), time.time()))
    return {"id": cur.lastrowid, "name": body.name.strip(), "scope": body.scope, "key": key}  # the only time it's shown


@router.delete("/api/me/api-keys/{key_id}")
def drop_key(key_id: int, user=Depends(me)):
    db().execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user["id"]))
    return {"ok": True}


# --- one search ---------------------------------------------------------------------------------

SOURCES = {"youtube": ("watch",), "seerr": ("media",), "spotify": ("music",), "lidarr": ("music",), "library": ("library",)}
LIMIT = 8.0  # seconds each source gets
_pool = ThreadPoolExecutor(10, thread_name_prefix="api-search")  # shared: a flood of searches can't pile up threads
_searching = threading.BoundedSemaphore(4)


def _open(user, needs) -> bool:
    return user["admin"] or not (set(needs) & acl.blocked(user["id"]))


def _youtube(q, user):
    import yt
    out = []
    for i in yt.search(q, None, user).get("items") or []:
        kind = i.get("type", "video")
        out.append({"kind": kind, "id": i["id"], "title": i.get("title") or i.get("name"), "subtitle": i.get("channel") or "", "year": None,
                    "image": i.get("thumbnail"), "get": [{"kind": "youtube_video", "id": i["id"]}, {"kind": "youtube_audio", "id": i["id"]}] if kind == "video" else []})
    return out


def _seerr(q, user):
    import seerr
    return [{"kind": x["type"], "id": str(x["id"]), "title": x["title"], "subtitle": x.get("status") or "", "year": x.get("year"), "image": x.get("poster"),
             "get": [{"kind": x["type"] if x["type"] == "movie" else "show", "id": str(x["id"])}]}
            for x in seerr.search(q).get("items") or [] if x.get("type") in ("movie", "tv")]


def _names(people) -> str:
    return ", ".join(p.get("name", "") if isinstance(p, dict) else str(p) for p in people or [])


def _spotify(q, user):
    import music
    found = music.search(q)
    out = []
    for kind, key in (("track", "tracks"), ("album", "albums"), ("artist", "artists"), ("playlist", "playlists")):
        for x in found.get(key) or []:
            out.append({"kind": kind, "id": x["id"], "title": x.get("name"), "subtitle": _names(x.get("artists")) or x.get("owner") or "", "year": x.get("year"),
                        "image": x.get("image"), "get": [{"kind": kind, "id": x["id"]}]})
    return out


def _lidarr(q, user):
    import activity
    return [{"kind": x["type"], "id": x["id"], "title": x.get("title") or x.get("name"), "subtitle": x.get("artist") or "", "year": x.get("year"),
             "image": x.get("image"), "get": [{"kind": f"lidarr_{x['type']}", "id": x["id"]}]}
            for x in activity.music_search(q).get("items") or []]


def _library(q, user):
    import jf
    r = jf.items(parent="", types="Movie,Series,Episode,MusicAlbum,MusicArtist,Audio,Book,AudioBook", sort="", order="", start=0, limit=30,
                 search=q, filters="", user=user)
    return [{"kind": x["type"].lower(), "id": x["id"], "title": x["name"], "subtitle": x.get("series") or ", ".join(x.get("artists") or []), "year": x.get("year"),
             "image": x.get("image"), "get": [], "open": f"/#/jf/item/{x['id']}"} for x in r["items"]]


FINDERS = {"youtube": _youtube, "seerr": _seerr, "spotify": _spotify, "lidarr": _lidarr, "library": _library}


@router.get("/api/v1/search", summary="Search everything: YouTube, films and shows, music on Spotify and in Lidarr, your Jellyfin library")
def search(q: str = Query(..., min_length=1, max_length=200), in_: str = Query("", alias="in", description="Sources, comma-separated: "
           + ",".join(SOURCES) + " (default: every one you can use)"), user=Depends(me)):
    """Every source's hits in one shape: `source`, `kind`, `id`, `title`, `subtitle`, `year`, `image`, and `get`, the
    `/api/v1/get` bodies that fetch or request it. Sources you can't use are left out; one that fails is named in
    `errors` and the rest still answer."""
    want = [s for s in in_.split(",") if s] or list(SOURCES)
    if any(s not in SOURCES for s in want):
        raise HTTPException(422, f"Sources are {', '.join(SOURCES)}")
    want = [s for s in want if _open(user, SOURCES[s])]
    items, errors = [], {}
    if not _searching.acquire(blocking=False):
        raise HTTPException(429, "Too many searches at once; try again in a moment")
    try:
        futures = {_pool.submit(FINDERS[s], q.strip(), user): s for s in want}
        done, late = wait(futures, timeout=LIMIT)
    finally:
        _searching.release()
    for f in done:
        try:
            items += [{"source": futures[f], **x} for x in f.result()]
        except HTTPException as exc:
            errors[futures[f]] = exc.detail
        except Exception as exc:  # noqa: BLE001  (one source down never sinks the others)
            errors[futures[f]] = str(exc)[:200] or type(exc).__name__
    for f in late:
        errors[futures[f]] = f"didn't answer in {LIMIT:.0f} s"
    return {"items": items, "errors": errors}


# --- one way to get anything ---------------------------------------------------------------------

GETS = {  # kind: (sections it needs, what it does)
    "youtube_video": (("watch", "downloads"), "download a YouTube video to the server"),
    "youtube_audio": (("watch", "downloads"), "save a YouTube video's audio to the music library"),
    "track": (("music", "downloads"), "download a Spotify song with music-fndr"),
    "album": (("music", "downloads"), "download a Spotify album with music-fndr"),
    "artist": (("music", "downloads"), "download an artist's discography with music-fndr"),
    "playlist": (("music", "downloads"), "download a Spotify playlist with music-fndr"),
    "lidarr_album": (("music",), "ask Lidarr for an album (MusicBrainz id), YouTube if Lidarr can't"),
    "lidarr_artist": (("music",), "ask Lidarr for an artist and everything they make"),
    "movie": (("media",), "request a film through Seerr (TMDB id)"),
    "show": (("media",), "request a show through Seerr (TMDB id); seasons: a list, or all"),
}


class Get(BaseModel):
    kind: str = Field(description=" · ".join(f"{k}: {v[1]}" for k, v in GETS.items()))
    id: str = Field(max_length=100)
    quality: str = Field("1080", max_length=10, description="youtube_video: best, 2160, 1440, 1080, 720, 480 or 360")
    seasons: Optional[list[int]] = Field(None, max_length=100, description="show: which seasons (default: all)")
    title: str = Field("", max_length=300)


@router.post("/api/v1/get", summary="Download or request anything search found")
def get(body: Get, user=Depends(me)):
    """Starts it and answers at once with a `job` to follow at `/api/v1/jobs/{job}`."""
    if body.kind not in GETS:
        raise HTTPException(422, f"kind is one of {', '.join(GETS)}")
    needs = GETS[body.kind][0]
    if not _open(user, needs):
        raise HTTPException(403, f"That needs {' and '.join(acl.SECTIONS[s] for s in needs)}, which isn't open to your account")
    if body.kind.startswith("youtube_"):
        import yt
        job = yt.queue_video(yt.DownloadRequest(id=body.id, kind="video" if body.kind == "youtube_video" else "music", quality=body.quality,
                                                title=body.title), user)
        return {"job": f"youtube:{job['job']}"}
    if body.kind in ("track", "album", "artist", "playlist"):
        import music
        return {"job": f"music:{music.queue_download(body.kind, body.id, body.title or None)['job']}"}
    if body.kind.startswith("lidarr_"):
        import activity
        import arr
        import musicreq
        try:
            got = activity.request_music(body.kind[7:], body.id)
        except arr.ArrError as exc:
            raise HTTPException(502, str(exc)) from exc
        musicreq.add(user["id"], got)
        row = one("SELECT id FROM music_requests WHERE album = ? OR (artist = ? AND album IS NULL) ORDER BY id DESC LIMIT 1",
                  got.get("album_id"), got.get("artist_id"))
        return {"job": f"request:{row['id']}" if row else None, "title": got.get("title")}
    import seerr
    if not re.fullmatch(r"[0-9]{1,9}", body.id):
        raise HTTPException(422, "id is a TMDB id")
    seerr.make_request(seerr.MediaRequest(type="movie" if body.kind == "movie" else "tv", id=int(body.id), seasons=body.seasons or "all"), user)
    return {"job": f"seerr:{'movie' if body.kind == 'movie' else 'tv'}:{body.id}"}


@router.get("/api/v1/jobs/{job}", summary="How something you started is going")
def job(job: str, user=Depends(me)):
    """`state` is the source's own word (queued, downloading, done, failed; or a request's searching, imported…)."""
    m = re.fullmatch(r"(youtube|music|request):([\w-]{1,40})|seerr:(movie|tv):(\d{1,9})", job)
    if not m:
        raise HTTPException(404, "No such job")
    needs = {"youtube": ("watch",), "music": ("music",), "request": ("music",)}.get(m[1] or "", ("media",))
    if not _open(user, needs):
        raise HTTPException(404, "No such job")
    if m[1] == "youtube":  # the drawer shows everyone's downloads, so the same people can follow one (asking twice gives the first job)
        import yt
        try:
            j = yt.find_job(m[2])
        except HTTPException:
            raise HTTPException(404, "No such job") from None
        return {"job": job, "state": j["status"], "progress": j.get("progress"), "title": j.get("title"), "error": j.get("error")}
    if m[1] == "music":
        import music
        with music.lock:
            j = next((j for j in music.jobs if j["job"] == m[2]), None)
            songs = list((j or {}).get("songs", {}).values())
        if not j:
            raise HTTPException(404, "No such job")
        return {"job": job, "state": j["status"], "title": j.get("title"), "error": j.get("error"),
                "done": sum(s["status"] in ("done", "skipped", "duplicate") for s in songs), "total": len(songs)}
    if m[1] == "request":
        r = one("SELECT * FROM music_requests WHERE id = ?", int(m[2])) if m[2].isdigit() else None
        if not r or not (user["admin"] or r["user_id"] == user["id"]):
            raise HTTPException(404, "No such job")
        import musicreq
        return {"job": job, "state": r["state"], "label": musicreq.LABEL.get(r["state"], r["state"]), "why": r["why"], "title": r["title"], "via": r["via"]}
    import seerr
    t = seerr.title(m[3], int(m[4]), user)
    return {"job": job, "state": t.get("status") or "unknown", "title": t.get("title")}
