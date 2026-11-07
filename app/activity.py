"""What Sonarr, Radarr and Lidarr are doing, in one shape for the page: downloads in progress
(and why one is stuck), what's coming up, what happened lately, what's missing and anything
wrong. Plus music requests through Lidarr (search, then add an artist or one album)."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import arr
from core import me

log = logging.getLogger("yeerr.activity")
router = APIRouter(prefix="/api/arr")
MEDIA = ("sonarr", "radarr", "lidarr")
INCLUDE = {"sonarr": {"includeSeries": "true", "includeEpisode": "true"}, "radarr": {"includeMovie": "true"},
           "lidarr": {"includeArtist": "true", "includeAlbum": "true"}}
EVENTS = {"grabbed": "Grabbed", "downloadFolderImported": "Imported", "trackFileImported": "Imported", "albumImportIncomplete": "Partly imported",
          "downloadFailed": "Download failed", "importFailed": "Import failed", "downloadImported": "Imported",
          "episodeFileDeleted": "Deleted", "movieFileDeleted": "Deleted", "trackFileDeleted": "Deleted", "downloadIgnored": "Ignored"}


def present():
    """The media apps yeerr can talk to (it has their keys)."""
    return [a for a in MEDIA if arr.key(a)]


def each(apps, fn):
    """fn(app) for every app at once; (results, plain errors for the apps that failed)."""
    out, errors = {}, []
    with ThreadPoolExecutor(max(1, len(apps))) as pool:
        for app, res in zip(apps, pool.map(lambda a: _try(fn, a), apps)):
            if isinstance(res, arr.ArrError):
                errors.append(str(res))
            else:
                out[app] = res
    return out, errors


def _try(fn, app):
    try:
        return fn(app)
    except arr.ArrError as exc:
        return exc


def image(images, *kinds):
    """The first picture of one of these kinds ("poster", "cover", ...), as a web address."""
    for kind in kinds:
        for i in images or []:
            if i.get("coverType") == kind and (i.get("remoteUrl") or i.get("url", "").startswith("http")):
                return i.get("remoteUrl") or i["url"]
    return None


def seconds(span):
    """'1.02:03:04' or '02:03:04' as seconds."""
    m = re.fullmatch(r"(?:(\d+)\.)?(\d+):(\d+):(\d+)(?:\.\d+)?", span or "")
    return int(m[1] or 0) * 86400 + int(m[2]) * 3600 + int(m[3]) * 60 + int(m[4]) if m else None


def subject(app, r):
    """(title, subtitle, picture) of what a queue, history or calendar record is about."""
    if app == "sonarr":
        series, ep = r.get("series") or {}, r.get("episode") or r
        se = f"S{ep.get('seasonNumber', 0):02}E{ep.get('episodeNumber', 0):02}" if ep.get("episodeNumber") is not None else ""
        return series.get("title") or r.get("title") or "", " · ".join(x for x in (se, ep.get("title")) if x), image(series.get("images"), "poster")
    if app == "radarr":
        movie = r.get("movie") or r
        return movie.get("title") or "", str(movie.get("year") or ""), image(movie.get("images"), "poster")
    artist, album = r.get("artist") or {}, r.get("album") or r
    return artist.get("artistName") or "", album.get("title") or "", image(album.get("images"), "cover") or image(artist.get("images"), "poster")


def why(r):
    msgs = [m for s in r.get("statusMessages") or [] for m in (s.get("messages") or [s.get("title")]) if m]
    return r.get("errorMessage") or (msgs[0] if msgs else None)


def state(r):
    """One word for where a download is: queued, downloading, paused, importing, stuck or failed."""
    status, tracked, health = (r.get("status") or "").lower(), r.get("trackedDownloadState") or "", r.get("trackedDownloadStatus") or ""
    if status == "failed" or tracked in ("failed", "failedPending"):
        return "failed"
    if health in ("warning", "error") or tracked == "importBlocked":
        return "stuck"
    if tracked in ("importPending", "importing") or status == "completed":
        return "importing"
    return {"downloading": "downloading", "paused": "paused", "queued": "queued", "delay": "queued"}.get(status, "downloading")


@router.get("/queue")
def queue(_=Depends(me)):
    def one(app):
        return arr.call(app, "GET", "/queue", params={"pageSize": 200, **INCLUDE[app]}).get("records", [])
    found, errors = each(present(), one)
    items = []
    for app, records in found.items():
        for r in records:
            title, sub, pic = subject(app, r)
            size, left = r.get("size") or 0, r.get("sizeleft") or 0
            items.append({"app": app, "id": r.get("id"), "title": title, "subtitle": sub, "image": pic, "release": r.get("title"),
                          "state": state(r), "why": why(r), "progress": round(1 - left / size, 4) if size else 0, "size": size, "left": left,
                          "eta": seconds(r.get("timeleft")), "client": r.get("downloadClient"),
                          "quality": ((r.get("quality") or {}).get("quality") or {}).get("name")})
    order = {"stuck": 0, "failed": 1, "downloading": 2, "importing": 3, "queued": 4, "paused": 5}
    return {"items": sorted(items, key=lambda i: (order[i["state"]], -(i["progress"] or 0))), "errors": errors}


@router.get("/calendar")
def calendar(days: int = Query(14, ge=1, le=90), _=Depends(me)):
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    span = {"start": (today - timedelta(days=1)).isoformat(), "end": (today + timedelta(days=days)).isoformat()}
    found, errors = each(present(), lambda app: arr.call(app, "GET", "/calendar", params={**span, **INCLUDE[app]}))
    items = []
    for app, records in found.items():
        for r in records:
            title, sub, pic = subject(app, r)
            if app == "sonarr":
                when, what = r.get("airDateUtc"), "Episode"
            elif app == "radarr":  # the first of its releases inside the window
                dates = sorted((d, label) for label, d in (("In cinemas", r.get("inCinemas")), ("Digital", r.get("digitalRelease")),
                                                          ("On disc", r.get("physicalRelease"))) if d and span["start"] <= d <= span["end"])
                when, what = dates[0] if dates else (None, None)
            else:
                when, what = r.get("releaseDate"), "Album"
            if when:
                items.append({"app": app, "id": r.get("id"), "date": when, "what": what, "title": title, "subtitle": sub, "image": pic,
                              "have": bool(r.get("hasFile") or (r.get("statistics") or {}).get("percentOfTracks") == 100), "monitored": r.get("monitored", True)})
    return {"items": sorted(items, key=lambda i: i["date"]), "errors": errors}


@router.get("/history")
def history(limit: int = Query(30, ge=1, le=200), _=Depends(me)):
    def one(app):
        return arr.call(app, "GET", "/history", params={"pageSize": limit, "sortKey": "date", "sortDirection": "descending", **INCLUDE[app]}).get("records", [])
    found, errors = each(present(), one)
    items = []
    for app, records in found.items():
        for r in records:
            title, sub, pic = subject(app, r)
            items.append({"app": app, "date": r.get("date"), "event": EVENTS.get(r.get("eventType"), r.get("eventType")), "title": title,
                          "subtitle": sub, "image": pic, "release": r.get("sourceTitle"), "quality": ((r.get("quality") or {}).get("quality") or {}).get("name")})
    return {"items": sorted(items, key=lambda i: i["date"] or "", reverse=True)[:limit], "errors": errors}


@router.get("/summary")
def summary(_=Depends(me)):
    """Per app: answering, version, how much is missing, and what's wrong."""
    def one(app):
        info = {"app": app, "label": arr.APPS[app]["label"]}
        info["version"] = arr.call(app, "GET", "/system/status").get("version")
        info["health"] = [{"type": h.get("type"), "message": h.get("message")} for h in arr.call(app, "GET", "/health")]
        if app in MEDIA:
            info["missing"] = arr.call(app, "GET", "/wanted/missing", params={"pageSize": 1}).get("totalRecords", 0)
        return info
    apps = [a for a in arr.APPS if arr.key(a)]
    found, errors = each(apps, one)
    return {"apps": [found[a] for a in apps if a in found], "errors": errors}


