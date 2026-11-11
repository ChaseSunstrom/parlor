"""Games you ask for, inside Parlor (Request → Games). Browsing and the title search are answered from Steam's public
store (gamelookup.py: no keys, a game's id is its Steam app id). A request is a row here and a request in Questarr
(its Seerr-style API, always by that id: by title Questarr takes the first hit, and "portal" finds another game), made
with the login Parlor has in Questarr. A worker follows each one: searching, pick a release, downloading, downloaded.

A game is a program somebody will run, so:
- a member's request waits for an admin's yes;
- a release is taken with nobody choosing only when every check in sure() passes; anything else waits for an admin to
  pick from the list Questarr accepted;
- only a release from that list can be picked: its link stays on the server (it carries Prowlarr's key).
Questarr's own automatic download stays off: it takes a release only when exactly one survives its filters."""
import json
import logging
import re
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import gamelookup
import stack
from core import admin, db, me, one, rows, set_setting, setting

log = logging.getLogger("parlor.gamereq")
router = APIRouter(prefix="/api/games/requests")
API = "/api/integration/seerrng/v1/requests"
VARIANT = {"operatingSystem": "windows", "architecture": "x64"}  # ponytail: Windows releases only; a choice per request if Linux builds are ever wanted
EVERY, LOOK_AGAIN, MAX_ERRORS, MIN_SEEDERS, SHOWN, SETTLE = 300, 6 * 3600, 12, 5, 30, 1800
MIN_AGE = 3 * 86400  # a release posted in the last days is where the fakes are: those wait for an admin
OPEN = ("searching", "choose", "downloading")
LABEL = {"pending": "Waiting for an admin", "searching": "Searching", "choose": "Pick a release", "downloading": "Downloading",
         "available": "Downloaded", "failed": "Stuck", "declined": "Declined"}
KNOWN = {"scene_release", "repack"}  # Questarr's own scoring lines: a scene group's release, a known repacker's
NOT_READY = "Questarr isn't set up with a login of Parlor's yet: an admin runs Settings → Services → Set up again"
_tick = threading.Lock()  # one check at a time; the pages never wait for it
_token = {"value": ""}  # Questarr's session for Parlor's login (it lasts a week there)
wake = threading.Event()


# --- Questarr, signed in as Parlor ------------------------------------------------------------

def ready() -> bool:
    return bool(stack._questarr_own())


def quest(method, path, timeout=60, **kw):
    """Questarr's answer, signed in with the login Parlor made there: its request, search and download routes take a
    session, not the key setup uses."""
    own = stack._questarr_own()
    if not own:
        raise HTTPException(409, NOT_READY)
    base = stack.questarr_url()
    try:
        for _ in range(2):
            if not _token["value"]:
                r = requests.request("POST", f"{base}/api/auth/login", json={"username": stack.QUESTARR_USER, "password": own}, timeout=30)
                if not r.ok:
                    raise HTTPException(502, f"Questarr didn't take Parlor's login ({r.status_code})")
                _token["value"] = r.json()["token"]
            r = requests.request(method, base + path, headers={"Authorization": f"Bearer {_token['value']}"}, timeout=timeout, **kw)
            if r.status_code != 401:
                return r
            _token["value"] = ""  # its week is up, or Questarr started over: sign in once more
    except (requests.RequestException, ValueError, KeyError):
        raise HTTPException(502, "Questarr can't be reached")
    raise HTTPException(502, "Questarr refused Parlor's session")


def clean(text) -> str:
    """Questarr's words without the links in them: a release's link carries Prowlarr's key."""
    return re.sub(r"\(?(?:https?://|magnet:)\S+", "(a link)", str(text or ""))[:200]


def said(r) -> dict:
    """Questarr's answer, or what it said went wrong."""
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not r.ok:
        raise HTTPException(502, clean((body or {}).get("error") or f"Questarr said {r.status_code}"))
    return body


# --- the catalogue: Steam's store, through gamelookup --------------------------------------------

def asked() -> dict:
    return {r["app"]: r["state"] for r in rows("SELECT app, state FROM game_requests")}


def card(g: dict, have: dict) -> dict:
    when = g.get("first_release_date") or 0
    return {"id": g["id"], "title": g["name"], "poster": "https:" + g["cover"]["url"] if g.get("cover") else None,
            "year": time.gmtime(when).tm_year if when else None, "soon": when > gamelookup._now(), "rating": g.get("rating"), "status": have.get(g["id"])}


