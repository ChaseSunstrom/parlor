"""A Subsonic API (1.16.1) with the OpenSubsonic extensions at /rest/*, over the music folder's index (library.py),
so Symfonium, Tempo, Feishin, DSub and the like can use yeerr as their music server. Every account has its own
stars, ratings, plays, playlists, play queue and bookmarks; the music is shared. Not behind the session
cookie (apps don't have one): each call logs in with u+p, u+t+s (token) or apiKey, and the token and key
logins use per-account app passwords, made at /api/me/app-passwords."""

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.responses import Response

import auth
import core
import library
import music
import yt
from core import db, me, one, rows

router = APIRouter()
VERSION, API = "1.0.0", "1.16.1"
TYPES = {"mp3": "audio/mpeg", "flac": "audio/flac", "ogg": "audio/ogg", "opus": "audio/ogg", "m4a": "audio/mp4",
         "wav": "audio/wav"}
OPEN = {"ping", "getLicense", "getOpenSubsonicExtensions"}  # work without credentials (wrong ones are still refused)
BINARY = {"stream": 0, "download": 0, "getCoverArt": 0}  # errors here are also HTTP errors, not just XML
HTTP_STATUS = {10: 400, 40: 401, 41: 401, 44: 401, 50: 403, 70: 404}
ENDPOINTS: dict = {}
_now: dict = {}  # user id -> (track id, started, player): who is playing what right now
_transcodes = threading.BoundedSemaphore(4)  # ffmpeg is heavy: a few at a time


class Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def api(*names):
    def register(fn):
        for name in names:
            ENDPOINTS[name] = fn
        return fn
    return register


class P:
    """The call's parameters (query string and form body; a name can repeat)."""

    def __init__(self, pairs):
        self.pairs = pairs

    def get(self, key, default=None):
        return next((v for k, v in reversed(self.pairs) if k == key), default)

    def all(self, key):
        return [v for k, v in self.pairs if k == key]

    def need(self, key):
        v = self.get(key)
        if v in (None, ""):
            raise Fail(10, f"Required parameter is missing: {key}")
        return v

    def int(self, key, default=None, lo=None, hi=None):
        v = self.get(key)
        if v in (None, ""):
            return default
        try:
            n = int(float(v))
        except ValueError:
            raise Fail(10, f"{key} must be a number")
        return n if lo is None else max(lo, n if hi is None else min(hi, n))


class C:
    def __init__(self, p, req, user):
        self.p, self.req, self.u = p, req, user

    @property
    def uid(self):
        return self.u["id"]

    def admin(self):
        if not self.u["admin"]:
            raise Fail(50, "Only an admin can do that")


# --- the envelope: XML (default) or JSON ------------------------------------------------------------

