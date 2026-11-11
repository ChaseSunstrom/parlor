"""YouTube through yt-dlp: search, channels, playlists, ad-free streaming through
this server, captions, SponsorBlock, subscriptions, history, watch later, and
fast downloads into the video folder (or audio into the music library)."""

import calendar
import hashlib
import itertools
import json
import logging
import os
import queue
import re
import shutil
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

import requests
import yt_dlp
from yt_dlp.extractor.youtube import YoutubeIE
from yt_dlp.utils import parse_count, parse_duration
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import core
import vault
from core import admin, db, dumps, me, one, rows

log = logging.getLogger("parlor.yt")
router = APIRouter(prefix="/api")

# whole-string, ASCII-only matches (used with .fullmatch)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
HANDLE = re.compile(r"@[\w.\-·]{2,100}")
LIST_ID = re.compile(r"[A-Za-z0-9_-]{10,64}")
FORMAT_ID = re.compile(r"[A-Za-z0-9_-]{1,24}")

# YouTube refusing this server for a while (same test as music-fndr's)
LIMITED = re.compile(r"rate.?limit|HTTP Error 429|Too Many Requests|confirm you.?re not a bot", re.I)
# app.py points these at music.py's: ask for a new yt-dlp when an error means YouTube changed
# something (True when it does), and whether a restart to load the new one is coming
heal_soon = lambda why: False  # noqa: E731
restarting = lambda: False  # noqa: E731
BASE = {"quiet": True, "no_warnings": True, "socket_timeout": 20, "extractor_retries": 2, "noprogress": True}


def check(pattern, value, what):
    if not pattern.fullmatch(value or ""):
        raise HTTPException(422, f"That isn't a YouTube {what}")
    return value


def thumb(vid, size="mqdefault"):
    return f"https://i.ytimg.com/vi/{vid}/{size}.jpg"


# --- yt-dlp ----------------------------------------------------------------------

def cookie_path(user_id) -> Path:
    return core.DATA / "cookies" / f"{int(user_id)}.txt"


def has_cookies(user_id) -> bool:
    return cookie_path(user_id).is_file()


def _opts(extra, user_id=None):
    opts = {**BASE, "cachedir": str(core.DATA / "cache" / "yt-dlp"), **extra}
    if user_id is not None and has_cookies(user_id):
        # yt-dlp reads cookies from a file and writes them back when it's done: give each call its own
        # plain copy (0600, deleted after the call); the one kept is sealed
        tmp = tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False)
        tmp.write(vault.read(cookie_path(user_id)))
        tmp.close()
        opts["cookiefile"] = tmp.name
    return opts


def plain_error(exc) -> str:
    text = re.sub(r"^(ERROR:\s*)?(\[[\w:]+\]\s*)?([\w-]+:\s*)?", "", str(exc).strip())
    return text.split("\n")[0][:300] or "YouTube lookup failed"


def extract(url, user_id=None, report=None, **extra):
    """yt-dlp's info for a URL. Errors become plain HTTP errors. If YouTube wants a
    sign-in (age limit, bot check) and you've added your cookies, it tries again with them."""
    for with_cookies in (False, True):
        opts = _opts(extra, user_id if with_cookies else None)
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.sanitize_info(y.extract_info(url, download=False))
            if report is not None:
                report["cookies"] = with_cookies
            return info
        except yt_dlp.utils.DownloadError as exc:
            msg = plain_error(exc)
            if not with_cookies and "sign in" in msg.lower() and user_id is not None and has_cookies(user_id):
                continue
            low = msg.lower()
            if "sign in to confirm you" in low and "bot" in low:
                raise HTTPException(503, "YouTube wants this server to prove it isn't a bot. Add your YouTube cookies in "
                                         "Settings, or try again later.") from exc
            if LIMITED.search(low):
                raise HTTPException(429, "YouTube is rate-limiting this server for now (too many requests from it). "
                                         "It usually clears within an hour.") from exc
            if heal_soon(str(exc)):
                raise HTTPException(503, "YouTube changed something; Parlor is updating yt-dlp to match. "
                                         "Try again in a few minutes.") from exc
            if "sign in" in low and not has_cookies(user_id or 0):
                raise HTTPException(403, f"{msg} Add your YouTube cookies in Settings to watch videos that need a sign-in.") from exc
            code = 404 if any(w in low for w in ("unavailable", "private", "not exist", "removed", "terminated", "404")) else 502
            raise HTTPException(code, msg) from exc
        finally:
            if opts.get("cookiefile"):
                Path(opts["cookiefile"]).unlink(missing_ok=True)


class Cache:
    """A small time-limited LRU map."""

    def __init__(self, ttl, size=300):
        self.ttl, self.size, self.data, self.lock = ttl, size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            hit = self.data.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                self.data.move_to_end(key)
                return hit[1]
            return None

    def put(self, key, value):
        with self.lock:
            self.data[key] = (time.time(), value)
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)
        return value

    def drop(self, key):
        with self.lock:
            self.data.pop(key, None)


# --- cards -------------------------------------------------------------------------

def _int(x):
    try:
        return int(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def video_card(e) -> dict:
    vid = e.get("id")
    url = e.get("url") or ""
    return {
        "type": "video",
        "id": vid,
        "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or "",
        "channel_id": e.get("channel_id") or "",
        "duration": _int(e.get("duration")),
        "views": _int(e.get("view_count")),
        "published": _int(e.get("timestamp") or e.get("release_timestamp")),
        "live": e.get("live_status") in ("is_live", "is_upcoming") and e.get("live_status"),
        "short": "/shorts/" in url,
        "thumb": thumb(vid),
    }


def _best_thumb(e, wanted=None):
    thumbs = e.get("thumbnails") or []
    if wanted:
        for t in thumbs:
            if t.get("id") == wanted:
                return t.get("url")
    return thumbs[-1].get("url") if thumbs else None


def entry_card(e):
    """A flat-playlist entry: a video, a channel or a playlist."""
    url, key = e.get("url") or "", e.get("ie_key") or ""
    if key == "Youtube" or (VIDEO_ID.fullmatch(e.get("id") or "") and "list=" not in url and "/channel/" not in url):
        return video_card(e)
    if "/channel/" in url or "/@" in url or CHANNEL_ID.fullmatch(e.get("id") or ""):
        avatar = _best_thumb(e)
        return {"type": "channel", "id": e.get("channel_id") or e.get("id"), "name": e.get("channel") or e.get("title"),
                "handle": e.get("uploader_id"), "avatar": avatar and ("https:" + avatar if avatar.startswith("//") else avatar),
                "followers": _int(e.get("channel_follower_count"))}
    if "list=" in url or key == "YoutubeTab":
        pid = parse_qs(urlparse(url).query).get("list", [e.get("id")])[0]
        return {"type": "playlist", "id": pid, "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader"),
                "channel_id": e.get("channel_id"), "count": _int(e.get("playlist_count")), "thumb": _best_thumb(e)}
    return None


def cards(entries):
    out = []
    for e in entries or []:
        try:
            card = entry_card(e)
        except Exception:  # odd entries (ads, shelves) are skipped, not fatal
            card = None
        if card and card.get("id"):
            out.append(card)
    return out


# --- links and search ---------------------------------------------------------------

def parse_link(q: str):
    """A YouTube link pasted in the search box: what it points at, or None."""
    q = q.strip()
    if not re.match(r"^(https?://)?([\w-]+\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)/", q, re.I):
        return None
    u = urlparse(q if "://" in q else "https://" + q)
    args = parse_qs(u.query)
    path = u.path.rstrip("/")
    if u.netloc.lower().endswith("youtu.be") and VIDEO_ID.fullmatch(path.lstrip("/")):
        return {"type": "video", "id": path.lstrip("/"), "t": _t(args)}
    if VIDEO_ID.fullmatch((args.get("v") or [""])[0]):
        return {"type": "video", "id": args["v"][0], "t": _t(args), "list": (args.get("list") or [None])[0]}
    m = re.fullmatch(r"/(shorts|live|embed|v)/([A-Za-z0-9_-]{11})", path)
    if m:
        return {"type": "video", "id": m[2], "t": _t(args)}
    if LIST_ID.fullmatch((args.get("list") or [""])[0]):
        return {"type": "playlist", "id": args["list"][0]}
    m = re.match(r"/channel/(UC[A-Za-z0-9_-]{22})(?:/|$)", path)
    if m:
        return {"type": "channel", "id": m[1]}
    m = re.match(r"^/(@[\w.\-·]{2,100})", path)
    if m:
        return {"type": "channel", "id": m[1]}
    return None