def cards(q: dict) -> list:
    """Games only (no add-ons), and none of Steam's adult-only ones: this is a family's server."""
    have = asked()
    return [card(g, have) for g in gamelookup.games(q) if g["game_type"] == 0 and not any(t["id"] == gamelookup.EROTIC for t in g["themes"])]  # found() says no to the same


def found(app: int) -> dict:
    g = next(iter(gamelookup.games({"where": f"id = {int(app)}"})), None) if 0 < app < 10 ** 10 else None
    if not g or g["game_type"] != 0 or any(t["id"] == gamelookup.EROTIC for t in g["themes"]):
        raise HTTPException(404, "No such game in Steam's store")
    return g


@router.get("/status")
def status(_=Depends(me)):
    return {"ready": ready(), "auto": auto()}


@router.get("/discover")
def discover(_=Depends(me)):
    now, day = int(gamelookup._now()), 86400
    return {"popular": cards({"limit": "24"}),
            "new": cards({"where": f"first_release_date >= {now - 60 * day} & first_release_date <= {now}", "limit": "24", "sort": "first_release_date desc"}),
            "upcoming": cards({"where": f"first_release_date >= {now}", "limit": "24", "sort": "first_release_date asc"})}


@router.get("/search")
def search(q: str = "", _=Depends(me)):
    q = " ".join(q.replace('"', " ").split())[:100]
    return {"items": cards({"search": f'"{q}"', "limit": "40"}) if q else []}


@router.get("/game/{app}")
def game(app: int, user=Depends(me)):
    g = found(app)
    r = one(ROW + " WHERE g.app = ?", app)
    people = lambda role: [c["company"]["name"] for c in g["involved_companies"] if c[role]]  # noqa: E731
    return {**card(g, {app: r["state"]} if r else {}), "summary": g["summary"], "screenshots": ["https:" + s["url"] for s in g["screenshots"]],
            "genres": [t["name"] for field in ("genres", "themes") for t in g[field]], "modes": [t["name"] for t in g["game_modes"]],
            "developers": people("developer"), "publishers": people("publisher"), "platforms": [p["name"] for p in g["platforms"]],
            "released": g.get("first_release_date"), "reviews": g.get("rating_count"), "early": g.get("status") == 4,
            "store": g["websites"][0]["url"], "ready": ready(), "request": view(r, user) if r else None}


# --- asking, and the list ------------------------------------------------------------------------

ROW = "SELECT g.*, u.name AS asker FROM game_requests g LEFT JOIN users u ON u.id = g.user_id"


def view(r: dict, user: dict) -> dict:
    picks = json.loads(r["picks"] or "[]") if user["admin"] and r["state"] == "choose" else []
    mine = r["user_id"] == user["id"]
    theirs = mine or user["admin"]  # anyone else learns that it's asked for and where it's at, not by whom or why
    return {"id": r["id"], "app": r["app"], "title": r["title"], "image": r["image"], "year": r["year"], "state": r["state"], "label": LABEL.get(r["state"], r["state"]),
            "why": r["why"] if theirs else "", "by": r.get("asker") if theirs else None, "mine": mine, "created": r["created"], "updated": r["updated"],
            "release": r["release"] if theirs else None,
            "picks": [{k: v for k, v in p.items() if k != "link"} for p in picks]}  # never the link: it carries Prowlarr's key


def set_(rid, **values):
    values.setdefault("updated", time.time())
    if values.get("why"):
        values["why"] = clean(values["why"])
    db().execute(f"UPDATE game_requests SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?", (*values.values(), rid))


def moved(rid, was: tuple, **values) -> bool:
    """Move a request on, only if it is still where the page saw it: two taps, or a tap and the worker, act once."""
    values.setdefault("updated", time.time())
    if values.get("why"):
        values["why"] = clean(values["why"])
    cur = db().execute(f"UPDATE game_requests SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ? AND state IN ({','.join('?' * len(was))})",
                       (*values.values(), rid, *was))
    return cur.rowcount == 1


def row(rid, user, anyone=False) -> dict:
    r = one(ROW + " WHERE g.id = ?", rid)
    if not r or not (anyone or user["admin"] or r["user_id"] == user["id"]):
        raise HTTPException(404, "No such request")
    return r


