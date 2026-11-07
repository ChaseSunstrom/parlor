"""Play your Jellyfin library in Parlor: browse, playback info, progress and a stream proxy.

Parlor holds one Jellyfin API key (an admin sets it). Everyone plays as a Jellyfin user: the one they signed in
with, else the one an admin linked to them, else the default. The key never reaches a browser: streams go
through /api/jf/stream, which only serves a short whitelist of Jellyfin paths and adds the key itself."""

import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from core import me, one, setting
from music import AUDIO
from yt import Cache, send_file

router = APIRouter(prefix="/api/jf")
T = 10_000_000  # Jellyfin ticks per second
_users = Cache(60, 2)  # Jellyfin's user list, so a page of requests asks once

# --- talking to Jellyfin -------------------------------------------------------------

def _conf():
    url, key = setting("jellyfin_url").rstrip("/"), setting("jellyfin_key")
    if not (url and key):
        raise HTTPException(503, "Jellyfin isn't set up: an admin adds its address and an API key in Settings")
    return url, key


def fetch(method, path, headers=None, **kw):
    """One request to Jellyfin with the key in the header; never follows a redirect (the key would go along)."""
    url, key = _conf()
    auth = f'MediaBrowser Client="Parlor", Device="Parlor", DeviceId="parlor", Version="1", Token="{key}"'
    try:
        return requests.request(method, url + path, headers={**(headers or {}), "Authorization": auth},
                                timeout=(5, 60), allow_redirects=False, **kw)
    except requests.RequestException as exc:
        raise HTTPException(502, "Couldn't reach Jellyfin") from exc


def need(r):
    """The response if it's a 2xx, else the error to show."""
    if 200 <= r.status_code < 300:
        return r
    r.close()
    if r.status_code == 404:
        raise HTTPException(404, "Jellyfin doesn't have that")
    if r.status_code in (401, 403):
        raise HTTPException(502, "Jellyfin refused Parlor's API key")
    raise HTTPException(502, f"Jellyfin answered {r.status_code}")


def call(method, path, **kw):
    return need(fetch(method, path, **kw))


def either(method, new, old, **kw):
    """Jellyfin 10.9 moved the per-user routes; try the new one, then the old."""
    r = fetch(method, new, **kw)
    return need(fetch(method, old, **kw) if r.status_code == 404 else r)


def who(user) -> dict:
    """The Jellyfin user this account plays as: its Jellyfin sign-in, else the admin's link, else the default."""
    link, via = user["jellyfin_id"], "signin"
    if not link:
        link, via = (one("SELECT jf_user FROM users WHERE id = ?", user["id"]) or {}).get("jf_user"), "link"
    if not link:
        link, via = setting("jellyfin_user"), "default"
    if not link:
        raise HTTPException(409, "No Jellyfin user is linked to your account; ask an admin to link one or set a default")
    users = _users.get("all") or _users.put("all", call("GET", "/Users").json())
    want = link.replace("-", "").lower()
    for u in users:
        if u["Id"].replace("-", "").lower() == want or u["Name"].lower() == link.lower():
            return {"id": u["Id"], "name": u["Name"], "via": via}
    raise HTTPException(404, f"Jellyfin has no user called {link}")


ID = re.compile(r"[0-9a-fA-F]{32}")


def hexid(value, what="id"):
    if not ID.fullmatch(value or ""):
        raise HTTPException(422, f"That isn't a Jellyfin {what}")
    return value


# --- cards: the few fields the page needs, nothing of Jellyfin's own paths ------------

def art(item_id, tag, kind="Primary"):
    width = 1920 if kind.startswith("Backdrop") else 600  # cards don't need the full-size original
    return f"/api/jf/stream/Items/{item_id}/Images/{kind}?tag={quote(str(tag), safe='')}&maxWidth={width}" if item_id and tag else None


def secs(ticks):
    return round((ticks or 0) / T)