def clean(x):
    """Nothing for None and empty lists, as Subsonic's JSON leaves them out."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items() if v is not None and v != []}
    return [clean(v) for v in x] if isinstance(x, list) else x


def text(v):
    v = "true" if v is True else "false" if v is False else str(v)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)  # not allowed in XML at all


def element(tag, value):
    el = ET.Element(tag)
    if not isinstance(value, dict):
        el.text = text(value)
        return el
    for k, v in value.items():
        if k == "value":
            el.text = text(v)
        elif isinstance(v, list):
            el.extend(element(k, i) for i in v)
        elif isinstance(v, dict):
            el.append(element(k, v))
        else:
            el.set(k, text(v))
    return el


def reply(p, payload, status="ok", http=200):
    body = clean({"status": status, "version": API, "type": "yeerr", "serverVersion": VERSION, "openSubsonic": True,
                  **payload})
    fmt = p.get("f", "xml")
    if fmt in ("json", "jsonp"):
        out, kind = json.dumps({"subsonic-response": body}), "application/json"
        callback = p.get("callback", "")
        if fmt == "jsonp" and re.fullmatch(r"[\w.]{1,64}", callback):
            out, kind = f"{callback}({out});", "application/javascript"
        return Response(out, http, media_type=kind)
    root = element("subsonic-response", body)
    root.set("xmlns", "http://subsonic.org/restapi")
    return Response(ET.tostring(root, encoding="utf-8", xml_declaration=True), http, media_type="application/xml")


@router.api_route("/rest/{method}", methods=["GET", "POST"])
async def rest(method: str, request: Request):
    pairs = list(request.query_params.multi_items())
    if request.method == "POST":  # formPost: the same parameters in the body
        body = (await request.body())[:1_000_000]
        pairs += parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
    return await anyio.to_thread.run_sync(handle, method.removesuffix(".view"), P(pairs), request)


def handle(name, p, request):
    try:
        fn = ENDPOINTS.get(name)
        if not fn:
            raise Fail(0, f"Unknown method: {name[:40]}")
        out = fn(C(p, request, login(p, request, name)))
        return out if isinstance(out, Response) else reply(p, out)
    except Fail as e:
        http = HTTP_STATUS.get(e.code, 200) if name in BINARY else 200
        return reply(p, {"error": {"code": e.code, "message": e.message}}, "failed", http)


# --- logging in ---------------------------------------------------------------------------------------

def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def login(p, request, method):
    key, name = p.get("apiKey"), p.get("u")
    if method == "getOpenSubsonicExtensions" or (method in OPEN and not key and not name):
        return None
    if not key and not name:
        raise Fail(10, "Required parameter is missing: u")
    keys = (f"name:{(name or '').casefold()}", f"ip:{auth._client(request)}")
    if auth._throttled(*keys):
        raise Fail(40, "Too many wrong attempts. Wait a few minutes and try again.")
    try:
        user, used = check(p, key, name)
    except Fail:
        auth._failed(*keys)
        raise
    if used:
        now = time.time()
        db().execute("UPDATE app_passwords SET used = ? WHERE id = ? AND (used IS NULL OR used < ?)", (now, used, now - 3600))
    return user


def check(p, key, name):
    """(user, id of the app password that matched, or None for the account password)."""
    if key:
        r = one("""SELECT a.id AS app, u.id, u.name, u.admin FROM app_passwords a JOIN users u ON u.id = a.user_id
                   WHERE a.key_hash = ?""", hashlib.sha256(key.encode()).hexdigest())
        if not r:
            raise Fail(44, "Invalid API key")
        return r, r["app"]
    user = one("SELECT id, name, admin, pw FROM users WHERE name = ?", name)
    apps = rows("SELECT id, secret FROM app_passwords WHERE user_id = ?", user["id"]) if user else []
    token, password = p.get("t"), p.get("p")
    if token:
        if not apps:
            raise Fail(41, "Token login needs an app password: make one in yeerr's settings, or send a password instead")
        salt = p.get("s", "")
        hit = next((a for a in apps if same(hashlib.md5((a["secret"] + salt).encode()).hexdigest(), token.lower())), None)
    elif password is not None:
        if password.startswith("enc:"):
            try:
                password = bytes.fromhex(password[4:]).decode()
            except ValueError:
                raise Fail(40, "Wrong username or password")
        hit = next((a for a in apps if same(a["secret"], password)), None)
        if not hit and not core.check_password(password, user["pw"] if user else "scrypt$00$00"):  # same work for unknown names
            user = None
        elif not hit and user:
            hit = {"id": None}
    else:
        raise Fail(10, "Required parameter is missing: p")
    if not user or not hit:
        raise Fail(40, "Wrong username or password")
    return user, hit["id"]


class NewKey(BaseModel):
    name: str


@router.get("/api/me/app-passwords")
def list_keys(user=Depends(me)):
    return rows("SELECT id, name, created, used FROM app_passwords WHERE user_id = ? ORDER BY id", user["id"])


@router.post("/api/me/app-passwords")
def make_key(body: NewKey, user=Depends(me)):
    """The secret is shown here once; it is also the apiKey."""
    name = body.name.strip()[:40]
    if not name:
        raise HTTPException(422, "Give it a name, like the app it is for")
    if one("SELECT COUNT(*) AS n FROM app_passwords WHERE user_id = ?", user["id"])["n"] >= 20:
        raise HTTPException(409, "That's enough app passwords; delete some first")
    secret = secrets.token_urlsafe(18)
    cur = db().execute("INSERT INTO app_passwords(user_id, name, secret, key_hash, created) VALUES (?, ?, ?, ?, ?)",
                       (user["id"], name, secret, hashlib.sha256(secret.encode()).hexdigest(), time.time()))
    return {"id": cur.lastrowid, "name": name, "secret": secret}


@router.delete("/api/me/app-passwords/{key_id}")
def drop_key(key_id: int, user=Depends(me)):
    if not db().execute("DELETE FROM app_passwords WHERE id = ? AND user_id = ?", (key_id, user["id"])).rowcount:
        raise HTTPException(404, "No such app password")
    return {"ok": True}


# --- shapes ---------------------------------------------------------------------------------------------

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bits(m):
    """What this account has done with an item: from a sub_meta row or a query that carries its columns."""
    m = m or {}
    return {"starred": iso(m["starred"]) if m.get("starred") else None, "userRating": m.get("rating") or None,
            "playCount": m.get("plays") or None, "played": iso(m["played"]) if m.get("played") else None}


def chunked(sql, ids, *before):
    """Rows for an `IN ({})` query over many ids, 500 at a time."""
    ids = list(ids)
    return [r for i in range(0, len(ids), 500)
            for r in rows(sql.format(",".join("?" * len(ids[i:i + 500]))), *before, *ids[i:i + 500])]


def song(t, m=None):
    return {"id": t["id"], "parent": t["album_id"], "isDir": False, "title": t["title"], "album": t["album"],
            "artist": t["artist"], "track": t["track"], "year": t["year"], "genre": t["genre"], "coverArt": t["album_id"],
            "size": t["size"], "contentType": TYPES.get(t["suffix"], "audio/mpeg"), "suffix": t["suffix"],
            "duration": int(round(t["secs"])), "bitRate": t["bitrate"] or None, "path": t["path"],
            "discNumber": t["disc"], "created": iso(t["mtime"]), "albumId": t["album_id"], "artistId": t["artist_id"],
            "type": "music", "isVideo": False, "mediaType": "song", "displayArtist": t["artist"],
            "genres": [{"name": t["genre"]}] if t["genre"] else None,
            "artists": [{"id": t["artist_id"], "name": t["album_artist"]}], **bits(m)}


def songs_in(uid, ids):
    """Songs for these ids, in that order; ids that are gone from the library are skipped."""
    found = {t["id"]: t for t in chunked("SELECT * FROM tracks WHERE id IN ({})", ids)}
    metas = {m["item"]: m for m in chunked("SELECT * FROM sub_meta WHERE user_id = ? AND item IN ({})", found, uid)}
    return [song(found[i], metas.get(i)) for i in ids if i in found]


def songs(uid, where="", args=(), order="COALESCE(t.disc, 0), COALESCE(t.track, 0), t.path", limit=-1, offset=0):
    ts = rows(f"SELECT t.* FROM tracks t {where} ORDER BY {order} LIMIT ? OFFSET ?", *args, limit, offset)
    return songs_in(uid, [t["id"] for t in ts])


ALBUMS = """SELECT t.album_id AS id, t.album AS name, t.album_artist AS artist, t.artist_id, COUNT(*) AS songs,
  SUM(t.secs) AS secs, MIN(NULLIF(t.year, 0)) AS year, MIN(t.genre) AS genre, MAX(t.mtime) AS created,
  COALESCE(SUM(m.plays), 0) AS plays, MAX(m.played) AS played, a.starred, a.rating
  FROM tracks t LEFT JOIN sub_meta m ON m.item = t.id AND m.user_id = ?
  LEFT JOIN sub_meta a ON a.item = t.album_id AND a.user_id = ? """


def albums(uid, where="", args=(), tail="ORDER BY name COLLATE NOCASE", limit=-1, offset=0):
    return rows(f"{ALBUMS} {where} GROUP BY t.album_id {tail} LIMIT ? OFFSET ?", uid, uid, *args, limit, offset)


def album_dict(a, as_dir=False):
    d = {"id": a["id"], "name": a["name"], "artist": a["artist"], "artistId": a["artist_id"], "coverArt": a["id"],
         "songCount": a["songs"], "duration": int(a["secs"] or 0), "year": a["year"], "genre": a["genre"],
         "created": iso(a["created"]), "mediaType": "album", **bits(a)}
    if as_dir:  # the folder-style forms (getAlbumList, search2, getMusicDirectory)
        d.update(isDir=True, title=a["name"], album=a["name"], parent=a["artist_id"])
    return d


def artists(uid, where="", args=()):
    return rows(f"""SELECT t.artist_id AS id, MIN(t.album_artist) AS name, COUNT(DISTINCT t.album_id) AS albums,
                    a.starred, a.rating FROM tracks t LEFT JOIN sub_meta a ON a.item = t.artist_id AND a.user_id = ?
                    {where} GROUP BY t.artist_id ORDER BY name COLLATE NOCASE""", uid, *args)


def artist_dict(a):
    return {"id": a["id"], "name": a["name"], "albumCount": a["albums"], "coverArt": a["id"], "mediaType": "artist",
            **bits(a)}


def one_album(uid, album_id):
    a = next(iter(albums(uid, "WHERE t.album_id = ?", (album_id,))), None)
    if not a:
        raise Fail(70, "Album not found")
    return a


def track(track_id):
    t = one("SELECT * FROM tracks WHERE id = ?", track_id)
    if not t:
        raise Fail(70, "Song not found")
    return t


def search_where(fields, query, prefix="WHERE"):
    """Every word of the query must start a word in `fields` (so "%" is just a character)."""
    sql, args = [], []
    for word in query.replace('"', " ").split():
        w = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql.append(f"({fields} LIKE ? ESCAPE '\\' OR {fields} LIKE ? ESCAPE '\\')")
        args += [w + "%", "% " + w + "%"]
    return (f"{prefix} " + " AND ".join(sql) if sql else ""), args


# --- system and browsing -------------------------------------------------------------------------------------

@api("ping")
def ping(c):
    return {}


@api("getLicense")
def license_(c):
    return {"license": {"valid": True, "email": "", "licenseExpires": "2099-12-31T00:00:00Z"}}


@api("getOpenSubsonicExtensions")
def extensions(c):
    return {"openSubsonicExtensions": [{"name": n, "versions": [1]} for n in ("apiKeyAuthentication", "formPost", "songLyrics")]}


@api("getMusicFolders")
def music_folders(c):
    return {"musicFolders": {"musicFolder": [{"id": 1, "name": "Music"}]}}


def index_of(artists_):
    groups: dict = {}
    for a in artists_:
        letter = a["name"][:1].upper()
        groups.setdefault(letter if letter.isalpha() else "#", []).append(artist_dict(a))
    return [{"name": k, "artist": v} for k, v in sorted(groups.items())]


@api("getArtists")
def get_artists(c):
    return {"artists": {"ignoredArticles": "", "index": index_of(artists(c.uid))}}


@api("getIndexes")
def get_indexes(c):
    last = one("SELECT MAX(mtime) AS m FROM tracks")["m"] or 0
    return {"indexes": {"ignoredArticles": "", "lastModified": int(last * 1000), "index": index_of(artists(c.uid))}}


@api("getArtist")
def get_artist(c):
    a = next(iter(artists(c.uid, "WHERE t.artist_id = ?", (c.p.need("id"),))), None)
    if not a:
        raise Fail(70, "Artist not found")
    kids = albums(c.uid, "WHERE t.artist_id = ?", (a["id"],), "ORDER BY year, name COLLATE NOCASE")
    return {"artist": {**artist_dict(a), "album": [album_dict(k) for k in kids]}}


@api("getAlbum")
def get_album(c):
    a = one_album(c.uid, c.p.need("id"))
    return {"album": {**album_dict(a), "song": songs(c.uid, "WHERE t.album_id = ?", (a["id"],))}}


@api("getSong")
def get_song(c):
    return {"song": songs_in(c.uid, [track(c.p.need("id"))["id"]])[0]}


@api("getMusicDirectory")
def music_directory(c):
    i = c.p.need("id")
    if i.startswith("al-"):
        a = one_album(c.uid, i)
        return {"directory": {"id": i, "parent": a["artist_id"], "name": a["name"], "child": songs(c.uid, "WHERE t.album_id = ?", (i,))}}
    if i.startswith("ar-"):
        a = next(iter(artists(c.uid, "WHERE t.artist_id = ?", (i,))), None)
        if not a:
            raise Fail(70, "Artist not found")
        kids = albums(c.uid, "WHERE t.artist_id = ?", (i,), "ORDER BY year, name COLLATE NOCASE")
        return {"directory": {"id": i, "parent": "1", "name": a["name"], "child": [album_dict(k, True) for k in kids]}}
    if i == "1":  # the music folder itself: its artists
        return {"directory": {"id": "1", "name": "Music", "child": [
            {"id": a["id"], "parent": "1", "isDir": True, "title": a["name"], "artist": a["name"]} for a in artists(c.uid)]}}
    raise Fail(70, "Directory not found")


@api("getArtistInfo")
def artist_info(c):
    return {"artistInfo": {}}


@api("getArtistInfo2")
def artist_info2(c):
    return {"artistInfo2": {}}


@api("getAlbumInfo", "getAlbumInfo2")
def album_info(c):
    return {"albumInfo": {}}


@api("getSimilarSongs")
def similar(c):
    return {"similarSongs": {}}


@api("getSimilarSongs2")
def similar2(c):
    return {"similarSongs2": {}}


@api("getTopSongs")
def top_songs(c):
    return {"topSongs": {}}


def album_list(c, key, as_dir):
    kind = c.p.need("type")
    size, offset = c.p.int("size", 10, 1, 500), c.p.int("offset", 0, 0)
    where, args, tail = "", [], "ORDER BY name COLLATE NOCASE"
    if kind == "random":
        tail = "ORDER BY RANDOM()"
    elif kind == "newest":
        tail = "ORDER BY created DESC"
    elif kind == "frequent":
        tail = "HAVING SUM(m.plays) > 0 ORDER BY SUM(m.plays) DESC"
    elif kind == "recent":
        tail = "HAVING MAX(m.played) IS NOT NULL ORDER BY MAX(m.played) DESC"
    elif kind == "starred":
        where = "WHERE a.starred IS NOT NULL"
    elif kind == "highest":
        where, tail = "WHERE a.rating IS NOT NULL", "ORDER BY a.rating DESC, name COLLATE NOCASE"
    elif kind == "alphabeticalByArtist":
        tail = "ORDER BY artist COLLATE NOCASE, name COLLATE NOCASE"
    elif kind == "byGenre":
        where, args = "WHERE t.genre = ? COLLATE NOCASE", [c.p.need("genre")]
    elif kind == "byYear":
        lo, hi = c.p.int("fromYear"), c.p.int("toYear")
        if lo is None or hi is None:
            raise Fail(10, "Required parameter is missing: fromYear and toYear")
        where, args = "WHERE t.year BETWEEN ? AND ?", [min(lo, hi), max(lo, hi)]
        tail = f"ORDER BY year {'DESC' if lo > hi else 'ASC'}, name COLLATE NOCASE"
    elif kind != "alphabeticalByName":
        raise Fail(0, f"Unknown list type: {kind[:40]}")
    return {key: {"album": [album_dict(a, as_dir) for a in albums(c.uid, where, args, tail, size, offset)]}}


@api("getAlbumList")
def album_list_old(c):
    return album_list(c, "albumList", True)


@api("getAlbumList2")
def album_list2(c):
    return album_list(c, "albumList2", False)


def song_filter(p):
    where, args = [], []
    if p.get("genre"):
        where.append("t.genre = ? COLLATE NOCASE")
        args.append(p.get("genre"))
    if p.int("fromYear") is not None:
        where.append("t.year >= ?")
        args.append(p.int("fromYear"))
    if p.int("toYear") is not None:
        where.append("t.year <= ?")
        args.append(p.int("toYear"))
    return ("WHERE " + " AND ".join(where) if where else ""), args


@api("getRandomSongs")
def random_songs(c):
    where, args = song_filter(c.p)
    return {"randomSongs": {"song": songs(c.uid, where, args, "RANDOM()", c.p.int("size", 10, 1, 500))}}


@api("getSongsByGenre")
def songs_by_genre(c):
    c.p.need("genre")
    where, args = song_filter(c.p)
    return {"songsByGenre": {"song": songs(c.uid, where, args, "t.title COLLATE NOCASE, t.path",
                                           c.p.int("count", 10, 1, 500), c.p.int("offset", 0, 0))}}


@api("getGenres")
def genres(c):
    return {"genres": {"genre": [{"songCount": g["n"], "albumCount": g["a"], "value": g["genre"]} for g in rows(
        """SELECT MIN(genre) AS genre, COUNT(*) AS n, COUNT(DISTINCT album_id) AS a FROM tracks
           WHERE genre IS NOT NULL GROUP BY genre COLLATE NOCASE ORDER BY genre COLLATE NOCASE""")]}}


def search(c, key, as_dir):
    q = c.p.get("query", "")
    out = {}
    for what, default in (("artist", 20), ("album", 20), ("song", 20)):
        size, offset = c.p.int(what + "Count", default, 0, 500), c.p.int(what + "Offset", 0, 0)
        if what == "artist":
            where, args = search_where("t.album_artist", q)
            out[what] = [artist_dict(a) for a in artists(c.uid, where, args)[offset:offset + size]]
        elif what == "album":
            where, args = search_where("(t.album || ' ' || t.album_artist)", q)
            out[what] = [album_dict(a, as_dir) for a in albums(c.uid, where, args, limit=size, offset=offset)]
        else:
            where, args = search_where("(t.title || ' ' || t.artist || ' ' || t.album)", q)
            out[what] = songs(c.uid, where, args, "t.title COLLATE NOCASE, t.path", size, offset)
    return {key: out}


@api("search2")
def search2(c):
    return search(c, "searchResult2", True)


@api("search3")
def search3(c):
    return search(c, "searchResult3", False)


# --- stars, ratings, plays -------------------------------------------------------------------------------------

def starred(c, key, as_dir):
    return {key: {"artist": [artist_dict(a) for a in artists(c.uid, "WHERE a.starred IS NOT NULL")],
                  "album": [album_dict(a, as_dir) for a in albums(c.uid, "WHERE a.starred IS NOT NULL")],
                  "song": songs(c.uid, "JOIN sub_meta m ON m.item = t.id AND m.user_id = ? AND m.starred IS NOT NULL",
                                [c.uid], "m.starred DESC")}}


@api("getStarred")
def get_starred(c):
    return starred(c, "starred", True)


@api("getStarred2")
def get_starred2(c):
    return starred(c, "starred2", False)


def known(item):
    return one("SELECT 1 AS x FROM tracks WHERE id = ? OR album_id = ? OR artist_id = ? LIMIT 1", item, item, item)


def mark(c, on):
    items = c.p.all("id") + c.p.all("albumId") + c.p.all("artistId")
    if not items:
        raise Fail(10, "Required parameter is missing: id")
    if not all(known(i) for i in items):
        raise Fail(70, "Not found")
    for i in items:
        db().execute("""INSERT INTO sub_meta(user_id, item, starred) VALUES (?, ?, ?)
                        ON CONFLICT(user_id, item) DO UPDATE SET starred = excluded.starred""",
                     (c.uid, i, time.time() if on else None))
    return {}


@api("star")
def star(c):
    return mark(c, True)


@api("unstar")
def unstar(c):
    return mark(c, False)


@api("setRating")
def set_rating(c):
    item, rating = c.p.need("id"), c.p.int("rating")
    if rating is None or not 0 <= rating <= 5:
        raise Fail(10, "rating is 0 (none) to 5")
    if not known(item):
        raise Fail(70, "Not found")
    db().execute("""INSERT INTO sub_meta(user_id, item, rating) VALUES (?, ?, ?)
                    ON CONFLICT(user_id, item) DO UPDATE SET rating = excluded.rating""", (c.uid, item, rating or None))
    return {}


@api("scrobble")
def scrobble(c):
    ids, times = c.p.all("id"), c.p.all("time")
    if not ids:
        raise Fail(10, "Required parameter is missing: id")
    ts = [track(i) for i in ids]
    if c.p.get("submission", "true").lower() == "false":
        _now[c.uid] = (ts[-1]["id"], time.time(), c.p.get("c", ""))
        return {}
    for n, t in enumerate(ts):
        try:
            when = int(times[n]) / 1000 if n < len(times) else time.time()
        except ValueError:
            when = time.time()
        db().execute("""INSERT INTO sub_meta(user_id, item, plays, played) VALUES (?, ?, 1, ?)
                        ON CONFLICT(user_id, item) DO UPDATE SET plays = plays + 1, played = MAX(IFNULL(played, 0), excluded.played)""",
                     (c.uid, t["id"], when))
    return {}


@api("getNowPlaying")
def now_playing(c):
    """Only your own: what one account plays isn't for the others to see."""
    mine = _now.get(c.uid)
    if not mine:
        return {"nowPlaying": {}}
    found = songs_in(c.uid, [mine[0]])
    if not found or time.time() - mine[1] > found[0]["duration"] + 60:
        return {"nowPlaying": {}}
    return {"nowPlaying": {"entry": [{**found[0], "username": c.u["name"], "minutesAgo": int((time.time() - mine[1]) // 60),
                                      "playerId": 0, "playerName": mine[2]}]}}


# --- playlists: each account's own (public ones can be read by all), plus music-fndr's synced files, read-only ----

PL = """SELECT p.*, u.name AS owner,
  (SELECT COUNT(*) FROM sub_playlist_tracks x JOIN tracks t ON t.id = x.track_id WHERE x.playlist_id = p.id) AS n,
  (SELECT COALESCE(SUM(t.secs), 0) FROM sub_playlist_tracks x JOIN tracks t ON t.id = x.track_id WHERE x.playlist_id = p.id) AS secs,
  (SELECT t.album_id FROM sub_playlist_tracks x JOIN tracks t ON t.id = x.track_id WHERE x.playlist_id = p.id ORDER BY x.pos LIMIT 1) AS cover
  FROM sub_playlists p JOIN users u ON u.id = p.user_id """


def synced():
    """music-fndr's playlist files (Playlists/*.m3u8): id -> file."""
    folder = library.root() / "Playlists"
    return {library.hid("m3u", f.name): f for f in sorted(folder.glob("*.m3u*"))} if folder.is_dir() else {}


def m3u_tracks(file):
    lines = [x.strip() for x in file.read_text("utf-8", "replace")[:2_000_000].splitlines()]  # ponytail: first 2 MB
    name = next((x[10:].strip() for x in lines if x.startswith("#PLAYLIST:")), file.stem)
    rels = [os.path.normpath(os.path.join("Playlists", x)).replace(os.sep, "/") for x in lines if x and not x[0] == "#"]
    found = {t["path"]: t for t in chunked("SELECT * FROM tracks WHERE path IN ({})", set(rels))}
    return name, [found[r] for r in rels if r in found]


def find_playlist(c, pid, write=False):
    """(row shaped like PL's, entry track ids) or a Fail: 70 if it isn't yours to see, 50 if you can see but not change."""
    if pid in synced():
        if write:
            raise Fail(50, "Synced playlists are read-only")
        file = synced()[pid]
        name, ts = m3u_tracks(file)
        m = file.stat().st_mtime
        return ({"id": pid, "name": name, "comment": "", "public": 1, "owner": "music-fndr", "created": m, "changed": m,
                 "n": len(ts), "secs": sum(t["secs"] for t in ts), "cover": ts[0]["album_id"] if ts else None, "user_id": 0},
                [t["id"] for t in ts])
    num = pid[3:] if pid.startswith("pl-") else ""
    row = one(PL + "WHERE p.id = ?", int(num)) if num.isdigit() else None
    if not row or (row["user_id"] != c.uid and not row["public"]):
        raise Fail(70, "Playlist not found")
    if write and row["user_id"] != c.uid:
        raise Fail(50, "That playlist isn't yours")
    return row, [r["track_id"] for r in rows("SELECT track_id FROM sub_playlist_tracks WHERE playlist_id = ? ORDER BY pos", row["id"])]


def playlist_dict(r):
    pid = r["id"] if isinstance(r["id"], str) else f"pl-{r['id']}"
    return {"id": pid, "name": r["name"], "comment": r["comment"], "owner": r["owner"], "public": bool(r["public"]),
            "songCount": r["n"], "duration": int(r["secs"]), "created": iso(r["created"]), "changed": iso(r["changed"]),
            "coverArt": r["cover"]}


def set_tracks(playlist_id, ids):
    conn = db()
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM sub_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.executemany("INSERT INTO sub_playlist_tracks(playlist_id, pos, track_id) VALUES (?, ?, ?)",
                         [(playlist_id, n, i) for n, i in enumerate(ids)])
        conn.execute("UPDATE sub_playlists SET changed = ? WHERE id = ?", (time.time(), playlist_id))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def full_playlist(c, pid):
    row, ids = find_playlist(c, pid)
    return {"playlist": {**playlist_dict(row), "entry": songs_in(c.uid, ids)}}


@api("getPlaylists")
def get_playlists(c):
    mine = rows(PL + "WHERE p.user_id = ? OR p.public = 1 ORDER BY p.name COLLATE NOCASE", c.uid)
    return {"playlists": {"playlist": [playlist_dict(r) for r in mine] +
                          [playlist_dict(find_playlist(c, i)[0]) for i in synced()]}}


@api("getPlaylist")
def get_playlist(c):
    return full_playlist(c, c.p.need("id"))


def wanted_tracks(p, key):
    ids = p.all(key)
    for i in ids:
        track(i)
    return ids


@api("createPlaylist")
def create_playlist(c):
    ids = wanted_tracks(c.p, "songId")
    pid = c.p.get("playlistId")
    if pid:
        row, _ = find_playlist(c, pid, write=True)
        if c.p.get("name"):
            db().execute("UPDATE sub_playlists SET name = ? WHERE id = ?", (c.p.get("name")[:200], row["id"]))
    else:
        now = time.time()
        row = {"id": db().execute("INSERT INTO sub_playlists(user_id, name, created, changed) VALUES (?, ?, ?, ?)",
                                  (c.uid, c.p.need("name")[:200], now, now)).lastrowid}
        pid = f"pl-{row['id']}"
    set_tracks(row["id"], ids)
    return full_playlist(c, pid)


@api("updatePlaylist")
def update_playlist(c):
    row, ids = find_playlist(c, c.p.need("playlistId"), write=True)
    drop = {int(i) for i in c.p.all("songIndexToRemove") if i.isdigit()}
    ids = [i for n, i in enumerate(ids) if n not in drop] + wanted_tracks(c.p, "songIdToAdd")
    db().execute("UPDATE sub_playlists SET name = ?, comment = ?, public = ? WHERE id = ?",
                 ((c.p.get("name") or row["name"])[:200], c.p.get("comment", row["comment"]),
                  int(c.p.get("public").lower() == "true") if c.p.get("public") else row["public"], row["id"]))
    set_tracks(row["id"], ids)
    return {}


@api("deletePlaylist")
def delete_playlist(c):
    row, _ = find_playlist(c, c.p.need("id"), write=True)
    db().execute("DELETE FROM sub_playlists WHERE id = ?", (row["id"],))  # its tracks go with it
    return {}


# --- play queue and bookmarks -----------------------------------------------------------------------------------

@api("savePlayQueue")
def save_play_queue(c):
    ids = c.p.all("id")
    if not ids:
        db().execute("DELETE FROM sub_queue WHERE user_id = ?", (c.uid,))
        return {}
    db().execute("""INSERT INTO sub_queue(user_id, tracks, current, position, changed, changed_by) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET tracks = excluded.tracks, current = excluded.current,
                    position = excluded.position, changed = excluded.changed, changed_by = excluded.changed_by""",
                 (c.uid, core.dumps(ids[:5000]), c.p.get("current"), c.p.int("position", 0, 0), time.time(), c.p.get("c", "")))
    return {}


@api("getPlayQueue")
def get_play_queue(c):
    q = one("SELECT * FROM sub_queue WHERE user_id = ?", c.uid)
    if not q:
        return {}
    return {"playQueue": {"current": q["current"], "position": q["position"], "username": c.u["name"],
                          "changed": iso(q["changed"]), "changedBy": q["changed_by"],
                          "entry": songs_in(c.uid, json.loads(q["tracks"]))}}


@api("createBookmark")
def create_bookmark(c):
    t, now = track(c.p.need("id")), time.time()
    db().execute("""INSERT INTO sub_bookmarks(user_id, track_id, position, comment, created, changed) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, track_id) DO UPDATE SET position = excluded.position, comment = excluded.comment,
                    changed = excluded.changed""", (c.uid, t["id"], c.p.int("position", 0, 0), (c.p.get("comment") or "")[:500], now, now))
    return {}


@api("getBookmarks")
def get_bookmarks(c):
    marks = rows("SELECT * FROM sub_bookmarks WHERE user_id = ? ORDER BY changed DESC", c.uid)
    entries = {s["id"]: s for s in songs_in(c.uid, [m["track_id"] for m in marks])}
    return {"bookmarks": {"bookmark": [
        {"position": m["position"], "username": c.u["name"], "comment": m["comment"], "created": iso(m["created"]),
         "changed": iso(m["changed"]), "entry": entries[m["track_id"]]} for m in marks if m["track_id"] in entries]}}


@api("deleteBookmark")
def delete_bookmark(c):
    db().execute("DELETE FROM sub_bookmarks WHERE user_id = ? AND track_id = ?", (c.uid, c.p.need("id")))
    return {}


# --- audio ----------------------------------------------------------------------------------------------------------

def file_of(t):
    path = library.root() / t["path"]
    if not path.is_file():
        raise Fail(70, "The file is gone")
    return path


@api("stream")
def stream(c):
    t = track(c.p.need("id"))
    path, fmt, kbps = file_of(t), (c.p.get("format") or "").lower(), c.p.int("maxBitRate", 0, 0)
    if fmt in ("mp3", "opus"):
        target = fmt if fmt != t["suffix"] or (kbps and (t["bitrate"] or 0) > kbps) else None
    else:
        target = "mp3" if fmt != "raw" and not fmt and kbps and (t["bitrate"] or 0) > kbps else None
    if not target or not shutil.which("ffmpeg"):
        return yt.send_file(path, c.req, TYPES.get(t["suffix"], "audio/mpeg"))
    kbps = min(max(kbps or (192 if target == "mp3" else 128), 32), 320)
    codec = ["-c:a", "libmp3lame", "-f", "mp3"] if target == "mp3" else ["-c:a", "libopus", "-f", "ogg"]
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:a:0", "-vn", "-threads", "1",
           *codec, "-b:a", f"{kbps}k", "pipe:1"]

    def pipe():
        if not _transcodes.acquire(timeout=30):
            return
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while chunk := proc.stdout.read(65536):
                yield chunk
        finally:  # a player that goes away mid-song stops the encoder
            proc.kill()
            proc.wait()
            _transcodes.release()

    return StreamingResponse(pipe(), media_type="audio/mpeg" if target == "mp3" else "audio/ogg")