def fresh(rid, user) -> dict:
    wake.set()
    return view(row(rid, user, anyone=True), user)


@router.get("")
def listing(user=Depends(me)):
    q = ROW + ("" if user["admin"] else " WHERE g.user_id = ?") + " ORDER BY g.updated DESC LIMIT 300"
    return {"items": [view(r, user) for r in rows(q, *(() if user["admin"] else (user["id"],)))]}


@router.post("/game/{app}")
def ask(app: int, user=Depends(me)):
    if not ready():
        raise HTTPException(409, NOT_READY)
    g = found(app)
    had = one("SELECT id, state, ext FROM game_requests WHERE app = ?", app)
    if had and had["state"] not in ("failed", "declined"):
        return fresh(had["id"], user)  # one request per game: whoever asked first
    if had:
        forget(had)
    c, now = card(g, {}), time.time()
    cur = db().execute("INSERT OR IGNORE INTO game_requests(user_id, app, title, image, year, state, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (user["id"], app, g["name"][:500], c["poster"], c["year"], "searching" if user["admin"] else "pending", now, now))
    rid = cur.lastrowid if cur.rowcount else one("SELECT id FROM game_requests WHERE app = ?", app)["id"]  # two people at once: the first one's
    return fresh(rid, user)


def forget(r: dict):
    """Out of Questarr (when it got that far) and out of the list."""
    if r["ext"]:
        quest("POST", f"{API}/{r['ext']}/cancel", json={})  # already cancelled or gone there: the same to us. Unreachable: it stays listed
    db().execute("DELETE FROM game_requests WHERE id = ?", (r["id"],))


@router.post("/{rid}/approve")
def approve(rid: int, user=Depends(admin)):
    if not moved(rid, ("pending",), state="searching", why="", next_at=0):
        raise HTTPException(409, "It isn't waiting for a yes")
    return fresh(rid, user)


@router.post("/{rid}/decline")
def decline(rid: int, user=Depends(admin)):
    if not moved(rid, ("pending",), state="declined", why=f"{user['name']} said no"):
        raise HTTPException(409, "It isn't waiting for a yes")
    return fresh(rid, user)


@router.post("/{rid}/retry")
def retry(rid: int, user=Depends(me)):
    """Search again: from the top for one that's stuck, a fresh look at the indexers for one that's searching or waiting for a pick."""
    r = row(rid, user)
    if not user["admin"] and time.time() - r["updated"] < EVERY:  # each search asks every indexer, and they ban the eager
        raise HTTPException(429, "It was looked for a moment ago: try again in a few minutes")
    if r["state"] == "failed":
        if r["ext"]:
            quest("POST", f"{API}/{r['ext']}/cancel", json={})  # Questarr won't retry every kind of failure; a new request it always takes
        if not moved(rid, ("failed",), state="searching", why="", ext=None, game=None, picks=None, release=None, tries=0, next_at=0):
            raise HTTPException(409, "It's already moving")
    elif not moved(rid, ("searching", "choose"), state="searching", why="", picks=None, tries=0, next_at=0):
        raise HTTPException(409, "There's nothing to search for again")
    return fresh(rid, user)


@router.delete("/{rid}")
def cancel(rid: int, user=Depends(me)):
    forget(row(rid, user))
    return {"ok": True}


# --- which release ---------------------------------------------------------------------------------

def auto() -> bool:
    return setting("games_auto_pick", "1") != "0"


class Auto(BaseModel):
    on: bool


@router.put("/auto")
def set_auto(body: Auto, _=Depends(admin)):
    set_setting("games_auto_pick", "1" if body.on else "0")
    return {"auto": auto()}


def usable(i) -> bool:
    """What an admin may pick from: a torrent Questarr accepts for this game (right platform, title, score), that is the
    game itself (an update, an add-on or a crack alone is for Questarr's own page) and that it doesn't distrust."""
    ev = i.get("evaluation") or {}
    return bool(i.get("downloadType") == "torrent" and str(i.get("link") or "").startswith(("http://", "https://", "magnet:")) and i.get("title")
                and ev.get("accepted") is True and ev.get("titleMatch") in ("exact", "contains")
                and ev.get("category") in (None, "main") and i.get("aiReleaseType") not in ("update", "dlc", "crack_only")
                and (i.get("trust") or {}).get("level") != "risky" and not i.get("blacklisted"))


def sure(i) -> bool:
    """Every check at once, for taking a release with nobody looking: usable; the title is the game's exactly; Questarr
    reads it as the whole game (not an update, an add-on or a crack); it comes from a scene group or a known repacker,
    or is listed on xREL; enough people are seeding it; and it has been up for days (a fake is new, and gets reported).
    ponytail: names on public indexers can be faked; this narrows the odds, it doesn't scan the files. Checking the
    torrent's hash against a repacker's own site is the upgrade."""
    if not isinstance(i, dict) or not usable(i):
        return False
    ev = i["evaluation"]
    known = (i.get("trust") or {}).get("level") == "trusted" or any(line.get("ruleId") in KNOWN for line in ev.get("lines") or [])
    try:
        age = time.time() - parsedate_to_datetime(i.get("pubDate")).timestamp()
    except (TypeError, ValueError):
        return False
    return bool(ev.get("titleMatch") == "exact" and ev.get("category") == "main" and i.get("aiReleaseType") in ("full_game", "repack")
                and known and isinstance(i.get("seeders"), int) and i["seeders"] >= MIN_SEEDERS and age >= MIN_AGE)


def pick_of(i) -> dict:
    return {"title": str(i["title"])[:300], "size": i.get("size"), "seeders": i.get("seeders"), "indexer": i.get("indexerName"),
            "kind": i.get("aiReleaseType") or (i.get("evaluation") or {}).get("category"), "score": (i.get("evaluation") or {}).get("score"),
            "posted": i.get("pubDate"), "sure": sure(i), "link": i["link"]}


def grab(r: dict, p: dict, why: str):
    """Hand the release to Questarr's download client (qBittorrent, behind the VPN), tied to the game."""
    got = said(quest("POST", "/api/downloads", timeout=120, json={"url": p["link"], "title": p["title"], "gameId": r["game"], "downloadType": "torrent"}))
    if got.get("success") is False:
        raise HTTPException(502, clean(got.get("message") or got.get("error") or "The download client didn't take it"))
    set_(r["id"], state="downloading", release=p["title"], why=why, picks=None, next_at=time.time() + 60)


class Pick(BaseModel):
    pick: int  # its place in the list shown; never a link
    title: str  # and its name there: the list may have been made again since the page drew it


@router.post("/{rid}/pick")
def pick(rid: int, body: Pick, user=Depends(admin)):
    r = row(rid, user)
    picks = json.loads(r["picks"] or "[]")
    if r["state"] != "choose" or not 0 <= body.pick < len(picks) or picks[body.pick]["title"] != body.title or not moved(rid, ("choose",), state="downloading"):
        raise HTTPException(409, "The list has changed: look again and pick one of the releases shown")
    try:
        grab(r, picks[body.pick], f"{user['name']} picked it")
    except HTTPException as exc:
        set_(rid, state="choose", why=f"That one couldn't be started: {exc.detail}")
        raise
    return fresh(rid, user)


# --- the worker ------------------------------------------------------------------------------------

def send(r: dict, now: float):
    """Into Questarr, by the game's Steam id."""
    ext = f"parlor-{r['id']}-{int(now)}"  # a new one each time: Questarr keeps a cancelled request under its old name
    got = quest("POST", API, json={"externalRequestId": ext, "title": r["title"], "igdbId": r["app"], "variant": VARIANT})
    if got.status_code in (404, 409, 422):  # not a PC game it can find, or it already has a request for it that isn't Parlor's
        try:
            why = got.json().get("error")
        except ValueError:
            why = ""
        return set_(r["id"], state="failed", why=str(why or f"Questarr said {got.status_code}")[:200])
    d = said(got)
    set_(r["id"], ext=ext, game=(d.get("game") or {}).get("id"), why="", next_at=0 if d.get("game") else now + 30)  # no game yet: Questarr is still making it
    wake.set()  # the search for releases is the next check: now, not in five minutes


def look(r: dict, d: dict, now: float):
    """Search Questarr's indexers for the game and take the best sure release, or leave the list for an admin."""
    got = said(quest("GET", "/api/search", timeout=180, params={"query": r["title"][:200], "gameId": d["game"]["id"], "limit": 100}))
    items = [i for i in got.get("items") or [] if isinstance(i, dict) and usable(i)]
    items.sort(key=lambda i: (sure(i), i["evaluation"].get("score") or 0, i.get("seeders") or 0), reverse=True)
    here = ("searching",)  # the search took minutes: only a request that's still searching moves on (it may be cancelled, or asked for again)
    if items and sure(items[0]) and auto() and out(r["app"]):
        if moved(r["id"], here, state="downloading"):
            try:
                grab({**r, "game": d["game"]["id"]}, pick_of(items[0]), "It passed every check, so Parlor took it")
            except HTTPException:
                moved(r["id"], ("downloading",), state="searching")
                raise
        return
    if items:
        why = ("taking one unseen is switched off" if not auto() else "none passes every check for taking one unseen" if out(r["app"])
               else "the game isn't out yet, so none is taken unseen")
        return moved(r["id"], here, state="choose", picks=json.dumps([pick_of(i) for i in items[:SHOWN]]),
                     why=f"{len(items)} release{'s' if len(items) != 1 else ''} found; {why}. An admin picks one.")
    moved(r["id"], here, next_at=now + LOOK_AGAIN, why=f"Nothing usable on the indexers yet; looking again in {LOOK_AGAIN // 3600} hours")


def out(app: int) -> bool:
    """Is the game released? A release of one that isn't is a fake until an admin says otherwise. Steam not answering: not known to be."""
    try:
        return not card(found(app), {})["soon"]
    except HTTPException:
        return False


def advance(r: dict, now: float):
    if not r["ext"]:
        return send(r, now)
    got = quest("GET", f"{API}/{r['ext']}")
    if got.status_code == 404:
        return set_(r["id"], state="failed", why="Questarr no longer has this request")
    d = said(got)
    s, game = d.get("status"), (d.get("game") or {}).get("id")
    if game != r["game"] and game:  # Questarr took the request first and made its game after
        set_(r["id"], game=game, updated=r["updated"])
    if s == "available":
        set_(r["id"], state="available", why="", picks=None)
    elif s in ("downloading", "importing"):
        if r["state"] != "downloading":  # started on Questarr's own page
            set_(r["id"], state="downloading", why="", picks=None)
    elif s in ("failed", "cancelled"):
        set_(r["id"], state="failed", why=str(d.get("error") or "It was cancelled in Questarr")[:200], picks=None)
    elif r["state"] == "downloading":
        if now - r["updated"] > SETTLE:  # the download is gone from Questarr without a word
            set_(r["id"], state="searching", why="The download went away; searching again", next_at=0)
    elif r["state"] == "searching":
        if game:
            look(r, d, now)
        else:
            set_(r["id"], next_at=now + 30, updated=r["updated"])


def tick(now: Optional[float] = None):
    """Every open request that's due, one step each; Questarr down means later, with a longer wait each time."""
    now = now or time.time()
    if not ready():
        return
    with _tick:
        for rid in [r["id"] for r in rows(f"SELECT id FROM game_requests WHERE state IN ({','.join('?' * len(OPEN))}) AND next_at <= ? ORDER BY id", *OPEN, now)]:
            r = one("SELECT * FROM game_requests WHERE id = ?", rid)  # as it is now: the page may have just moved it
            if not r or r["state"] not in OPEN:
                continue
            try:
                if r["tries"]:  # the trouble's words go with the trouble; this step writes its own if it has any
                    db().execute("UPDATE game_requests SET tries = 0, why = '' WHERE id = ?", (rid,))
                advance(r, now)
            except Exception as exc:  # noqa: BLE001  (one stuck request never stops the others)
                tries, said_ = r["tries"] + 1, str(getattr(exc, "detail", exc))[:120]
                if tries >= MAX_ERRORS:
                    set_(rid, state="failed", tries=tries, why=f"Gave up after {tries} errors in a row: {said_}")
                    continue
                wait = min(EVERY * 2 ** tries, 6 * 3600)
                set_(rid, tries=tries, next_at=now + wait, updated=r["updated"], why=f"{said_}; trying again in {wait // 60} min")


def start():
    def loop():
        while True:
            try:
                tick()
            except Exception:  # noqa: BLE001
                log.exception("game requests: the check failed")
            wake.wait(EVERY)
            wake.clear()
    threading.Thread(target=loop, daemon=True, name="game-requests").start()