def card(i):
    data, tags = i.get("UserData") or {}, i.get("ImageTags") or {}
    image = (art(i["Id"], tags.get("Primary")) or art(i.get("SeriesId"), i.get("SeriesPrimaryImageTag"))
             or art(i.get("AlbumId"), i.get("AlbumPrimaryImageTag")))
    back = (i.get("BackdropImageTags") or [None])[0]
    return {"id": i["Id"], "type": i.get("Type"), "name": i.get("Name"), "year": i.get("ProductionYear"),
            "overview": i.get("Overview"), "runtime": secs(i.get("RunTimeTicks")), "played": bool(data.get("Played")),
            "position": secs(data.get("PlaybackPositionTicks")), "favourite": bool(data.get("IsFavorite")),
            "series": i.get("SeriesName"), "series_id": i.get("SeriesId"), "season": i.get("ParentIndexNumber"),
            "episode": i.get("IndexNumber"), "artists": i.get("Artists") or ([i["AlbumArtist"]] if i.get("AlbumArtist") else []),
            "album": i.get("Album"), "album_id": i.get("AlbumId"), "image": image,
            "backdrop": art(i["Id"], back, "Backdrop/0"), "collection": i.get("CollectionType")}


def cards(r):
    return [card(i) for i in (r.json().get("Items") or [])]


# --- browsing --------------------------------------------------------------------------

@router.get("/status")
def status(user=Depends(me)):
    if not (setting("jellyfin_url") and setting("jellyfin_key")):
        return {"configured": False, "connected": False}
    try:
        info = call("GET", "/System/Info").json()
    except HTTPException as exc:
        return {"configured": True, "connected": False, "error": exc.detail}
    out = {"configured": True, "connected": True, "version": info.get("Version"), "server": info.get("ServerName"), "user": None}
    try:
        w = who(user)
        out.update(user={"id": w["id"], "name": w["name"]}, via=w["via"])
    except HTTPException as exc:
        out["error"] = exc.detail
    return out


@router.get("/views")
def views(user=Depends(me)):
    uid = who(user)["id"]
    return cards(either("GET", "/UserViews", f"/Users/{uid}/Views", params={"userId": uid}))


TYPES = {"Movie", "Series", "Season", "Episode", "Audio", "MusicAlbum", "MusicArtist", "BoxSet", "Playlist", "Video", "Folder"}
SORTS = {"SortName", "DateCreated", "PremiereDate", "ProductionYear", "CommunityRating", "Random", "DatePlayed", "PlayCount", "Runtime"}
FILTERS = {"IsFavorite", "IsUnplayed", "IsPlayed", "IsResumable"}


def _list(text, allowed, what):
    parts = [p for p in (text or "").split(",") if p]
    if any(p not in allowed for p in parts):
        raise HTTPException(422, f"Unknown {what}")
    return ",".join(parts)


@router.get("/items")
def items(parent: str = "", types: str = "", sort: str = "", order: str = "", start: int = Query(0, ge=0, le=10**6),
          limit: int = Query(50, ge=1, le=200), search: str = Query("", max_length=100), filters: str = "", user=Depends(me)):
    types, filters = _list(types, TYPES, "type"), _list(filters, FILTERS, "filter")
    if sort and sort not in SORTS or order not in ("", "Ascending", "Descending"):
        raise HTTPException(422, "Unknown sort")
    params = {"userId": who(user)["id"], "startIndex": start, "limit": limit, "fields": "Overview"}
    for key, value in (("parentId", hexid(parent, "folder") if parent else ""), ("includeItemTypes", types), ("sortBy", sort),
                       ("sortOrder", order), ("searchTerm", search.strip()), ("filters", filters)):
        if value:
            params[key] = value
    if types or params.get("searchTerm"):
        params["recursive"] = "true"
    r = call("GET", "/Items", params=params).json()
    return {"items": [card(i) for i in r.get("Items") or []], "total": r.get("TotalRecordCount", 0), "start": start}


@router.get("/latest")
def latest(parent: str = "", limit: int = Query(20, ge=1, le=100), user=Depends(me)):
    uid = who(user)["id"]
    params = {"userId": uid, "limit": limit, "fields": "Overview"}
    if parent:
        params["parentId"] = hexid(parent, "folder")
    return [card(i) for i in either("GET", "/Items/Latest", f"/Users/{uid}/Items/Latest", params=params).json()]


@router.get("/resume")
def resume(limit: int = Query(20, ge=1, le=100), user=Depends(me)):
    uid = who(user)["id"]
    params = {"userId": uid, "limit": limit, "mediaTypes": "Video", "fields": "Overview", "enableTotalRecordCount": "false"}
    return cards(either("GET", "/UserItems/Resume", f"/Users/{uid}/Items/Resume", params=params))