# --- music requests through Lidarr ---------------------------------------------------------

def lidarr_card(x):
    if "artist" in x and x.get("artist") and "album" not in x:
        a = x["artist"]
        return {"type": "artist", "id": a.get("foreignArtistId"), "name": a.get("artistName"), "image": image(a.get("images"), "poster"),
                "about": (a.get("overview") or "")[:300], "have": bool(a.get("id"))}
    al = x.get("album")
    if al:
        return {"type": "album", "id": al.get("foreignAlbumId"), "title": al.get("title"), "artist": (al.get("artist") or {}).get("artistName"),
                "year": (al.get("releaseDate") or "")[:4] or None, "image": image(al.get("images"), "cover"), "have": bool(al.get("id"))}
    return None


@router.get("/music/search")
def music_search(q: str, _=Depends(me)):
    q = q.strip()[:200]
    if not q:
        return {"items": []}
    try:
        found = arr.call("lidarr", "GET", "/search", params={"term": q}, timeout=60)
    except arr.ArrError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"items": [c for c in map(lidarr_card, found or []) if c and c["id"]]}


class MusicRequest(BaseModel):
    type: str  # "artist" or "album"
    id: str    # MusicBrainz id (Lidarr's foreign id)


MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def defaults():
    """Where Lidarr puts music, and the profiles new artists get."""
    root = (arr.call("lidarr", "GET", "/rootfolder") or [{}])[0]
    quality = root.get("defaultQualityProfileId") or arr.call("lidarr", "GET", "/qualityprofile")[0]["id"]
    meta = root.get("defaultMetadataProfileId") or next(p["id"] for p in arr.call("lidarr", "GET", "/metadataprofile") if p["name"] != "None")
    return {"rootFolderPath": root.get("path") or arr.APPS["lidarr"]["root"], "qualityProfileId": quality, "metadataProfileId": meta}