def _t(args):
    t = (args.get("t") or args.get("start") or [""])[0].rstrip("s")
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(\d+)?", t)
    if not t or not m:
        return None
    return int(m[1] or 0) * 3600 + int(m[2] or 0) * 60 + int(m[3] or 0)


_search = Cache(600)


@router.get("/yt/search")
def search(q: str, request: Request, user=Depends(me)):
    q = q.strip()[:200]
    if not q:
        return {"items": []}
    link = parse_link(q)
    if link:
        return {"link": link}
    hit = _search.get(q.casefold())
    if hit is None:
        info = extract(f"https://www.youtube.com/results?search_query={quote(q)}", extract_flat=True, playlistend=40)
        hit = _search.put(q.casefold(), cards(info.get("entries")))
    return {"items": mark(hit, user["id"])}


def mark(items, user_id):
    """Add what this user has done with each video (progress, watch later, downloaded)
    and whether they follow each channel."""
    if any(i.get("type") == "channel" for i in items):
        subbed = {r["channel_id"] for r in rows("SELECT channel_id FROM subs WHERE user_id = ?", user_id)}
        items = [{**i, "subscribed": i["id"] in subbed} if i.get("type") == "channel" else i for i in items]
    ids = [i["id"] for i in items if i.get("type", "video") == "video"]
    if not ids:
        return items
    marks = ",".join("?" * len(ids))
    seen = {r["video_id"]: r["position"] for r in rows(
        f"SELECT video_id, position FROM history WHERE user_id = ? AND video_id IN ({marks})", user_id, *ids)}
    later = {r["video_id"] for r in rows(f"SELECT video_id FROM later WHERE user_id = ? AND video_id IN ({marks})", user_id, *ids)}
    have = {r["id"] for r in rows(f"SELECT id FROM videos WHERE id IN ({marks})", *ids)}
    return [{**i, "position": seen.get(i["id"]), "later": i["id"] in later, "downloaded": i["id"] in have}
            if i.get("type", "video") == "video" else i for i in items]


# --- one video ------------------------------------------------------------------------

_info = Cache(2 * 3600, 200)  # stream URLs last about six hours


def video_info(vid, user_id=None, fresh=False):
    """A video's info, cached. What needed someone's cookies (age-restricted or
    members-only videos) is cached for that person alone."""
    if not fresh:
        hit = _info.get((vid, user_id)) or _info.get((vid, None))
        if hit:
            return hit
    used = {}
    info = extract(f"https://www.youtube.com/watch?v={vid}", user_id, report=used, noplaylist=True)
    if info.get("live_status") == "is_upcoming":
        return info  # not kept: once it starts, the next look should see it live
    return _info.put((vid, user_id if used.get("cookies") else None), info)


def _mime(f, kind):
    container = "mp4" if f.get("ext") in ("mp4", "m4a") else "webm"
    codec = f.get("vcodec") if kind == "video" else f.get("acodec")
    return f'{kind}/{container}; codecs="{codec}"'


def stream_formats(info):
    """The separate video and audio streams the player can use, best first."""
    video, audio, muxed = [], [], []
    for f in info.get("formats") or []:
        if f.get("protocol") != "https" or f.get("has_drm") or not f.get("url"):
            continue
        v, a = f.get("vcodec") or "none", f.get("acodec") or "none"
        size = f.get("filesize") or f.get("filesize_approx")
        if v != "none" and a == "none":
            video.append({"id": f["format_id"], "height": f.get("height"), "fps": f.get("fps"), "codec": v.split(".")[0],
                          "mime": _mime(f, "video"), "size": size, "hdr": (f.get("dynamic_range") or "SDR") != "SDR",
                          "tbr": f.get("tbr")})
        elif a != "none" and v == "none":
            if "drc" in f["format_id"] or a.split(".")[0] not in ("mp4a", "opus") or (f.get("audio_channels") or 2) > 2:
                continue  # compressed-range copies, and surround tracks (browsers mix them down to stereo anyway)
            note = f.get("format_note") or ""
            audio.append({"id": f["format_id"], "abr": f.get("abr"), "codec": a.split(".")[0], "mime": _mime(f, "audio"),
                          "size": size, "lang": f.get("language"), "note": note, "original": "original" in note.lower()})
        elif v != "none" and a != "none":
            muxed.append({"id": f["format_id"], "height": f.get("height"), "mime": _mime(f, "video").replace('"' + v, f'"{v}, {a}'), "size": size})
    video.sort(key=lambda f: (f["height"] or 0, f["fps"] or 0, f["tbr"] or 0), reverse=True)
    audio.sort(key=lambda f: (f["original"], f["abr"] or 0), reverse=True)
    return video, audio, muxed


def caption_tracks(info):
    tracks = [{"lang": lang, "name": (subs[0].get("name") or lang), "auto": False}
              for lang, subs in (info.get("subtitles") or {}).items() if lang != "live_chat" and subs]
    spoken = info.get("language") or ""
    for lang, subs in (info.get("automatic_captions") or {}).items():
        # auto captions come in ~150 machine translations; keep the original
        if subs and (lang.endswith("-orig") or (spoken and lang == spoken)) and not any(t["lang"] == lang for t in tracks):
            tracks.append({"lang": lang, "name": f"{(subs[0].get('name') or lang)} (auto)", "auto": True})
    return tracks


def _heatmap(info, points=100):
    heat = info.get("heatmap") or []
    if len(heat) > points:
        step = len(heat) / points
        heat = [heat[int(i * step)] for i in range(points)]
    return [[round(h["start_time"], 1), round(h["end_time"], 1), round(h["value"], 3)] for h in heat]


@router.get("/yt/video/{vid}")
def video(vid: str, user=Depends(me)):
    check(VIDEO_ID, vid, "video id")
    info = video_info(vid, user["id"])
    v, a, muxed = stream_formats(info)
    h = one("SELECT position FROM history WHERE user_id = ? AND video_id = ?", user["id"], vid)
    saved = one("SELECT path, quality FROM videos WHERE id = ?", vid)
    if saved and not inside_videos(saved["path"]):
        saved = None
    return {
        "id": vid,
        "title": info.get("title"),
        "description": info.get("description") or "",
        "channel": info.get("channel") or info.get("uploader"),
        "channel_id": info.get("channel_id"),
        "handle": info.get("uploader_id"),
        "verified": bool(info.get("channel_is_verified")),
        "followers": info.get("channel_follower_count"),
        "published": info.get("timestamp") or info.get("release_timestamp"),
        "upload_date": info.get("upload_date"),
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "comments": info.get("comment_count"),
        "duration": info.get("duration"),
        "chapters": [{"start": c["start_time"], "end": c["end_time"], "title": c.get("title")} for c in info.get("chapters") or []],
        "heatmap": _heatmap(info),
        "thumbnail": info.get("thumbnail") or thumb(vid, "hqdefault"),
        "tags": (info.get("tags") or [])[:20],
        "category": (info.get("categories") or [None])[0],
        "live": info.get("live_status"),
        "age_limit": info.get("age_limit"),
        "video": v,
        "audio": a,
        "muxed": muxed,
        "captions": caption_tracks(info),
        "position": h and h["position"],
        "later": bool(one("SELECT 1 AS x FROM later WHERE user_id = ? AND video_id = ?", user["id"], vid)),
        "subscribed": bool(one("SELECT 1 AS x FROM subs WHERE user_id = ? AND channel_id = ?", user["id"], info.get("channel_id") or "")),
        "downloaded": saved and {"quality": saved["quality"]},
        "music": info.get("track") and {"track": info.get("track"), "artist": info.get("artist"), "album": info.get("album")},
    }


# --- streaming through this server ------------------------------------------------------
# YouTube's stream URLs only work from the address that asked for them, and HD
# comes as separate video and audio streams. The player asks for both here, in
# byte ranges, and this server fetches each range from YouTube.