@router.get("/nextup")
def nextup(limit: int = Query(20, ge=1, le=100), user=Depends(me)):
    return cards(call("GET", "/Shows/NextUp", params={"userId": who(user)["id"], "limit": limit, "fields": "Overview"}))


def get_item(iid, uid):
    return either("GET", f"/Items/{iid}", f"/Users/{uid}/Items/{iid}", params={"userId": uid}).json()


@router.get("/item/{iid}")
def item(iid: str, user=Depends(me)):
    hexid(iid)
    uid = who(user)["id"]
    i = get_item(iid, uid)
    out = card(i)
    out.update(genres=i.get("Genres") or [], rating=i.get("CommunityRating"), certification=i.get("OfficialRating"),
               people=[{"name": p["Name"], "role": p.get("Role") or p.get("Type")} for p in (i.get("People") or [])[:20] if p.get("Name")])
    kind, params = i.get("Type"), {"userId": uid, "fields": "Overview"}
    if kind in ("Movie", "Series"):
        try:
            out["similar"] = cards(call("GET", f"/Items/{iid}/Similar", params={**params, "limit": 12}))
        except HTTPException:  # nice to have; the page works without it
            out["similar"] = []
    if kind == "Series":
        out["seasons"] = cards(call("GET", f"/Shows/{iid}/Seasons", params=params))
    elif kind == "Season":
        out["episodes"] = cards(call("GET", f"/Shows/{i['SeriesId']}/Episodes", params={**params, "seasonId": iid}))
    elif kind == "MusicAlbum":
        out["tracks"] = cards(call("GET", "/Items", params={**params, "parentId": iid, "includeItemTypes": "Audio",
                                                              "sortBy": "ParentIndexNumber,IndexNumber"}))
    return out


# --- playback ----------------------------------------------------------------------------

def profile(hevc):
    """What this browser can play directly; anything else Jellyfin turns into h264/aac HLS."""
    video = "h264,hevc" if hevc else "h264"
    return {"Name": "Parlor",
            "DirectPlayProfiles": [{"Type": "Video", "Container": "mp4,m4v", "VideoCodec": video, "AudioCodec": "aac,mp3,opus"},
                                   {"Type": "Video", "Container": "webm", "VideoCodec": "vp8,vp9,av1", "AudioCodec": "vorbis,opus"},
                                   *({"Type": "Audio", "Container": c} for c in ("mp3", "flac", "opus", "m4a", "aac", "ogg", "wav", "webma"))],
            "TranscodingProfiles": [{"Type": "Video", "Context": "Streaming", "Protocol": "hls", "Container": c, "VideoCodec": "h264",
                                     "AudioCodec": "aac", "MaxAudioChannels": "2"} for c in ("ts", "mp4")]
            + [{"Type": "Audio", "Context": "Streaming", "Protocol": "http", "Container": "mp3", "AudioCodec": "mp3"}],
            "SubtitleProfiles": [{"Format": "vtt", "Method": "External"}]}


class Play(BaseModel):
    media_source: Optional[str] = Field(None, max_length=64, pattern=r"^[\w-]+$")
    audio_index: Optional[int] = Field(None, ge=0, le=1000)
    max_bitrate: Optional[int] = Field(None, ge=100_000, le=500_000_000)
    start: Optional[float] = Field(None, ge=0, le=10**7)  # seconds; default is where you left off
    transcode: bool = False
    hevc: bool = False


