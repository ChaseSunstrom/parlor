"""Music requests that don't get lost. Each album someone asks for (from Music → Request music, an artist's albums,
or Lidarr's own wanted list) gets a row here, and a worker follows it through Lidarr: searching, downloading,
importing, imported. When Lidarr finds nothing in 6 hours, two of its downloads fail, or a download stops moving
for 12 hours, the album is handed over: Lidarr stops looking (unmonitored, its queue items removed and
blocklisted), music-fndr fetches it from YouTube into a staging folder, the files get the album's MusicBrainz ids,
and Lidarr imports them from there, told which track each file is, so the library and Lidarr agree. A Spotify album that
can't be matched for sure waits for someone to pick it. Errors retry with backoff; everything lives in the
database, so a restart loses nothing; each row says what it's doing and why."""

import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import arr
from core import db, me, one, rows, setting

log = logging.getLogger("parlor.musicreq")
router = APIRouter(prefix="/api/arr/music/requests")
NO_GRAB, STALLED, FAILED_GRABS = 6 * 3600, 12 * 3600, 2  # when Lidarr has had its chance
GRACE = 2 * 3600  # after a grab, before it shows in the queue or the album
EVERY, IMPORT_WAIT, MAX_TRIES, MAX_ERRORS, HAND_OVERS = 600, 6 * 3600, 4, 12, 3
STAGING = Path(os.environ.get("MUSIC_STAGING", "/data/downloads/parlor-music"))  # Lidarr sees it at the same path
OPEN = ("searching", "downloading", "importing", "fetching", "expanding", "needs_look")
LABEL = {"searching": "Lidarr is searching", "downloading": "Downloading in Lidarr", "importing": "Importing", "fetching": "Getting it from YouTube",
         "expanding": "Waiting for Lidarr to list the albums", "needs_look": "Pick the right album", "imported": "In your library", "failed": "Stuck"}
_lock = threading.Lock()  # ponytail: one tick at a time; rows are few and the work is calls to Lidarr


# --- adding requests --------------------------------------------------------------------------

def add(user_id: Optional[int], got: dict, now: Optional[float] = None):
    """After activity.request_music put it in Lidarr (and started a search): follow it from here."""
    now = now or time.time()
    if got.get("album_id"):
        track_album(user_id, got["album_id"], now, title=got.get("title"))
    elif got.get("artist_id"):  # an artist: their albums are listed once Lidarr has refreshed them
        db().execute("INSERT INTO music_requests(user_id, artist, title, state, created, updated, searched) VALUES (?, ?, ?, 'expanding', ?, ?, ?)",
                     (user_id, got["artist_id"], got.get("title") or "", now, now, now))


def track_album(user_id, album_id, now, title="", by="", searched=None):
    """A row for this Lidarr album (once: asking again searches again and keeps the row)."""
    row = one("SELECT id, state FROM music_requests WHERE album = ?", album_id)
    if row:
        if row["state"] in ("failed", "needs_look", "searching", "imported"):  # asked again, or missing again: from the top
            db().execute("UPDATE music_requests SET state = 'searching', via = 'lidarr', why = '', searched = ?, tries = 0, yt_tries = 0, next_at = 0, "
                         "job = NULL, spotify = NULL, command = NULL, candidates = NULL, left_bytes = NULL, moved = NULL, updated = ?, "
                         "user_id = COALESCE(user_id, ?) WHERE id = ?", (now, now, user_id, row["id"]))
        return row["id"]
    cur = db().execute("INSERT INTO music_requests(user_id, album, title, artist_name, state, created, updated, searched) VALUES (?, ?, ?, ?, 'searching', ?, ?, ?)",
                       (user_id, album_id, title or "", by or "", now, now, searched or now))
    return cur.lastrowid


def from_wanted(now):
    """Albums Lidarr itself wants (added there, or by an artist's monitoring) join the list too."""
    got = arr.call("lidarr", "GET", "/wanted/missing", params={"page": 1, "pageSize": 250, "monitored": "true", "includeArtist": "true"})
    for a in got.get("records") or []:
        have = one("SELECT state FROM music_requests WHERE album = ?", a["id"])
        if not have or have["state"] == "imported":  # new to the list, or gone missing again
            track_album(None, a["id"], now, a.get("title"), (a.get("artist") or {}).get("artistName"), searched=now)


