"""Artwork through Parlor: /api/art?u=<address> fetches a thumbnail or poster once, keeps it on disk, and serves it, so a
screen with no internet (or a browser that blocks other sites) still shows its pictures. Only a few image hosts, only
people signed in (the guard in app.py), no redirects, pictures only, size-capped: never an open proxy."""

import hashlib
import logging
import os
import re
import tempfile
import threading
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

import core

log = logging.getLogger("parlor.art")
router = APIRouter(prefix="/api")
ALLOWED = {"i.ytimg.com", "yt3.ggpht.com", "image.tmdb.org"}  # YouTube's thumbnails and channel pictures, TMDB's posters
TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}  # no SVG: opened on its own, it can run script
MAX_BYTES, CACHE_BYTES, CHUNK = 5 * 1024 * 1024, 200 * 1024 * 1024, 64 * 1024
_PATH = re.compile(r"/[\w\-./~%+@=,]*")  # what image addresses on these hosts hold; no spaces, controls or backslashes
_lock = threading.Lock()  # one eviction at a time


def folder():
    return core.DATA / "cache" / "art"


def clean(url: str) -> str:
    """The address rebuilt from its parts, if it's https on a listed host with no sign-in, port or odd characters."""
    if len(url) > 500 or re.search(r"[\x00-\x20\x7f\\]", url):  # urlsplit silently drops newlines and tabs: say no instead
        raise HTTPException(422, "That isn't an image address")
    try:
        p = urlsplit(url)
        host, port = p.hostname, p.port
    except ValueError:
        raise HTTPException(422, "That isn't an image address") from None
    if p.scheme != "https" or host not in ALLOWED or p.username is not None or p.password is not None or port is not None \
            or "@" in p.netloc or p.netloc != host or not _PATH.fullmatch(p.path):
        raise HTTPException(422, "Parlor only fetches artwork from YouTube and TMDB")
    return f"https://{host}{p.path}" + (f"?{p.query}" if p.query and re.fullmatch(r"[\w=&.\-]{1,100}", p.query) else "")


def kept(name: str):
    return next((f for ext in TYPES.values() if (f := folder() / f"{name}.{ext}").is_file()), None)


def fetch(url: str):
    """(bytes, extension) from the other side, or a plain error; nothing is read past the cap."""
    try:
        r = requests.get(url, stream=True, timeout=(5, 15), allow_redirects=False, headers={"User-Agent": "Parlor"})
    except requests.RequestException as exc:
        log.info("artwork not fetched: %s", type(exc).__name__)
        raise HTTPException(502, "Couldn't fetch that picture") from None
    try:
        if r.status_code == 404:
            raise HTTPException(404, "No such picture")
        if r.status_code != 200:  # a redirect included: it could lead anywhere
            raise HTTPException(502, "Couldn't fetch that picture")
        ext = TYPES.get(r.headers.get("Content-Type", "").split(";")[0].strip().lower())
        if not ext:
            raise HTTPException(415, "That isn't a picture Parlor keeps")
        if (r.headers.get("Content-Length") or "").isdigit() and int(r.headers["Content-Length"]) > MAX_BYTES:
            raise HTTPException(413, "That picture is too big")
        data = bytearray()
        for chunk in r.iter_content(chunk_size=CHUNK):
            data += chunk
            if len(data) > MAX_BYTES:
                raise HTTPException(413, "That picture is too big")
        return bytes(data), ext
    except requests.RequestException:
        raise HTTPException(502, "Couldn't fetch that picture") from None
    finally:
        r.close()


def keep(name: str, data: bytes, ext: str):
    """Write it (0600, atomically), then drop the oldest files while the folder is over its cap. Returns its path."""
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".part-")  # 0600
    final = d / f"{name}.{ext}"
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, final)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    with _lock:
        files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for p in files[:-1]:  # the one just written always stays
            if total <= CACHE_BYTES:
                break
            total -= p.stat().st_size
            p.unlink(missing_ok=True)
    return final


@router.get("/art")
def artwork(u: str = ""):
    url = clean(u)
    name = hashlib.sha256(url.encode()).hexdigest()
    path = kept(name)
    if path:
        os.utime(path)  # used just now: the last to be evicted
    else:
        path = keep(name, *fetch(url))
    kind = next(k for k, v in TYPES.items() if v == path.suffix[1:])
    return FileResponse(path, media_type=kind, headers={"Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff"})