@router.post("/playback/{iid}")
def playback(iid: str, body: Play = Play(), user=Depends(me)):
    hexid(iid)
    uid = who(user)["id"]
    i = get_item(iid, uid)
    audio = "Audio" in (i.get("Type"), i.get("MediaType"))
    resume_at = secs((i.get("UserData") or {}).get("PlaybackPositionTicks"))
    position = resume_at if body.start is None else round(body.start)
    ask = {"StartTimeTicks": position * T, "EnableDirectPlay": not body.transcode, "EnableDirectStream": not body.transcode,
           "EnableTranscoding": True, "MaxStreamingBitrate": body.max_bitrate or 20_000_000, "DeviceProfile": profile(body.hevc)}
    for key, value in (("MediaSourceId", body.media_source), ("AudioStreamIndex", body.audio_index)):
        if value is not None:
            ask[key] = value
    info = call("POST", f"/Items/{iid}/PlaybackInfo", params={"userId": uid}, json=ask).json()
    if info.get("ErrorCode"):
        raise HTTPException(409, f"Jellyfin can't play this: {info['ErrorCode']}")
    sources = info.get("MediaSources") or []
    src = next((s for s in sources if s.get("Id") == body.media_source), None) if body.media_source else (sources or [None])[0]
    if not src:
        raise HTTPException(409, "Jellyfin has nothing playable for this")
    if src.get("SupportsDirectPlay") and not body.transcode:
        query = urlencode({"static": "true", "MediaSourceId": src["Id"], **({"Tag": src["ETag"]} if src.get("ETag") else {})})
        url, method = f"/api/jf/stream/{'Audio' if audio else 'Videos'}/{iid}/stream?{query}", "direct"
    elif src.get("TranscodingUrl"):
        url, method = proxied(src["TranscodingUrl"]), "transcode"
    else:
        raise HTTPException(409, "Jellyfin offers no way to play this in a browser")
    if not url:
        raise HTTPException(502, "Jellyfin sent a stream address Parlor won't serve")
    streams = src.get("MediaStreams") or []
    return {"method": method, "url": url, "media_source": src["Id"], "play_session": info.get("PlaySessionId"),
            "runtime": secs(src.get("RunTimeTicks") or i.get("RunTimeTicks")), "position": position,
            "audio": [{"index": s["Index"], "language": s.get("Language"), "title": s.get("DisplayTitle"), "codec": s.get("Codec"),
                       "channels": s.get("Channels"), "default": bool(s.get("IsDefault"))} for s in streams if s.get("Type") == "Audio"],
            "subtitles": [{"index": s["Index"], "language": s.get("Language"), "title": s.get("DisplayTitle"), "codec": s.get("Codec"),
                           "default": bool(s.get("IsDefault")),
                           "url": proxied(s["DeliveryUrl"]) if s.get("DeliveryMethod") == "External" and s.get("DeliveryUrl") else None}
                          for s in streams if s.get("Type") == "Subtitle"],
            "chapters": [{"start": secs(c.get("StartPositionTicks")), "name": c.get("Name")} for c in i.get("Chapters") or []]}


class Progress(BaseModel):
    id: str
    position: Optional[float] = Field(None, ge=0, le=10**7)  # seconds
    played: Optional[bool] = None
    favourite: Optional[bool] = None


@router.post("/progress")
def progress(body: Progress, user=Depends(me)):
    hexid(body.id)
    saves = []
    if body.position is not None:
        saves.append({"PlaybackPositionTicks": round(body.position * T)})
    if body.played is not None:
        saves.append({"Played": body.played, "PlaybackPositionTicks": 0})
    if body.favourite is not None:
        saves.append({"IsFavorite": body.favourite})
    if not saves:
        raise HTTPException(422, "Nothing to save")
    uid = who(user)["id"]
    for save in saves:
        either("POST", f"/UserItems/{body.id}/UserData", f"/Users/{uid}/Items/{body.id}/UserData", params={"userId": uid}, json=save)
    return {"ok": True}


# --- the stream proxy ----------------------------------------------------------------------

_I = r"[0-9a-fA-F]{32}"
_SEGS = r"(?:[\w-][\w.-]*/)*[\w-][\w.-]*"  # path pieces, never "." or ".." or empty
_PLAY = rf"(?:stream(?:\.\w+)?|master\.m3u8|main\.m3u8|hls1/{_SEGS})"
ALLOWED = re.compile(rf"Videos/{_I}/(?:{_PLAY}|{_I}/Subtitles/\d+/(?:\d+/)?Stream\.(?:vtt|srt|ass))"
                     rf"|Audio/{_I}/(?:{_PLAY}|universal)"
                     rf"|Items/{_I}/(?:Images/(?:Primary|Backdrop|Thumb|Logo|Banner)(?:/\d+)?|Download)", re.ASCII)