CHUNK = 10 * 1024 * 1024  # YouTube throttles bigger range requests


def parse_range(header, size):
    """(start, end), inclusive, for a single 'bytes=' range; None for no range.
    `size` may be None when the length isn't known."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
    if not m or not (m[1] or m[2]):
        return None
    if m[1]:
        start = int(m[1])
        end = int(m[2]) if m[2] else (size - 1 if size else None)
    else:  # the last n bytes
        if not size:
            return None
        start, end = max(size - int(m[2]), 0), size - 1
    if size:
        end = min(end, size - 1)
        if start >= size:
            raise HTTPException(416, "Range not satisfiable", headers={"Content-Range": f"bytes */{size}"})
    if end is not None and start > end:
        raise HTTPException(416, "Range not satisfiable")
    return start, end


def _upstream(vid, fmt, start, end, user_id, fresh=False):
    info = video_info(vid, user_id, fresh=fresh)
    f = next((f for f in info.get("formats") or [] if f.get("format_id") == fmt and f.get("protocol") == "https"), None)
    if not f:
        raise HTTPException(404, "That stream isn't available")
    stop = start + CHUNK - 1 if end is None else min(end, start + CHUNK - 1)
    headers = {**(f.get("http_headers") or {}), "Range": f"bytes={start}-{stop}"}
    r = requests.get(f["url"], headers=headers, stream=True, timeout=(10, 30))
    return f, r


@router.get("/yt/stream/{vid}/{fmt}")
def stream(vid: str, fmt: str, request: Request):
    check(VIDEO_ID, vid, "video id")
    check(FORMAT_ID, fmt, "format")
    user_id = request.state.user["id"]
    first = video_info(vid, user_id)
    size = next((f.get("filesize") for f in first.get("formats") or [] if f.get("format_id") == fmt), None)
    start, end = parse_range(request.headers.get("range"), size) or (0, None)
    f, r = _upstream(vid, fmt, start, end, user_id)
    if r.status_code in (403, 410):  # the link expired: look the video up again once
        r.close()
        f, r = _upstream(vid, fmt, start, end, user_id, fresh=True)
    if r.status_code not in (200, 206):
        r.close()
        raise HTTPException(502, f"YouTube answered {r.status_code} for that stream")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    for name in ("content-range", "content-length"):
        if name in r.headers:
            headers[name] = r.headers[name]
    kind = "video" if (f.get("vcodec") or "none") != "none" else "audio"
    return StreamingResponse(r.iter_content(64 * 1024), status_code=r.status_code, media_type=_mime(f, kind).split(";")[0],
                             headers=headers, background=BackgroundTask(r.close))


def send_file(path: Path, request: Request, media_type: str):
    """A file with byte-range support, so videos seek (this Starlette's FileResponse can't)."""
    size = path.stat().st_size
    rng = parse_range(request.headers.get("range"), size)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    if rng is None:
        return FileResponse(path, media_type=media_type, headers=headers)
    start, end = rng

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(256 * 1024, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
    return StreamingResponse(body(), status_code=206, media_type=media_type, headers=headers)


# --- captions, SponsorBlock, comments ---------------------------------------------------

@router.get("/yt/captions/{vid}/{lang}")
def captions(vid: str, lang: str, user=Depends(me)):
    check(VIDEO_ID, vid, "video id")
    info = video_info(vid, user["id"])
    for source in ("subtitles", "automatic_captions"):
        for sub in (info.get(source) or {}).get(lang) or []:
            if sub.get("ext") == "vtt" and sub.get("url"):
                r = requests.get(sub["url"], timeout=20)
                if r.ok:
                    return Response(r.content, media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "private, max-age=3600"})
    raise HTTPException(404, "No captions in that language")


SB_CATEGORIES = ["sponsor", "selfpromo", "interaction", "intro", "outro", "preview", "music_offtopic", "filler", "poi_highlight"]
_segments = Cache(3600, 500)


@router.get("/yt/segments/{vid}")
def segments(vid: str):
    """SponsorBlock's community-marked segments (sponsors, intros...). Asked by a
    hash prefix, so SponsorBlock doesn't learn which video is being watched."""
    check(VIDEO_ID, vid, "video id")
    hit = _segments.get(vid)
    if hit is not None:
        return hit
    prefix = hashlib.sha256(vid.encode()).hexdigest()[:4]
    try:
        r = requests.get(f"https://sponsor.ajay.app/api/skipSegments/{prefix}",
                         params={"categories": json.dumps(SB_CATEGORIES), "actionTypes": json.dumps(["skip", "mute", "poi", "full"])},
                         timeout=8)
        found = r.json() if r.ok else []
    except (requests.RequestException, ValueError):
        return []  # SponsorBlock down: play without it (not cached, so it's asked again next time)
    out = [{"start": s["segment"][0], "end": s["segment"][1], "category": s["category"], "action": s.get("actionType", "skip")}
           for v in found if v.get("videoID") == vid for s in v.get("segments") or []]
    return _segments.put(vid, sorted(out, key=lambda s: s["start"]))


_comments = Cache(1800, 100)


@router.get("/yt/comments/{vid}")
def comments(vid: str, user=Depends(me)):
    check(VIDEO_ID, vid, "video id")
    hit = _comments.get(vid)
    if hit is None:
        info = extract(f"https://www.youtube.com/watch?v={vid}", None, getcomments=True,
                       extractor_args={"youtube": {"max_comments": ["80", "30", "10", "5"], "comment_sort": ["top"]}})
        by_id, top = {}, []
        for c in info.get("comments") or []:
            item = {"id": c.get("id"), "text": c.get("text") or "", "author": c.get("author"), "avatar": c.get("author_thumbnail"),
                    "likes": c.get("like_count"), "pinned": bool(c.get("is_pinned")), "creator": bool(c.get("author_is_uploader")),
                    "hearted": bool(c.get("is_favorited")), "published": c.get("timestamp"), "replies": []}
            by_id[item["id"]] = item
            parent = c.get("parent") or "root"
            (top if parent == "root" else by_id.get(parent, {"replies": top})["replies"]).append(item)
        hit = _comments.put(vid, {"count": info.get("comment_count"), "items": top})
    return hit


# --- channels and playlists ------------------------------------------------------------

_channels = Cache(1800, 200)
TABS = ("videos", "streams", "shorts", "playlists")


@router.get("/yt/channel/{cid}")
def channel(cid: str, tab: str = "videos", user=Depends(me)):
    if not (CHANNEL_ID.fullmatch(cid) or HANDLE.fullmatch(cid)):
        raise HTTPException(422, "That isn't a YouTube channel")
    if tab not in TABS:
        raise HTTPException(422, f"tab is one of {', '.join(TABS)}")
    key = (cid, tab)
    hit = _channels.get(key)
    if hit is None:
        path = f"channel/{cid}" if cid.startswith("UC") else quote(cid)
        info = extract(f"https://www.youtube.com/{path}/{tab}", extract_flat=True, playlistend=60,
                       extractor_args={"youtubetab": {"approximate_date": [""]}})
        avatar = _best_thumb(info, "avatar_uncropped")
        hit = _channels.put(key, {
            "id": info.get("channel_id") or cid,
            "name": info.get("channel") or info.get("uploader") or "",
            "handle": info.get("uploader_id"),
            "followers": info.get("channel_follower_count"),
            "description": info.get("description") or "",
            "avatar": avatar,
            "banner": _best_thumb(info, "banner_uncropped"),
            "verified": bool(info.get("channel_is_verified")),
            "tab": tab,
            "items": [{**c, "channel": c.get("channel") or info.get("channel"), "channel_id": c.get("channel_id") or info.get("channel_id")}
                      for c in cards(info.get("entries"))],
        })
        if avatar and hit["id"]:  # subscriptions list shows the face
            db().execute("UPDATE subs SET avatar = ?, name = COALESCE(NULLIF(name, ''), ?) WHERE channel_id = ?",
                         (avatar, hit["name"], hit["id"]))
    sub = one("SELECT 1 AS x FROM subs WHERE user_id = ? AND channel_id = ?", user["id"], hit["id"])
    return {**hit, "items": mark(hit["items"], user["id"]), "subscribed": bool(sub)}