@api("download")
def download(c):
    t = track(c.p.need("id"))
    return FileResponse(file_of(t), media_type=TYPES.get(t["suffix"], "audio/mpeg"), filename=Path(t["path"]).name)


# --- cover art ------------------------------------------------------------------------------------------------------

def cover_track(item):
    if item.startswith("tr-"):
        return one("SELECT * FROM tracks WHERE id = ?", item)
    if item.startswith("al-"):
        return one("SELECT * FROM tracks WHERE album_id = ? ORDER BY disc, track LIMIT 1", item)
    if item.startswith("ar-"):
        return one("SELECT * FROM tracks WHERE artist_id = ? ORDER BY year, album, disc, track LIMIT 1", item)
    if item.startswith(("pl-", "m3u-")):
        if item in synced():
            return next(iter(m3u_tracks(synced()[item])[1]), None)
        first = one("SELECT track_id FROM sub_playlist_tracks WHERE playlist_id = ? ORDER BY pos LIMIT 1", item[3:] if item[3:].isdigit() else -1)
        return first and one("SELECT * FROM tracks WHERE id = ?", first["track_id"])
    return None


def cover_data(t):
    """A cover.jpg/folder.jpg beside the song, else the art inside the file: (bytes, where it came from) or None."""
    file = library.root() / t["path"]
    try:
        beside = {f.name.lower(): f for f in file.parent.iterdir()}
    except OSError:
        beside = {}
    for name in ("cover", "folder", "front", "albumart", "album"):
        for ext in (".jpg", ".jpeg", ".png"):
            if name + ext in beside:
                try:
                    return beside[name + ext].read_bytes(), str(beside[name + ext])
                except OSError:
                    pass
    try:
        return music.library_cover(t["path"]).body, str(file)
    except HTTPException:
        return None