IMAGE = re.compile(rf"Items/{_I}/Images/[A-Za-z]+(?:/\d+)?", re.ASCII)  # within ALLOWED: only these skip the access check
KEYS = {"api_key", "apikey", "x-emby-token"}
DROP = KEYS | {"userid", "deviceid"}  # never forwarded from the browser: the server decides who is asking
_KEY = re.compile(r"([?&])(?:api_key|apikey|x-emby-token)=[^&\s\"']*(&?)", re.I)
PASS = ("content-range", "content-length", "accept-ranges")


def strip_key(text: str) -> str:
    """Text with any api_key=... query parameter taken out."""
    return _KEY.sub(lambda m: m[1] if m[2] else "", text)


def proxied(url: str):
    """Our address for a path Jellyfin gave us (no key), or None if the proxy wouldn't serve it."""
    path, _, query = url.lstrip("/").partition("?")
    head, _, rest = path.partition("/")
    path = f"{head.capitalize()}/{rest}"  # Jellyfin writes /videos/... in transcoding urls
    if not ALLOWED.fullmatch(path):
        return None
    query = strip_key("?" + query)[1:] if query else ""
    return f"/api/jf/stream/{path}" + (f"?{query}" if query else "")


_seen = Cache(60, 5000)  # (Jellyfin user, item) pairs checked lately, so HLS segments don't each ask


def may_see(user, iid, download=False):
    """The proxy fetches with the admin key, so check this person's Jellyfin user can see the item
    (and download, for Download) before serving it."""
    uid = who(user)["id"]
    if _seen.get((uid, iid.lower())) is None:
        get_item(iid, uid)  # a 404 here means their Jellyfin user can't see it
        _seen.put((uid, iid.lower()), True)
    if download:
        policy = next((u.get("Policy") or {} for u in _users.get("all") or [] if u["Id"] == uid), {})
        if policy.get("EnableContentDownloading") is False:
            raise HTTPException(403, "Your Jellyfin account isn't allowed to download")


@router.get("/stream/{path:path}")
def stream(path: str, request: Request, user=Depends(me)):
    if len(path) > 300 or not ALLOWED.fullmatch(path):
        raise HTTPException(404, "No such stream")
    image = bool(IMAGE.fullmatch(path))
    if not image:  # pictures are let through unchecked: a page of cards would mean a lookup each
        may_see(user, path.split("/")[1], download=path.endswith("/Download"))
    params = [(k, v) for k, v in request.query_params.multi_items() if k.lower() not in DROP]
    headers = {"Accept-Encoding": "identity"}  # a compressed body would make Content-Length lie
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    r = fetch("GET", "/" + path, params=params, headers=headers, stream=True)
    if r.status_code == 416:
        r.close()
        return Response(status_code=416, headers={"Content-Range": r.headers.get("Content-Range", "bytes */*")})
    if r.status_code not in (200, 206):
        need(r)
    ctype = r.headers.get("Content-Type", "application/octet-stream")
    if path.endswith(".m3u8"):  # playlists carry the key in their links
        body = b""
        for chunk in r.iter_content(64 * 1024):
            body += chunk
            if len(body) > 2_000_000:
                r.close()
                raise HTTPException(502, "Jellyfin sent a playlist that's too big")
        r.close()
        return Response(strip_key(body.decode("utf-8", "replace")), status_code=200, media_type=ctype, headers={"Cache-Control": "no-store"})
    headers = {"Cache-Control": "private, max-age=3600" if image else "no-store"}
    headers.update({n: r.headers[n] for n in PASS if n in r.headers})
    return StreamingResponse(r.iter_content(64 * 1024), status_code=r.status_code, media_type=ctype, headers=headers,
                             background=BackgroundTask(r.close))


# --- songs on this server's own disk ---------------------------------------------------------

@router.get("/music/{path:path}")
def local_song(path: str, request: Request, user=Depends(me)):
    """A song from the music folder (the working directory, like the music app), with byte ranges."""
    root = Path.cwd().resolve()
    try:
        file = (root / path).resolve()
    except (OSError, ValueError):
        raise HTTPException(404, "No such song")
    if root not in file.parents or file.suffix.lower() not in AUDIO or not file.is_file():
        raise HTTPException(404, "No such song")
    return send_file(file, request, mimetypes.guess_type(file.name)[0] or "application/octet-stream")