_playlists = Cache(1800, 100)


@router.get("/yt/playlist/{pid}")
def playlist(pid: str, user=Depends(me)):
    check(LIST_ID, pid, "playlist id")
    hit = _playlists.get(pid)
    if hit is None:
        info = extract(f"https://www.youtube.com/playlist?list={pid}", user["id"], extract_flat=True, playlistend=300)
        hit = _playlists.put(pid, {"id": pid, "title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
                                   "channel_id": info.get("channel_id"), "description": info.get("description") or "",
                                   "count": info.get("playlist_count"), "items": [c for c in cards(info.get("entries")) if c["type"] == "video"]})
    return {**hit, "items": mark(hit["items"], user["id"])}


# --- subscriptions feed ------------------------------------------------------------------

NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015", "m": "http://search.yahoo.com/mrss/"}
_feeds = Cache(20 * 60, 2000)


def parse_feed(xml: bytes, cid: str):
    root = ET.fromstring(xml)
    name = root.findtext("a:title", "", NS)
    items = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", "", NS)
        if not VIDEO_ID.fullmatch(vid):
            continue
        link = e.find("a:link", NS)
        stats = e.find("m:group/m:community/m:statistics", NS)
        published = e.findtext("a:published", "", NS)
        try:
            ts = calendar.timegm(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S"))  # YouTube gives UTC
        except ValueError:
            ts = None
        items.append({"type": "video", "id": vid, "title": e.findtext("a:title", "", NS),
                      "channel": e.findtext("a:author/a:name", name, NS), "channel_id": cid,
                      "views": _int(stats.get("views")) if stats is not None else None, "duration": None,
                      "published": ts, "short": link is not None and "/shorts/" in (link.get("href") or ""),
                      "live": None, "thumb": thumb(vid)})
    return name, items


def channel_feed(cid):
    """A channel's newest uploads with dates: its RSS feed, or yt-dlp when the feed fails."""
    hit = _feeds.get(cid)
    if hit is not None:
        return hit
    try:
        r = requests.get("https://www.youtube.com/feeds/videos.xml", params={"channel_id": cid}, timeout=10)
        r.raise_for_status()
        name, items = parse_feed(r.content, cid)
    except (requests.RequestException, ET.ParseError):
        info = extract(f"https://www.youtube.com/channel/{cid}/videos", extract_flat=True, playlistend=15,
                       extractor_args={"youtubetab": {"approximate_date": [""]}})
        name = info.get("channel") or ""
        items = [{**c, "channel": c.get("channel") or name, "channel_id": cid} for c in cards(info.get("entries"))]
    return _feeds.put(cid, (name, items))


def merged_feed(channel_ids, limit=60):
    """Newest videos across channels; channels that fail are named, not fatal."""
    items, failed, names = [], [], {}
    with ThreadPoolExecutor(8) as pool:
        for cid, result in zip(channel_ids, pool.map(_safe_feed, channel_ids)):
            if result is None:
                failed.append(cid)
                continue
            names[cid], found = result
            items += found
    items.sort(key=lambda i: i.get("published") or 0, reverse=True)
    return items[:limit], failed, names


def _safe_feed(cid):
    try:
        return channel_feed(cid)
    except Exception:
        log.warning("couldn't read channel %s", cid, exc_info=True)
        return None


LIVE_SEEN: dict = {}  # channel id -> (when, its live or scheduled video or None), as Live now (live.py) last found it


def live_seen(item):
    """the item marked live or upcoming when Live now found it so in the last ten minutes; never asks YouTube"""
    when, card = LIVE_SEEN.get(item.get("channel_id"), (0, None))
    if card and card["id"] == item["id"] and time.time() - when < 600:
        return {**item, "live": card["live"]}
    return item


@router.get("/yt/feed")
def feed(limit: int = 90, user=Depends(me)):
    subs = rows("SELECT channel_id, name FROM subs WHERE user_id = ?", user["id"])
    if not subs:
        return {"items": [], "channels": 0, "failed": []}
    items, failed, names = merged_feed([s["channel_id"] for s in subs], max(1, min(limit, 300)))
    for s in subs:  # fill in names imported without one
        if not s["name"] and names.get(s["channel_id"]):
            db().execute("UPDATE subs SET name = ? WHERE user_id = ? AND channel_id = ?", (names[s["channel_id"]], user["id"], s["channel_id"]))
    known = {s["channel_id"]: s["name"] or names.get(s["channel_id"]) or s["channel_id"] for s in subs}
    return {"items": mark([live_seen(i) for i in items], user["id"]), "channels": len(subs), "failed": [known[c] for c in failed]}


ACCOUNT_FEEDS = {"recommended": ":ytrec", "subscriptions": ":ytsubs", "later": ":ytwatchlater", "history": ":ythis"}


@router.get("/yt/account/{kind}")
def account_feed(kind: str, user=Depends(me)):
    """Your own YouTube feeds, read with the cookies you added in Settings."""
    if kind not in ACCOUNT_FEEDS:
        raise HTTPException(404, "No such feed")
    if not has_cookies(user["id"]):
        raise HTTPException(409, "Add your YouTube cookies in Settings to see your own YouTube feeds")
    info = extract(ACCOUNT_FEEDS[kind], user["id"], extract_flat=True, playlistend=60)
    return {"items": mark([c for c in cards(info.get("entries")) if c["type"] == "video"], user["id"])}


@router.get("/yt/recommended")
def recommended(user=Depends(me)):
    """With your YouTube cookies: YouTube's own recommendations. Without: new
    videos from channels you watch but haven't subscribed to, then your subscriptions."""
    if has_cookies(user["id"]):
        try:
            return {**account_feed("recommended", user), "source": "youtube"}
        except HTTPException as exc:
            log.warning("account recommendations failed: %s", exc.detail)
    recent = [(r["video_id"], json.loads(r["item"]).get("channel_id")) for r in rows(
        "SELECT video_id, item FROM history WHERE user_id = ? ORDER BY watched DESC LIMIT 300", user["id"])]
    watched = Counter(c for _, c in recent if c)
    subbed = {r["channel_id"] for r in rows("SELECT channel_id FROM subs WHERE user_id = ?", user["id"])}
    channels = [c for c, _ in watched.most_common(30) if c not in subbed][:15] or list(subbed)[:15]
    seeds, seeded = [], set()  # your latest videos, one per channel: their related videos come first
    for v, c in recent[:60]:
        if len(seeds) < 6 and (c or v) not in seeded:
            seeded.add(c or v)
            seeds.append(v)
    if not channels and not seeds:
        return {"items": [], "source": "none"}
    with ThreadPoolExecutor(6) as pool:
        groups = list(pool.map(related, seeds))
    if channels:
        by_channel = OrderedDict()
        for i in merged_feed(channels, 120)[0]:
            by_channel.setdefault(i["channel_id"], []).append(i)
        groups += by_channel.values()
    # take one from each group in turn so no single video or channel fills the page
    seen = {r["video_id"] for r in rows("SELECT video_id FROM history WHERE user_id = ?", user["id"])}
    mixed = []
    for i in (i for row in itertools.zip_longest(*groups) for i in row if i):
        if i["id"] not in seen:
            seen.add(i["id"])
            mixed.append(i)
    return {"items": mark(mixed[:60], user["id"]), "source": "local"}


# --- browsing: what YouTube suggests next, and topics -----------------------------------

AGO = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
SPAN = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}


def _ago(text, now=None):
    """'9 years ago' as a rough timestamp."""
    m = AGO.search(text or "")
    return int((now or time.time()) - int(m[1]) * SPAN[m[2].lower()]) if m else None