def resized(data, source, size):
    """JPEG of at most size x size, kept under DATA/covers so the next ask is a file read."""
    cache = core.DATA / "covers" / (hashlib.sha1(f"{source}|{os.path.getmtime(source)}|{size}".encode()).hexdigest() + ".jpg")
    if cache.is_file():
        return cache.read_bytes()
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data))
        im.thumbnail((size, size))
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=85)
    except Exception:  # no Pillow, or art it can't read: the original is still a cover
        return data
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(out.getvalue())
    return out.getvalue()


@api("getCoverArt")
def cover_art(c):
    t = cover_track(c.p.need("id"))
    got = t and cover_data(t)
    if not got:
        raise Fail(70, "No cover art")
    data, source = got
    size = c.p.int("size", 0, 0, 1200)
    if size:
        data = resized(data, source, max(size, 16))
    kind = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return Response(data, media_type=kind, headers={"Cache-Control": "max-age=86400"})


# --- lyrics ---------------------------------------------------------------------------------------------------------

STAMP = re.compile(r"((?:\[\d+:\d+(?:\.\d+)?\])+)(.*)")


def parse_lrc(raw):
    """([{start (ms), value}], True) for a synced .lrc, or ([{value}, ...], False) for plain text."""
    synced_ = []
    for line in raw.splitlines():
        m = STAMP.fullmatch(line.strip())
        for mm, ss in re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", m[1]) if m else []:
            synced_.append({"start": int(round((int(mm) * 60 + float(ss)) * 1000)), "value": m[2].strip()})
    if synced_:
        return sorted(synced_, key=lambda x: x["start"]), True
    return [{"value": x} for x in raw.splitlines()], False