def request_music(kind, mbid):
    """Add an artist (everything, searched now) or one album (only it, searched now) to Lidarr.
    Returns what Lidarr has for it: {"artist_id", "album_id", "title", "already"}."""
    if kind not in ("artist", "album") or not MBID.fullmatch(mbid or ""):
        raise HTTPException(422, "Pick an artist or album from the search")
    if kind == "artist":
        have = arr.call("lidarr", "GET", "/artist", params={"mbId": mbid})
        if have:
            return {"artist_id": have[0]["id"], "album_id": None, "title": have[0]["artistName"], "already": True}
        artist = arr.call("lidarr", "GET", "/artist/lookup", params={"term": f"lidarr:{mbid}"}, timeout=60)[0]
        artist.update(**defaults(), monitored=True, monitorNewItems="all", addOptions={"monitor": "all", "searchForMissingAlbums": True})
        added = arr.call("lidarr", "POST", "/artist", json=artist, timeout=60)
        return {"artist_id": added["id"], "album_id": None, "title": added["artistName"], "already": False}
    have = arr.call("lidarr", "GET", "/album", params={"foreignAlbumId": mbid})
    if have:
        album = have[0]
        if not album.get("monitored"):
            arr.call("lidarr", "PUT", "/album/monitor", json={"albumIds": [album["id"]], "monitored": True})
        arr.call("lidarr", "POST", "/command", json={"name": "AlbumSearch", "albumIds": [album["id"]]})
        return {"artist_id": album.get("artistId"), "album_id": album["id"], "title": album["title"], "already": True}
    album = arr.call("lidarr", "GET", "/album/lookup", params={"term": f"lidarr:{mbid}"}, timeout=60)[0]
    artist = album.get("artist") or {}
    known = arr.call("lidarr", "GET", "/artist", params={"mbId": artist.get("foreignArtistId")}) if artist.get("foreignArtistId") else []
    if known:
        album["artistId"], album["artist"] = known[0]["id"], known[0]
    else:  # a new artist with nothing else of theirs wanted
        artist.update(**defaults(), monitored=True, monitorNewItems="none", addOptions={"monitor": "none", "searchForMissingAlbums": False})
        album["artist"] = artist
    album.update(monitored=True, addOptions={"searchForNewAlbum": True})
    added = arr.call("lidarr", "POST", "/album", json=album, timeout=60)
    return {"artist_id": added.get("artistId"), "album_id": added["id"], "title": added["title"], "already": False}


@router.post("/music/request")
def music_request(body: MusicRequest, _=Depends(me)):
    try:
        return request_music(body.type, body.id)
    except arr.ArrError as exc:
        raise HTTPException(502, str(exc)) from exc