def lockup_card(item):
    """A video in YouTube's "lockup" layout (its Up next list) as a card; None for shelves and the rest."""
    lv = item.get("lockupViewModel") or {}
    if lv.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO" or not VIDEO_ID.fullmatch(lv.get("contentId") or ""):
        return None
    meta = lv["metadata"]["lockupMetadataViewModel"]
    parts = [p for row in meta["metadata"]["contentMetadataViewModel"]["metadataRows"] for p in row.get("metadataParts", [])]
    said = lambda end: next((p for p in parts if (p.get("accessibilityLabel") or "").endswith(end)), {})  # noqa: E731
    raw = json.dumps(lv)
    badge = re.search(r'"thumbnailBadgeViewModel": \{"text": "([^"]+)"', raw)
    channel_id = re.search(r'"browseId": "(UC[\w-]{22})"', raw)
    badge = badge and badge[1]
    return video_card({
        "id": lv["contentId"], "title": meta["title"]["content"], "channel": parts[0]["text"]["content"] if parts else "",
        "channel_id": channel_id and channel_id[1], "duration": parse_duration(badge) if badge else None,
        "view_count": parse_count((said("views").get("text") or {}).get("content")),
        "timestamp": _ago(said("ago").get("accessibilityLabel")), "live_status": "is_live" if badge == "LIVE" else None,
    })


def lockup_cards(data):
    results = data["contents"]["twoColumnWatchNextResults"]["secondaryResults"]["secondaryResults"]["results"]
    out = []
    for r in results:
        try:
            card = lockup_card(r)
        except (KeyError, IndexError, TypeError):  # an odd item is skipped, not fatal
            card = None
        if card:
            out.append(card)
    return out


_related = Cache(6 * 3600, 500)
_related_miss = Cache(5 * 60, 500)  # failures and empty answers: retried after a few minutes, not on every page


def related(vid):
    """The videos YouTube suggests beside this one; empty if YouTube's answer can't be read."""
    hit = _related.get(vid) or _related_miss.get(vid)
    if hit is not None:
        return hit
    try:
        # ponytail: yt-dlp's private innertube helper (it keeps it working as YouTube changes);
        # callers fall back to the channel's videos when this comes back empty
        with yt_dlp.YoutubeDL(_opts({})) as y:
            data = YoutubeIE(y)._call_api("next", {"videoId": vid}, vid, note=False)
        found = lockup_cards(data)
    except Exception as exc:
        log.warning("related videos for %s: %s", vid, exc)
        found = []
    return (_related if found else _related_miss).put(vid, found)


@router.get("/yt/related/{vid}")
def related_videos(vid: str, user=Depends(me)):
    return {"items": mark(related(check(VIDEO_ID, vid, "video")), user["id"])}


# YouTube's search filter for videos from this week. Sorting the search by views instead
# brings the week's biggest videos in any language, about anything; relevance then views stays on topic.
TOPIC_FILTER = "EgQIAxAB"
DEFAULT_TOPICS = ["Music", "Gaming", "Science", "Comedy", "Cooking", "Technology", "Sports", "Movie trailers"]
_topic = Cache(3600, 100)


@router.get("/yt/topic")
def topic(q: str, user=Depends(me)):
    """This week's most-watched videos about something (YouTube dropped its Trending page)."""
    q = q.strip()[:60]
    if not q:
        raise HTTPException(422, "Which topic?")
    hit = _topic.get(q.casefold())
    if hit is None:
        info = extract(f"https://www.youtube.com/results?search_query={quote(q)}&sp={TOPIC_FILTER}", extract_flat=True, playlistend=30)
        videos = [c for c in cards(info.get("entries")) if c["type"] == "video"]
        hit = _topic.put(q.casefold(), sorted(videos, key=lambda c: -(c["views"] or 0)))
    return {"items": mark(hit, user["id"])}


class Topics(BaseModel):
    topics: list[str]


@router.get("/me/topics")
def my_topics(user=Depends(me)):
    r = one("SELECT value FROM prefs WHERE user_id = ? AND key = 'topics'", user["id"])
    return {"topics": json.loads(r["value"]) if r else DEFAULT_TOPICS, "suggested": DEFAULT_TOPICS}


@router.put("/me/topics")
def set_topics(body: Topics, user=Depends(me)):
    kept = {}
    for t in body.topics:
        t = " ".join(t.split())[:60]
        if t:
            kept.setdefault(t.casefold(), t)
    topics = list(kept.values())[:20]
    db().execute("INSERT INTO prefs (user_id, key, value) VALUES (?, 'topics', ?) "
                 "ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value", (user["id"], json.dumps(topics)))
    return {"topics": topics}


# --- your subscriptions -------------------------------------------------------------------

class Sub(BaseModel):
    channel_id: str
    name: str = ""
    avatar: Optional[str] = None


@router.get("/me/subs")
def my_subs(user=Depends(me)):
    return rows("SELECT channel_id, name, avatar, added FROM subs WHERE user_id = ? ORDER BY name COLLATE NOCASE", user["id"])


@router.post("/me/subs")
def subscribe(body: Sub, user=Depends(me)):
    check(CHANNEL_ID, body.channel_id, "channel id")
    avatar = body.avatar if (body.avatar or "").startswith("https://") else None
    db().execute("INSERT INTO subs(user_id, channel_id, name, avatar, added) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT DO UPDATE SET name = excluded.name, avatar = COALESCE(excluded.avatar, avatar)",
                 (user["id"], body.channel_id, body.name[:200], avatar, time.time()))
    return {"ok": True}


@router.delete("/me/subs/{cid}")
def unsubscribe(cid: str, user=Depends(me)):
    db().execute("DELETE FROM subs WHERE user_id = ? AND channel_id = ?", (user["id"], cid))
    return {"ok": True}


def find_channels(text: str):
    """Channels in an export: Google Takeout CSV, NewPipe or FreeTube JSON,
    Invidious/FreeTube OPML, or just a list of channel links. -> {id: name}"""
    found = {}
    try:
        data = json.loads(text)
        subs = data.get("subscriptions", data) if isinstance(data, dict) else data
        for s in subs if isinstance(subs, list) else []:
            if isinstance(s, dict):
                m = re.search(r"UC[\w-]{22}", str(s.get("url") or s.get("id") or ""))
                if m:
                    found[m[0]] = str(s.get("name") or "")[:200]
    except ValueError:
        pass
    for m in re.finditer(r'<outline[^>]*?(?:text|title)="([^"]*)"[^>]*?(UC[\w-]{22})', text):
        found.setdefault(m[2], m[1])
    for line in text.splitlines():  # Takeout: Channel Id,Channel Url,Channel Title
        parts = line.split(",")
        if len(parts) >= 3 and CHANNEL_ID.fullmatch(parts[0].strip()):
            found.setdefault(parts[0].strip(), ",".join(parts[2:]).strip())
    for m in re.finditer(r"(?<![\w-])(UC[\w-]{22})(?![\w-])", text):
        found.setdefault(m[1], "")
    return found


class Import(BaseModel):
    text: str