def lyrics_of(t):
    path = library.root() / t["path"]
    raw = ""
    lrc = path.with_suffix(".lrc")
    if lrc.is_file():
        raw = lrc.read_text("utf-8", "replace")[:1_000_000]
    else:
        try:
            tags = library.AudioFile(path).tags or {}
            frames = tags.getall("USLT") if hasattr(tags, "getall") else []
            raw = frames[0].text if frames else next(
                (str(v[0]) for k in ("lyrics", "unsyncedlyrics", "\xa9lyr") if (v := tags.get(k))), "")
        except Exception:
            raw = ""
    return parse_lrc(raw.strip()) if raw.strip() else None


@api("getLyricsBySongId")
def lyrics_by_id(c):
    t = track(c.p.need("id"))
    got = lyrics_of(t)
    return {"lyricsList": {"structuredLyrics": [{"displayArtist": t["artist"], "displayTitle": t["title"], "lang": "xxx",
                                                  "offset": 0, "synced": got[1], "line": got[0]}] if got else []}}


@api("getLyrics")
def get_lyrics(c):
    where, args = "WHERE t.title = ? COLLATE NOCASE", [c.p.need("title")]
    if c.p.get("artist"):
        where += " AND (t.artist = ? COLLATE NOCASE OR t.album_artist = ? COLLATE NOCASE)"
        args += [c.p.get("artist")] * 2
    t = one(f"SELECT t.* FROM tracks t {where} LIMIT 1", *args)
    got = t and lyrics_of(t)
    return {"lyrics": {"artist": t["artist"], "title": t["title"], "value": "\n".join(x["value"] for x in got[0])} if got else {}}