# --- following one request --------------------------------------------------------------------

def complete(album) -> bool:
    s = album.get("statistics") or {}
    return s.get("trackCount", 0) > 0 and s.get("trackFileCount", 0) >= s["trackCount"]


def set_(row_id, **values):
    values["updated"] = values.get("updated") or time.time()
    db().execute(f"UPDATE music_requests SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?", (*values.values(), row_id))


def history(album):
    return arr.call("lidarr", "GET", "/history/artist", params={"artistId": album["artistId"], "albumId": album["id"]}) or []


def since(records, t, *kinds):
    return [h for h in records if h.get("eventType") in kinds and iso(h.get("date")) >= t]


def iso(text) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat((text or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def advance(row, now):
    """One step for one request, from what Lidarr says now."""
    if row["state"] == "expanding":
        return expand(row, now)
    try:
        album = arr.call("lidarr", "GET", f"/album/{row['album']}")
    except arr.ArrError as exc:
        if "404" in str(exc):
            return set_(row["id"], state="failed", why="The album was removed from Lidarr")
        raise
    names = {"title": album.get("title") or row["title"], "artist_name": (album.get("artist") or {}).get("artistName") or row["artist_name"],
             "image": next((i.get("remoteUrl") or i.get("url") for i in album.get("images") or [] if i.get("coverType") == "cover"), row["image"])}
    if complete(album):
        if row["via"] == "youtube" and not album.get("monitored"):  # Lidarr may look for a better copy from now on
            arr.call("lidarr", "PUT", "/album/monitor", json={"albumIds": [album["id"]], "monitored": True})
        clean(row)
        return set_(row["id"], state="imported", why="", **names)
    if row["state"] == "needs_look":  # waiting for someone to pick; Lidarr keeps looking meanwhile
        return set_(row["id"], **names)
    if row["state"] == "fetching":
        return fetching(row, album, now)
    if row["state"] == "importing" and row["via"] == "youtube":
        return importing(row, album, now)
    if not album.get("monitored"):  # adding a new artist with "monitor none" can unmonitor the very album asked for
        arr.call("lidarr", "PUT", "/album/monitor", json={"albumIds": [album["id"]], "monitored": True})
    queue = arr.call("lidarr", "GET", "/queue/details", params={"albumIds": album["id"]}) or []
    past = history(album)
    failed = since(past, row["created"], "downloadFailed")
    if len(failed) >= FAILED_GRABS:
        return give_up(row, album, f"{len(failed)} of Lidarr's downloads failed", now, names)
    if queue:
        q = queue[0]
        left, tracked = q.get("sizeleft"), q.get("trackedDownloadState")
        state = "importing" if tracked in ("importPending", "importing", "importBlocked", "importFailed") else "downloading"
        moved = now if row["left_bytes"] is None or left != row["left_bytes"] or state != row["state"] else row["moved"] or now
        why = "; ".join(m for s in q.get("statusMessages") or [] for m in s.get("messages") or []) or q.get("errorMessage") or ""
        if now - moved >= STALLED:  # downloading or waiting to import, nothing has changed in 12 hours
            return give_up(row, album, "Lidarr's download hasn't moved in 12 hours" if state == "downloading"
                           else f"Lidarr couldn't import its download for 12 hours{': ' + why if why else ''}", now, names)
        return set_(row["id"], state=state, why=why, left_bytes=left, moved=moved, **names)
    got = since(past, row["searched"] or row["created"], "grabbed", "albumImportIncomplete", "trackFileImported", "downloadImported")
    last = max((iso(h.get("date")) for h in got), default=0.0)
    if last and now - last < GRACE:  # grabbed: on its way into the queue or the library
        incomplete = [h for h in got if h.get("eventType") == "albumImportIncomplete"]
        return set_(row["id"], state="importing", why=str((incomplete[-1].get("data") or {}).get("statusMessages") or "")[:300] if incomplete else "", **names)
    if now - max(row["searched"] or row["created"], last) >= NO_GRAB:
        return give_up(row, album, "Lidarr's download never arrived whole" if last else "Lidarr found nothing in 6 hours", now, names)
    return set_(row["id"], state="searching", why="Lidarr got part of it and is looking for the rest" if last else "", **names)


def expand(row, now):
    albums = arr.call("lidarr", "GET", "/album", params={"artistId": row["artist"]}) or []
    wanted = [a for a in albums if a.get("monitored") and not complete(a)]
    if not wanted and now - row["created"] < 3600:
        return set_(row["id"], why="Lidarr is still reading the artist's albums")
    for a in wanted:
        track_album(row["user_id"], a["id"], now, a.get("title"), (a.get("artist") or {}).get("artistName") or row["title"], searched=row["searched"])
    db().execute("DELETE FROM music_requests WHERE id = ?", (row["id"],))


# --- when Lidarr has had its chance -------------------------------------------------------------

_handed = {"n": 0}  # hand-overs this tick: a big wanted list goes over a few at a time


def give_up(row, album, why, now, names, by_hand=False):
    """Lidarr has had its chance. Find the album on Spotify first: one sure match, and only then is Lidarr stopped and
    YouTube fetches it; several or none, and someone picks (Lidarr keeps looking meanwhile)."""
    if not by_hand and row["user_id"] is None and setting("music_fallback_wanted") != "1":
        return set_(row["id"], state="searching", why=f"{why}; Lidarr keeps looking (an admin can get it from YouTube)", **names)
    if not by_hand:
        if _handed["n"] >= HAND_OVERS:
            return None  # next time round
        _handed["n"] += 1
    picks = candidates(names["artist_name"], names["title"], (album.get("releaseDate") or "")[:4], lengths(album))
    set_(row["id"], via="youtube", why=why, **names)
    if len(picks) == 1:
        stop_lidarr(album["id"])
        return fetch(row["id"], picks[0]["id"], why)
    set_(row["id"], state="needs_look", candidates=json.dumps(picks[:8]),
         why=f"{why}. {'More than one album on Spotify could be it' if picks else 'Spotify has nothing that matches for sure'}: pick it")


def stop_lidarr(album_id):
    """So Lidarr can't grab it too: unwatched, and its downloads of it removed and blocklisted."""
    arr.call("lidarr", "PUT", "/album/monitor", json={"albumIds": [album_id], "monitored": False})
    for q in arr.call("lidarr", "GET", "/queue/details", params={"albumIds": album_id}) or []:
        arr.call("lidarr", "DELETE", f"/queue/{q['id']}", params={"removeFromClient": "true", "blocklist": "true"})


def lengths(album) -> set:
    """How many tracks each edition of the album has: Lidarr often watches a big deluxe one, YouTube has the plain one."""
    return {r["trackCount"] for r in album.get("releases") or [] if r.get("trackCount")} or {(album.get("statistics") or {}).get("trackCount") or 0}


def norm(text) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"[\(\[][^)\]]*(deluxe|remaster|edition|version|expanded|anniversary|bonus)[^)\]]*[\)\]]", "", text)
    text = re.sub(r"\s-\s.*(deluxe|remaster|edition|version).*$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text.replace("&", "and"))


def candidates(artist, title, year, counts: set) -> list:
    """Spotify albums that are this one: same artist and title (editions aside), about the same year and length."""
    import music  # late: music-fndr starts its own machinery on import
    found = music.search(f"{artist} {title}").get("albums") or []
    out = []
    for a in found:
        if norm(title) != norm(a["name"]) or norm(artist) not in [norm(p.get("name") if isinstance(p, dict) else p) for p in a.get("artists") or []]:
            continue
        if year and a.get("year") and abs(int(a["year"]) - int(year)) > 1:
            continue
        try:
            n = music.album(a["id"]).get("total") or 0
        except Exception:  # noqa: BLE001  (unknown length: keep it, let the person decide)
            n = 0
        if n and any(counts) and min(abs(n - c) for c in counts if c) > 2:
            continue
        out.append({"id": a["id"], "name": a["name"], "artist": artist, "year": a.get("year"), "tracks": n, "image": a.get("image")})
    return out


def fetch(row_id, spotify_id, why=""):
    import music
    folder = STAGING / f"request-{row_id}"
    job = music.queue_download("album", spotify_id, output=f"{folder}/{{album-artist}}/{{album}}/{{track-number}} - {{title}}.{{output-ext}}")
    set_(row_id, state="fetching", spotify=spotify_id, job=job["job"], why=why or "Getting it from YouTube", candidates=None)


def fetching(row, album, now):
    import music
    job = next((j for j in music.jobs if j["job"] == row["job"]), None)
    if job and job["status"] in music.RUNNING:
        return None
    if not job or job["status"] != "done" or not any(s["status"] in ("done", "skipped", "duplicate") for s in job["songs"].values()):
        tries = row["yt_tries"] + 1
        what = "the download was stopped" if job and job["status"] == "stopped" else (job or {}).get("error") or "the download went missing"
        if tries >= MAX_TRIES:
            return set_(row["id"], state="failed", yt_tries=tries, why=f"YouTube didn't work out: {what}")
        fetch(row["id"], row["spotify"], f"Trying YouTube again ({tries} of {MAX_TRIES - 1}): {what}")
        return set_(row["id"], yt_tries=tries)
    folder = STAGING / f"request-{row['id']}"
    tag(folder, album)
    import stack
    stack._own(folder, *folder.rglob("*"))  # Parlor writes as root; Lidarr (PUID) has to move them
    arr.call("lidarr", "PUT", "/album/monitor", json={"albumIds": [album["id"]], "monitored": True})  # an unwatched album counts no tracks
    cmd, why = import_files(folder, album)
    set_(row["id"], state="importing", command=cmd, moved=now, why=why)


def import_files(folder: Path, album) -> tuple:
    """Lidarr imports the files as this album: each file is told which release and track it is (by its track number,
    else its title), so nothing is left to Lidarr's guessing, which a copy from YouTube can fail."""
    import mutagen
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    releases = sorted((r for r in album.get("releases") or [] if r.get("trackCount")),
                      key=lambda r: (abs(r["trackCount"] - len(files)), not r.get("monitored"), r["id"]))
    if not files or not releases:
        return None, "Nothing to import"
    release = releases[0]
    tracks = arr.call("lidarr", "GET", "/track", params={"albumReleaseId": release["id"]}) or []
    preview = {i["path"]: i for i in arr.call("lidarr", "GET", "/manualimport", params={"folder": str(folder), "filterExistingFiles": "false"}) or []}
    picked, used = [], set()
    for f in files:
        try:
            tags = mutagen.File(f, easy=True) or {}
        except Exception:  # noqa: BLE001
            tags = {}
        number = _int(((tags.get("tracknumber") or [""])[0]).split("/")[0]) or _int(re.match(r"\d+", f.name) and re.match(r"\d+", f.name)[0])
        disc = _int(((tags.get("discnumber") or [""])[0]).split("/")[0]) or 1
        title = norm(re.sub(r"^\d+\s*-\s*", "", (tags.get("title") or [f.stem])[0]))
        free = [t for t in tracks if t["id"] not in used]
        t = next((t for t in free if norm(t["title"]) == title), None) \
            or next((t for t in free if number and _int(t.get("trackNumber")) == number and (t.get("mediumNumber") or 1) == disc), None) \
            or next((t for t in free if number and t.get("absoluteTrackNumber") == number), None)
        item = preview.get(str(f))
        if not t or not item:
            continue
        used.add(t["id"])
        picked.append({"path": str(f), "artistId": album["artistId"], "albumId": album["id"], "albumReleaseId": release["id"], "trackIds": [t["id"]],
                       "quality": item.get("quality"), "indexerFlags": 0, "disableReleaseSwitching": True})
    if not picked:
        return None, "None of the files matched a track of the album"
    cmd = arr.call("lidarr", "POST", "/command", json={"name": "ManualImport", "files": picked, "importMode": "move", "replaceExistingFiles": False})
    return cmd.get("id"), f"Lidarr is importing {len(picked)} of {release['trackCount']} songs from YouTube"


def _int(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return None


def tag(folder: Path, album):
    """The album's MusicBrainz ids in every file, so Lidarr matches it to this album and no other."""
    import mutagen
    files = [p for p in folder.rglob("*") if p.is_file()]
    # the album's id always; a release's only when one has exactly this many tracks (else Lidarr picks the closest)
    release = next((r for r in album.get("releases") or [] if r.get("trackCount") == len(files)), None)
    ids = {"musicbrainz_releasegroupid": album.get("foreignAlbumId"), "musicbrainz_artistid": (album.get("artist") or {}).get("foreignArtistId")}
    if release:
        ids["musicbrainz_albumid"] = release.get("foreignReleaseId")
    for path in files:
        if not path.is_file():
            continue
        try:
            f = mutagen.File(path, easy=True)
            if f is None:
                continue
            for k, v in ids.items():
                if v:
                    f[k] = v
            f.save()
        except Exception:  # noqa: BLE001  (a file Lidarr can still match by its other tags)
            log.warning("couldn't tag %s", path)


def importing(row, album, now):
    cmd = arr.call("lidarr", "GET", f"/command/{row['command']}") if row["command"] else {"status": "completed"}
    running = cmd.get("status") in ("queued", "started")
    if running and now - (row["moved"] or now) < IMPORT_WAIT:  # a big album takes a while: still going isn't stuck
        return set_(row["id"], why=cmd.get("message") or row["why"])
    if not running and now - (cmd.get("ended") and iso(cmd["ended"]) or row["moved"] or now) < 60:  # its counts land just after
        return None
    past = since(history(album), row["moved"] or row["created"], "albumImportIncomplete", "trackFileImported")
    why = cmd.get("message") or "Lidarr didn't import the files"
    incomplete = [h for h in past if h.get("eventType") == "albumImportIncomplete"]
    if incomplete:
        why = str((incomplete[-1].get("data") or {}).get("statusMessages") or why)[:300]
    set_(row["id"], state="failed", why=f"{why}. The files are in {STAGING.name}/request-{row['id']}; Retry imports them again")


def clean(row):
    folder = STAGING / f"request-{row['id']}"
    if folder.is_dir() and not any(p.is_file() for p in folder.rglob("*")):
        shutil.rmtree(folder, ignore_errors=True)


# --- the worker ---------------------------------------------------------------------------------

def tick(now: Optional[float] = None):
    """Every request that's due, one step each; Lidarr down means try again later, with backoff."""
    now = now or time.time()
    if not arr.key("lidarr"):
        return
    with _lock:
        _handed["n"] = 0
        try:
            from_wanted(now)
        except Exception as exc:  # noqa: BLE001  (the list still moves without it)
            log.warning("couldn't read Lidarr's wanted list: %s", exc)
        due = [r["id"] for r in rows(f"SELECT id FROM music_requests WHERE state IN ({','.join('?' * len(OPEN))}) AND next_at <= ? ORDER BY id", *OPEN, now)]
        for rid in due:
            row = one("SELECT * FROM music_requests WHERE id = ?", rid)  # fresh: the page may have just moved it
            if not row or row["state"] not in OPEN:
                continue
            try:
                advance(row, now)
                if row["tries"]:
                    set_(rid, tries=0, next_at=0)
            except Exception as exc:  # noqa: BLE001  (one stuck row never stops the others)
                tries = row["tries"] + 1
                if tries >= MAX_ERRORS:
                    set_(rid, state="failed", tries=tries, why=f"Gave up after {tries} errors in a row: {str(exc)[:160]}")
                    continue
                wait = min(EVERY * 2 ** tries, 6 * 3600)
                set_(rid, tries=tries, next_at=now + wait, why=f"Lidarr didn't answer ({str(exc)[:120]}); trying again in {wait // 60} min")


def start():
    def loop():
        while True:
            started = time.time()
            try:
                tick()
            except Exception:  # noqa: BLE001
                log.exception("music requests: the check failed")
            wake.wait(EVERY)
            wake.clear()
            time.sleep(max(0.0, 30 - (time.time() - started)))  # a burst of webhooks makes one check, not dozens
    threading.Thread(target=loop, daemon=True, name="music-requests").start()


wake = threading.Event()  # a webhook from Lidarr checks now instead of in ten minutes
hooks = APIRouter()  # outside /api: Lidarr calls it with no session, so the address carries a secret instead


def hook_token() -> str:
    from core import set_setting
    token = setting("lidarr_hook")
    if not token:
        import secrets
        token = secrets.token_urlsafe(24)
        set_setting("lidarr_hook", token)
    return token


@hooks.post("/hooks/lidarr/{token}")
def lidarr_hook(token: str):
    import hmac

    import vault
    if vault.locked():  # the token lives in the encrypted database; the ten-minute check carries on once it's open
        raise HTTPException(423, "Parlor is locked")
    if not hmac.compare_digest(token.encode(), hook_token().encode()):
        raise HTTPException(404, "Not Found")
    wake.set()
    return {"ok": True}


# --- the page -----------------------------------------------------------------------------------

def view(r):
    return {"id": r["id"], "title": r["title"], "by": r["artist_name"], "image": r["image"], "state": r["state"], "label": LABEL.get(r["state"], r["state"]),
            "why": r["why"], "via": r["via"], "created": r["created"], "updated": r["updated"],
            "candidates": json.loads(r["candidates"]) if r["candidates"] else [], "mine": None}


@router.get("")
def listing(user=Depends(me)):
    q = "SELECT * FROM music_requests" + ("" if user["admin"] else " WHERE user_id = ?") + " ORDER BY updated DESC LIMIT 300"
    return {"items": [{**view(r), "mine": r["user_id"] == user["id"]} for r in rows(q, *(() if user["admin"] else (user["id"],)))]}


def _own(row_id, user):
    r = one("SELECT * FROM music_requests WHERE id = ?", row_id)
    if not r or not (user["admin"] or r["user_id"] == user["id"]):  # Lidarr's own wanted list is the admins'

        raise HTTPException(404, "No such request")
    return r


class Choice(BaseModel):
    spotify: str


@router.post("/{row_id}/choose")
def choose(row_id: int, body: Choice, user=Depends(me)):
    with _lock:
        r = _own(row_id, user)
        picks = {c["id"] for c in json.loads(r["candidates"] or "[]")}
        if r["state"] != "needs_look" or (body.spotify not in picks and not re.fullmatch(r"[A-Za-z0-9]{22}", body.spotify)):
            raise HTTPException(409, "Pick one of the albums shown")
        stop_lidarr(r["album"])
        fetch(row_id, body.spotify, "You picked it")
    return view(one("SELECT * FROM music_requests WHERE id = ?", row_id))


@router.post("/{row_id}/retry")
def retry(row_id: int, user=Depends(me)):
    with _lock:
        return _retry(_own(row_id, user), row_id)


def _retry(r, row_id):
    if r["state"] not in ("failed", "needs_look"):
        raise HTTPException(409, "It's still moving")
    if r["via"] == "youtube" and r["spotify"] and any(p.is_file() for p in (STAGING / f"request-{row_id}").rglob("*")):
        cmd, why = import_files(STAGING / f"request-{row_id}", arr.call("lidarr", "GET", f"/album/{r['album']}"))
        set_(row_id, state="importing", command=cmd, moved=time.time(), tries=0, next_at=0, why=why)
    elif r["via"] == "youtube" and r["spotify"]:
        fetch(row_id, r["spotify"], "Trying YouTube again")
        set_(row_id, tries=0, yt_tries=0, next_at=0)
    else:
        arr.call("lidarr", "POST", "/command", json={"name": "AlbumSearch", "albumIds": [r["album"]]})
        set_(row_id, state="searching", searched=time.time(), tries=0, next_at=0, why="")
    wake.set()
    return view(one("SELECT * FROM music_requests WHERE id = ?", row_id))


@router.post("/{row_id}/youtube")
def from_youtube(row_id: int, user=Depends(me)):
    """Don't wait for Lidarr: hand it over now."""
    with _lock:
        r = _own(row_id, user)
        if r["state"] not in ("searching", "downloading", "importing", "failed") or not r["album"] or r["via"] == "youtube" and r["state"] != "failed":
            raise HTTPException(409, "It can't be handed over now")
        album = arr.call("lidarr", "GET", f"/album/{r['album']}")
        give_up(r, album, "You asked for YouTube", time.time(), {"title": album.get("title") or r["title"], "image": r["image"],
                "artist_name": (album.get("artist") or {}).get("artistName") or r["artist_name"]}, by_hand=True)
    return view(one("SELECT * FROM music_requests WHERE id = ?", row_id))