@router.post("/me/subs/import")
def import_subs(body: Import, user=Depends(me)):
    found = find_channels(body.text[:5_000_000])
    handles = list(dict.fromkeys(re.findall(r"(?:youtube\.com/|^|\s)(@[\w.\-]{3,100})", body.text, re.M)))[:50]
    unresolved = []
    for h in handles:  # handles need a lookup each; capped above
        try:
            info = extract(f"https://www.youtube.com/{quote(h)}", extract_flat=True, playlistend=0)
            found.setdefault(info["channel_id"], info.get("channel") or h)
        except (HTTPException, KeyError):
            unresolved.append(h)
    now = time.time()
    db().executemany("INSERT INTO subs(user_id, channel_id, name, added) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                     [(user["id"], cid, name, now) for cid, name in found.items()])
    return {"found": len(found), "unresolved": unresolved}


@router.get("/me/subs/export")
def export_subs(user=Depends(me)):
    esc = lambda s: (s or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")  # noqa: E731
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<opml version="1.1">', "<body>",
             '<outline text="YouTube Subscriptions" title="YouTube Subscriptions">']
    for s in my_subs(user):
        lines.append(f'<outline text="{esc(s["name"])}" title="{esc(s["name"])}" type="rss" '
                     f'xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={s["channel_id"]}"/>')
    lines += ["</outline>", "</body>", "</opml>"]
    return Response("\n".join(lines) + "\n", media_type="text/x-opml; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="parlor-subscriptions.opml"'})


# --- history, resume, watch later -------------------------------------------------------

class Card(BaseModel):
    id: str
    title: str = ""
    channel: str = ""
    channel_id: str = ""
    duration: Optional[float] = None
    views: Optional[int] = None


def clean(card: Card) -> str:
    check(VIDEO_ID, card.id, "video id")
    return dumps({"title": card.title[:300], "channel": card.channel[:200],
                  "channel_id": card.channel_id if CHANNEL_ID.fullmatch(card.channel_id or "") else "",
                  "duration": _int(card.duration), "views": card.views})


def stored(r, extra=None):
    return {"type": "video", "id": r["video_id"], **json.loads(r["item"]), "thumb": thumb(r["video_id"]), **(extra or {})}


class Progress(BaseModel):
    video: Card
    position: float = 0


@router.post("/me/history")
def save_progress(body: Progress, user=Depends(me)):
    db().execute("INSERT INTO history(user_id, video_id, item, position, watched) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT DO UPDATE SET item = excluded.item, position = excluded.position, watched = excluded.watched",
                 (user["id"], body.video.id, clean(body.video), max(0.0, body.position), time.time()))
    return {"ok": True}


@router.get("/me/history")
def history(limit: int = 200, unfinished: bool = False, user=Depends(me)):
    found = rows("SELECT video_id, item, position, watched FROM history WHERE user_id = ? ORDER BY watched DESC LIMIT ?",
                 user["id"], max(1, min(limit, 1000)) * (3 if unfinished else 1))
    items = [stored(r, {"position": r["position"], "watched": r["watched"]}) for r in found]
    if unfinished:  # "continue watching": started, not near the end
        items = [i for i in items if i["position"] > 15 and (not i.get("duration") or i["position"] < i["duration"] - 30)][:limit]
    return {"items": mark(items, user["id"])}


@router.delete("/me/history/{vid}")
def forget(vid: str, user=Depends(me)):
    db().execute("DELETE FROM history WHERE user_id = ? AND video_id = ?", (user["id"], vid))
    return {"ok": True}


@router.delete("/me/history")
def clear_history(user=Depends(me)):
    db().execute("DELETE FROM history WHERE user_id = ?", (user["id"],))
    return {"ok": True}


@router.get("/me/later")
def later(user=Depends(me)):
    found = rows("SELECT video_id, item, added FROM later WHERE user_id = ? ORDER BY added DESC", user["id"])
    return {"items": mark([stored(r, {"added": r["added"]}) for r in found], user["id"])}


@router.post("/me/later")
def add_later(body: Card, user=Depends(me)):
    db().execute("INSERT INTO later(user_id, video_id, item, added) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                 (user["id"], body.id, clean(body), time.time()))
    return {"ok": True}


@router.delete("/me/later/{vid}")
def remove_later(vid: str, user=Depends(me)):
    db().execute("DELETE FROM later WHERE user_id = ? AND video_id = ?", (user["id"], vid))
    return {"ok": True}


# --- your YouTube cookies -------------------------------------------------------------------

class Cookies(BaseModel):
    text: str


@router.put("/me/youtube-cookies")
def set_cookies(body: Cookies, user=Depends(me)):
    lines = [l for l in body.text.splitlines() if l.strip() and not l.startswith("#") or l.startswith("#HttpOnly_")]
    youtube = [l for l in lines if "youtube.com" in l.split("\t")[0]]
    if not youtube or any(len(l.split("\t")) != 7 for l in youtube):
        raise HTTPException(422, "That doesn't look like a cookies.txt (Netscape format) with youtube.com cookies")
    vault.write(cookie_path(user["id"]), ("# Netscape HTTP Cookie File\n" + "\n".join(youtube) + "\n").encode())
    return {"ok": True, "cookies": len(youtube)}


@router.delete("/me/youtube-cookies")
def drop_cookies(user=Depends(me)):
    cookie_path(user["id"]).unlink(missing_ok=True)
    return {"ok": True}


# --- downloads ----------------------------------------------------------------------------
# Jobs run YT_PARALLEL at a time, each with yt-dlp fetching in parallel chunks.
# Videos go to VIDEO_DIR/<channel>/<title> [id].mp4 with the thumbnail, chapters,
# subtitles and metadata embedded (Jellyfin and Plex read them). "music" saves
# the audio, tagged, into the music library instead.

STATE = lambda: core.DATA / "yt-downloads.json"  # noqa: E731  (DATA can change in tests)
QUALITIES = {"best": 4320, "2160": 2160, "1440": 1440, "1080": 1080, "720": 720, "480": 480, "360": 360}
PARALLEL = int(os.environ.get("YT_PARALLEL", "2"))
RUNNING = ("queued", "waiting", "downloading", "processing")
LIMIT_WAIT = 15 * 60  # YouTube says "for up to an hour"
MAX_WAITS = 8
HEAL_WAIT = 60 * 60  # sooner when the new yt-dlp's restart picks waiting jobs back up
MAX_HEALS = 3
HOLD = 15 * 60
jobs: list = []
_jobs_lock = threading.Lock()
_pending: queue.Queue = queue.Queue()


class DownloadRequest(BaseModel):
    id: str
    quality: str = "1080"
    kind: str = "video"  # or "music"
    title: str = ""
    channel: str = ""
    sponsorblock: bool = False  # cut sponsor segments out of the file


def save_jobs():
    with _jobs_lock:
        snapshot = json.loads(json.dumps([{k: v for k, v in j.items() if not k.startswith("_")} for j in jobs]))
    try:
        vault.write(STATE(), json.dumps(snapshot).encode())
    except OSError:
        log.exception("couldn't save the download list")


def restore_jobs():
    try:
        saved = json.loads(vault.read(STATE()))
    except (OSError, ValueError, vault.WrongKey):
        return
    with _jobs_lock:
        jobs[:] = [j for j in saved if isinstance(j, dict) and "job" in j]
        again = [j for j in jobs if j.get("status") in RUNNING]
    for j in reversed(again):  # yt-dlp picks up its .part files where they stopped
        j.update(status="queued", stop=False)
        _pending.put(j)


def wait_then_retry(job, delay=LIMIT_WAIT, why="YouTube is rate-limiting this server"):
    """YouTube is limiting this server (or changed something): try the job again later
    instead of failing it, up to MAX_WAITS times (about two hours)."""
    job["waits"] = job.get("waits", 0) + 1
    if job["waits"] > MAX_WAITS:
        job.update(status="failed", speed=None, eta=None, error="YouTube kept refusing this server for two hours; Retry later")
        return
    job.update(status="waiting", speed=None, eta=None, retry_at=time.time() + delay,
               error=f"{why}; trying again at {time.strftime('%H:%M', time.localtime(time.time() + delay))}")

    def again():
        with _jobs_lock:
            if job.get("status") != "waiting":  # removed or retried by hand meanwhile
                return
            job.update(status="queued", error=None)
        _pending.put(job)
        save_jobs()

    timer = threading.Timer(delay, again)
    timer.daemon = True
    timer.start()


def view(j):
    return {k: v for k, v in j.items() if not k.startswith("_")}


def find_job(job_id):
    j = next((j for j in jobs if j["job"] == job_id), None)
    if not j:
        raise HTTPException(404, "No such download")
    return j


@router.post("/yt/downloads")
def queue_video(body: DownloadRequest, user=Depends(me)):
    check(VIDEO_ID, body.id, "video id")
    if body.kind not in ("video", "music"):
        raise HTTPException(422, "kind is video or music")
    if body.kind == "video" and body.quality not in QUALITIES:
        raise HTTPException(422, f"quality is one of {', '.join(QUALITIES)}")
    with _jobs_lock:
        dupe = next((j for j in jobs if j["id"] == body.id and j["kind"] == body.kind and j["status"] in RUNNING), None)
        if dupe:
            return view(dupe)
        job = {"job": uuid.uuid4().hex[:10], "id": body.id, "kind": body.kind, "quality": body.quality,
               "title": body.title[:300], "channel": body.channel[:200], "sponsorblock": body.sponsorblock,
               "status": "queued", "progress": 0, "speed": None, "eta": None, "size": None, "step": None, "error": None,
               "user_id": user["id"], "added": time.time(), "stop": False, "path": None}
        jobs.insert(0, job)
        finished = [j for j in jobs if j["status"] not in RUNNING]
        for old in finished[200:]:
            jobs.remove(old)
    _pending.put(job)
    save_jobs()
    return view(job)


@router.get("/yt/downloads")
def list_downloads(user=Depends(me)):
    with _jobs_lock:
        return [view(j) for j in jobs]


def own_job(job_id, user):
    job = find_job(job_id)
    if not user["admin"] and job.get("user_id") not in (None, user["id"]):
        raise HTTPException(403, "Only the person who started it, or an admin, can change it")
    return job


@router.delete("/yt/downloads/{job_id}")
def stop_download(job_id: str, user=Depends(me)):
    with _jobs_lock:
        job = own_job(job_id, user)
        if job["status"] in ("downloading", "processing"):
            job["stop"] = True
            return {"ok": True, "stopping": True}
        job["status"] = "removed"  # a queued or waiting one is skipped by the worker
        jobs.remove(job)
    save_jobs()
    return {"ok": True}


@router.post("/yt/downloads/{job_id}/retry")
def retry_download(job_id: str, user=Depends(me)):
    with _jobs_lock:
        job = own_job(job_id, user)
        if job["status"] in RUNNING and job["status"] != "waiting":
            raise HTTPException(409, "That download is already running")
        job.update(status="queued", progress=0, error=None, stop=False, speed=None, eta=None, step=None, waits=0, heals=0)
    _pending.put(job)
    save_jobs()
    return view(job)


def music_settings():
    """(format, yt-dlp's quality, which stream to take) from the Music downloads choice: for the source-audio ones the
    stream already in that codec, so it's copied, not re-encoded"""
    import music  # here, not at the top: music is the bigger module and loads after this one

    fmt, rate = music.SETTINGS["format"], music.SETTINGS["bitrate"]
    own = {"m4a": "bestaudio[ext=m4a]/", "opus": "bestaudio[acodec=opus]/"}.get(fmt, "") if rate == "disable" else ""
    return fmt, "0" if rate in ("disable", "auto") else rate.upper(), own + "bestaudio/best"


def download_args(job) -> list:
    """yt-dlp's own command-line options for the job (parsed by yt-dlp itself, so they stay right)."""
    args = ["--no-playlist", "--concurrent-fragments", "8", "--http-chunk-size", "10M", "--retries", "10",
            "--fragment-retries", "10", "--socket-timeout", "30", "--embed-metadata", "--embed-thumbnail",
            "--convert-thumbnails", "jpg", "--no-mtime", "--windows-filenames"]
    if job["kind"] == "music":
        fmt, quality, stream = music_settings()
        who = "%(artists.0,artist,creator,channel,uploader|Unknown artist)s"
        args += ["-f", stream, "-x", "--audio-format", fmt, "--audio-quality", quality,
                 "--parse-metadata", f"{who}:%(meta_artist)s", "--parse-metadata", f"{who}:%(meta_album_artist)s",
                 "--parse-metadata", "%(album|YouTube)s:%(meta_album)s", "--parse-metadata", "%(track,title)s:%(meta_title)s",
                 # square cover art, like an album's
                 "--ppa", "ThumbnailsConvertor+ffmpeg_o:-c:v mjpeg -vf crop=\"'if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'\"",
                 "--sponsorblock-remove", "music_offtopic",
                 "-o", str(MUSIC_DIR() / f"{who}/%(album|YouTube)s/%(track,title)s.%(ext)s")]
        return args
    h = QUALITIES[job["quality"]]
    # up to 1080p prefer H.264 + AAC (plays everywhere, Jellyfin direct-plays it); above that only VP9/AV1 exist
    fmt = (f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b" if h <= 1080
           else f"bv*[height<={h}]+ba/b")
    args += ["-f", fmt, "--merge-output-format", "mp4", "--write-thumbnail", "--embed-chapters", "--embed-subs",
             "--sub-langs", "en.*,-live_chat", "-o", str(core.VIDEOS / "%(channel,uploader|Unknown channel)s/%(title).180B [%(id)s].%(ext)s")]
    if job.get("sponsorblock"):
        args += ["--sponsorblock-remove", "sponsor,selfpromo,interaction"]
    return args


MUSIC_DIR = lambda: Path(os.environ.get("MUSIC_DIR", str(Path.cwd())))  # noqa: E731  (music-fndr's folder)


def run_download(job):
    with _jobs_lock:
        if job["status"] != "queued":
            return
        job.update(status="downloading", error=None, progress=0, _done=0, retry_at=None)
    save_jobs()
    sizes = {}

    def progress(d):
        if job["stop"]:
            raise yt_dlp.utils.DownloadCancelled("Stopped")
        info = d.get("info_dict") or {}
        if not sizes and info.get("requested_formats"):
            for f in info["requested_formats"]:
                sizes[f["format_id"]] = f.get("filesize") or f.get("filesize_approx") or 0
        total = sum(sizes.values()) or d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if d["status"] == "downloading":
            got = job["_done"] + (d.get("downloaded_bytes") or 0)
            job.update(progress=round(min(99.0, 100 * got / total), 1) if total else None, speed=d.get("speed"),
                       eta=d.get("eta"), size=total or None, title=job["title"] or info.get("title") or "",
                       channel=job["channel"] or info.get("channel") or "")
        elif d["status"] == "finished":
            job["_done"] += d.get("total_bytes") or d.get("downloaded_bytes") or 0

    def processing(d):
        if d["status"] == "started":
            job.update(status="processing", step=d.get("postprocessor"), speed=None, eta=None)

    try:
        parsed = yt_dlp.parse_options(download_args(job))
        opts = {**parsed.ydl_opts, **BASE, "cachedir": str(core.DATA / "cache" / "yt-dlp"),
                "ignoreerrors": False,  # the command line's default turns every error into a None info
                "progress_hooks": [progress], "postprocessor_hooks": [processing]}
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={job['id']}", download=True)
        done = (info.get("requested_downloads") or [{}])[0].get("filepath") or info.get("filepath")
        if not done or not Path(done).is_file():
            raise RuntimeError("yt-dlp finished but the file isn't there")
        path = Path(done)
        job.update(status="done", progress=100, speed=None, eta=None, step=None, size=path.stat().st_size,
                   title=info.get("title") or job["title"], channel=info.get("channel") or job["channel"])
        if job["kind"] == "video":
            rel = str(path.relative_to(core.VIDEOS))
            item = clean(Card(id=job["id"], title=info.get("title") or "", channel=info.get("channel") or info.get("uploader") or "",
                              channel_id=info.get("channel_id") or "", duration=info.get("duration"), views=info.get("view_count")))
            db().execute("INSERT INTO videos(id, item, path, size, quality, user_id, added) VALUES (?, ?, ?, ?, ?, ?, ?) "
                         "ON CONFLICT(id) DO UPDATE SET item = excluded.item, path = excluded.path, size = excluded.size, "
                         "quality = excluded.quality, added = excluded.added",
                         (job["id"], item, rel, job["size"], job["quality"], job["user_id"], time.time()))
            job["path"] = rel
        else:
            job["path"] = str(path.relative_to(MUSIC_DIR())) if path.is_relative_to(MUSIC_DIR()) else path.name
    except yt_dlp.utils.DownloadCancelled:
        job.update(status="stopped", speed=None, eta=None)
    except HTTPException as exc:
        job.update(status="failed", error=exc.detail, speed=None, eta=None)
    except yt_dlp.utils.DownloadError as exc:
        if LIMITED.search(str(exc)):
            wait_then_retry(job)
        elif job.get("heals", 0) < MAX_HEALS and heal_soon(str(exc)):
            job["heals"] = job.get("heals", 0) + 1
            wait_then_retry(job, HEAL_WAIT, "YouTube changed something; waiting for a yt-dlp update")
        else:
            job.update(status="failed", error=plain_error(exc), speed=None, eta=None)
    except Exception as exc:  # keep the worker alive; show why
        log.exception("download %s failed", job["job"])
        job.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300], speed=None, eta=None)
    finally:
        job.pop("_done", None)
        save_jobs()


def serve(job):
    held = time.time()
    # a new yt-dlp is about to load: new downloads wait for it (and the restart requeues them),
    # but not forever if the restart never comes (music's worker restarts Parlor between its own jobs)
    while restarting() and time.time() - held < HOLD:
        time.sleep(5)
    if job.get("status") == "queued":
        run_download(job)


def download_worker():
    while True:
        job = _pending.get()
        try:
            serve(job)
        except Exception:
            log.exception("download worker hiccup")


def busy() -> bool:
    with _jobs_lock:
        return any(j["status"] in ("downloading", "processing") for j in jobs)


def start():
    restore_jobs()
    for n in range(max(1, PARALLEL)):
        threading.Thread(target=download_worker, daemon=True, name=f"yt-download-{n}").start()
    threading.Thread(target=sweeper, daemon=True, name="yt-retention").start()


# --- your video library ------------------------------------------------------------------

@router.get("/yt/library")
def library(user=Depends(me)):
    found = rows("SELECT v.id, v.item, v.path, v.size, v.quality, v.added, v.user_id, v.keep, u.name AS by_name "
                 "FROM videos v LEFT JOIN users u ON u.id = v.user_id ORDER BY v.added DESC")
    rule, items = retention(), []
    for r in found:
        if inside_videos(r["path"]):
            items.append({"type": "video", "id": r["id"], **json.loads(r["item"]), "thumb": f"/api/yt/file/{r['id']}/thumb",
                          "size": r["size"], "quality": r["quality"], "added": r["added"], "by": r["by_name"],
                          "mine": r["user_id"] == user["id"], "keep": bool(r["keep"]),
                          "expires": r["added"] + rule["days"] * 86400 if rule["days"] and not r["keep"] and not rule["watched_only"] else None})
    try:
        disk = shutil.disk_usage(core.VIDEOS)
        space = {"total": disk.total, "free": disk.free}
    except OSError:
        space = None
    return {"items": mark(items, user["id"]), "used": sum(i["size"] or 0 for i in items), "disk": space}


def _saved(vid):
    check(VIDEO_ID, vid, "video id")
    row = one("SELECT path, user_id FROM videos WHERE id = ?", vid)
    if not row:
        raise HTTPException(404, "That video isn't downloaded")
    path = inside_videos(row["path"])
    if not path:
        raise HTTPException(404, "The file is gone")
    return row, path


def inside_videos(rel):
    """The file a library row points at, if it exists inside the video folder."""
    root = core.VIDEOS.resolve()
    path = (root / rel).resolve()
    return path if root in path.parents and path.is_file() else None


@router.get("/yt/file/{vid}")
def video_file(vid: str, request: Request):
    _, path = _saved(vid)
    kind = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska"}.get(path.suffix.lower(), "application/octet-stream")
    return send_file(path, request, kind)


@router.get("/yt/file/{vid}/thumb")
def video_thumb(vid: str):
    _, path = _saved(vid)
    for ext in (".jpg", ".webp", ".png"):
        pic = path.with_suffix(ext)
        if pic.is_file():
            return FileResponse(pic, headers={"Cache-Control": "private, max-age=86400"})
    return Response(status_code=307, headers={"Location": thumb(vid)})


@router.post("/yt/library/{vid}/private")
def make_private(vid: str, request: Request, user=Depends(me)):
    """Take a downloaded video out of the shared library into your private items, encrypted with your key."""
    import private  # late: it uses this module's helpers
    row, path = _saved(vid)
    if not user["admin"] and row["user_id"] != user["id"]:
        raise HTTPException(403, "Only the person who downloaded it, or an admin, can make it private")
    private._key(request, user)  # their key is open (or they're told how to open it) before anything moves
    full = one("SELECT item, path, quality FROM videos WHERE id = ?", vid)
    item = json.loads(full["item"])
    kind = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska"}.get(path.suffix.lower(), "application/octet-stream")
    beside = [f for f in path.parent.glob(glob_escape(path.stem) + ".*") if f != path]  # its thumbnail and subtitles
    iid = private.make_private(user, path, {"vid": vid, "title": item.get("title") or path.stem, "channel": item.get("channel") or "",
                                            "duration": item.get("duration"), "type": kind, "item": full["item"], "path": full["path"],
                                            "quality": full["quality"]})
    for f in beside:
        f.unlink(missing_ok=True)
    db().execute("DELETE FROM videos WHERE id = ?", (vid,))
    with _jobs_lock:  # everyone sees the download list: its finished entry would still show the title
        jobs[:] = [j for j in jobs if not (j["id"] == vid and j["kind"] == "video" and j["status"] not in RUNNING)]
    save_jobs()
    return {"ok": True, "id": iid}


@router.delete("/yt/library/{vid}")
def delete_video(vid: str, user=Depends(me)):
    row, _ = _saved(vid)
    if not user["admin"] and row["user_id"] != user["id"]:
        raise HTTPException(403, "Only the person who downloaded it, or an admin, can delete it")
    remove_video(vid)
    return {"ok": True}


def remove_video(vid):
    """Delete a downloaded video's files (if they're still there) and its row."""
    row = one("SELECT path FROM videos WHERE id = ?", vid)
    path = row and inside_videos(row["path"])
    if path:
        for f in path.parent.glob(glob_escape(path.stem) + ".*"):  # the video and its thumbnail/subtitles
            f.unlink(missing_ok=True)
        try:
            path.parent.rmdir()  # the channel folder, if that was its last video
        except OSError:
            pass
    db().execute("DELETE FROM videos WHERE id = ?", (vid,))


# --- retention: downloads deleted after a while, if an admin turns it on ------------------

def retention():
    try:
        days = max(0.0, float(core.setting("video_keep_days") or 0))
    except ValueError:
        days = 0.0
    return {"days": int(days) if days == int(days) else days, "watched_only": core.setting("video_keep_watched_only") == "1"}


def expired(now=None):
    """Downloads past the retention period: N days after download, or with watched-only,
    N days after someone finished watching. Kept videos never expire."""
    rule = retention()
    if not rule["days"]:
        return []
    cutoff = (now or time.time()) - rule["days"] * 86400
    if not rule["watched_only"]:
        return [r["id"] for r in rows("SELECT id FROM videos WHERE keep = 0 AND added < ?", cutoff)]
    out = []
    for r in rows("SELECT id, item FROM videos WHERE keep = 0"):
        length = json.loads(r["item"]).get("duration") or 0
        # finished: within 30 s or 10% of the end, whichever is later
        if length and one("SELECT 1 AS x FROM history WHERE video_id = ? AND position >= ? AND watched < ?",
                          r["id"], max(length * 0.9, length - 30), cutoff):
            out.append(r["id"])
    return out


def sweep():
    for vid in expired():
        try:
            remove_video(vid)
            log.info("deleted downloaded video %s (retention)", vid)
        except OSError:
            log.warning("couldn't delete downloaded video %s", vid, exc_info=True)


def sweeper():
    while True:
        try:
            sweep()
        except Exception:
            log.exception("retention sweep failed")
        time.sleep(3600)


class Retention(BaseModel):
    days: float
    watched_only: bool = False


@router.get("/yt/retention")
def get_retention(_=Depends(me)):
    return retention()


@router.put("/yt/retention")
def set_retention(body: Retention, _=Depends(admin)):
    if not 0 <= body.days <= 3650:
        raise HTTPException(422, "Days is 0 (keep everything) up to 3650")
    core.set_setting("video_keep_days", str(body.days))
    core.set_setting("video_keep_watched_only", "1" if body.watched_only else "0")
    return retention()


class Keep(BaseModel):
    keep: bool


@router.post("/yt/library/{vid}/keep")
def keep_video(vid: str, body: Keep, user=Depends(me)):
    row, _ = _saved(vid)
    if not user["admin"] and row["user_id"] != user["id"]:
        raise HTTPException(403, "Only the person who downloaded it, or an admin, can change that")
    db().execute("UPDATE videos SET keep = ? WHERE id = ?", (int(body.keep), vid))
    return {"keep": body.keep}


def glob_escape(name: str) -> str:
    return re.sub(r"([*?\[\]])", r"[\1]", name)


@router.get("/yt/status")
def status(_=Depends(admin)):
    return {"yt_dlp": yt_dlp.version.__version__, "parallel": PARALLEL, "videos": str(core.VIDEOS), "retention": retention()}