# --- users and scanning ---------------------------------------------------------------------------------------------

def user_dict(u):
    admin = bool(u["admin"])
    return {"username": u["name"], "email": "", "scrobblingEnabled": True, "adminRole": admin, "settingsRole": admin,
            "downloadRole": True, "uploadRole": False, "playlistRole": True, "coverArtRole": True, "commentRole": False,
            "podcastRole": False, "streamRole": True, "jukeboxRole": False, "shareRole": False, "videoConversionRole": False,
            "folder": [1]}


@api("getUser")
def get_user(c):
    name = c.p.get("username") or c.u["name"]
    if name.casefold() != c.u["name"].casefold():
        c.admin()
    u = one("SELECT name, admin FROM users WHERE name = ?", name)
    if not u:
        raise Fail(70, "User not found")
    return {"user": user_dict(u)}


@api("getUsers")
def get_users(c):
    c.admin()
    return {"users": {"user": [user_dict(u) for u in rows("SELECT name, admin FROM users ORDER BY name")]}}


def scan_status():
    return {"scanStatus": {"scanning": library.status["scanning"], "count": one("SELECT COUNT(*) AS n FROM tracks")["n"]}}


@api("getScanStatus")
def get_scan_status(c):
    return scan_status()


@api("startScan")
def start_scan(c):
    c.admin()
    library.scan_in_background()
    return scan_status()
