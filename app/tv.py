"""TVs. Parlor on a TV pairs with an account by a short code entered on a signed-in phone, so nobody types a password
on a TV; the TV then holds a session of its own. Phones and computers of that account cast to it and drive it as a
remote: messages go down as server-sent events and up as POSTs, authenticated like everything else by the session
cookie, so they pass through the Android app and any reverse proxy unchanged. Pairings are rows tied to the TV's
session, so signing out everywhere, deleting the account or unpairing ends them. The live connections are held in this
one process (Parlor runs a single worker)."""
import asyncio
import itertools
import json
import re
import secrets
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Optional

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

import auth
import core
import pwa
from core import _digest as _hash, db, me, one, rows  # the same digest as sessions.token, which tvs.session points at

router = APIRouter(prefix="/api/tv")
page = APIRouter()  # /tv: the same page, which starts in TV mode there
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O, no 1/I/L: 8 of them is about 40 bits
CODE_LIFE = 300  # seconds a code lasts
LIMITS = {"pair": 5, "code": 20, "claim": 120, "hw": 90, "hwtest": 10}  # tries a minute per address (hw: a TV set's buttons, tvhw.py)
PING = 15  # seconds between keep-alives on an event stream (and between checks that its session still stands)
_tries: dict = defaultdict(list)
_now = time.time  # the clock (tests move it)


def _throttle(request: Request, kind: str):
    now, key = _now(), (kind, request.client.host if request.client else "?")
    if len(_tries) > 10000:  # bounded: forget everything stale
        for k in [k for k, ts in _tries.items() if not ts or now - ts[-1] >= 60]:
            del _tries[k]
    _tries[key] = [t for t in _tries[key] if now - t < 60] + [now]
    if len(_tries[key]) > LIMITS[kind]:
        raise HTTPException(429, "Too many tries: wait a minute")


@page.get("/tv", include_in_schema=False)
def tv_page():
    return FileResponse(Path(__file__).parent / "static" / "index.html", headers={"Cache-Control": "no-cache"})


# --- pairing ----------------------------------------------------------------------

@router.post("/code")
def new_code(request: Request):
    """The TV's pairing screen: a code to show, and a secret only the TV has, to claim the pairing with."""
    _throttle(request, "code")
    code, claim, now = "".join(secrets.choice(ALPHABET) for _ in range(8)), secrets.token_urlsafe(32), _now()
    db().execute("DELETE FROM tv_codes WHERE created < ?", (now - CODE_LIFE,))
    db().execute("INSERT INTO tv_codes(code, claim, created) VALUES (?, ?, ?)", (_hash(code), _hash(claim), now))
    return {"code": code, "claim": claim, "expires": CODE_LIFE}


class Pair(BaseModel):
    code: str = Field(max_length=20)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


@router.post("/pair")
def pair(body: Pair, request: Request, user=Depends(me)):
    """A signed-in phone enters the TV's code: the TV may now claim a session of this account."""
    _throttle(request, "pair")
    code = _hash("".join(c for c in body.code.upper() if c in ALPHABET))
    cur = db().execute("UPDATE tv_codes SET user_id = ?, name = ? WHERE code = ? AND user_id IS NULL AND created >= ?",
                       (user["id"], body.name, code, _now() - CODE_LIFE))
    if cur.rowcount != 1:
        raise HTTPException(404, "That code isn't right, or it has run out: the TV shows a fresh one")
    return {"ok": True, "name": body.name}


class Claim(BaseModel):
    claim: str = Field(max_length=100)


@router.post("/claim")
def claim(body: Claim, request: Request, response: Response):
    """The TV asks every couple of seconds: once its code was entered, it gets its session (a cookie, like a sign-in)."""
    _throttle(request, "claim")
    row = one("SELECT * FROM tv_codes WHERE claim = ? AND created >= ?", _hash(body.claim), _now() - CODE_LIFE)
    if not row:
        raise HTTPException(404, "This code has run out")
    if not row["user_id"]:
        return {"paired": False}
    if db().execute("DELETE FROM tv_codes WHERE code = ?", (row["code"],)).rowcount != 1:
        raise HTTPException(404, "This code has run out")  # claimed a moment ago: once only
    token = core.new_session(row["user_id"], f"TV: {row['name']}")
    db().execute("INSERT INTO tvs(user_id, name, session, created) VALUES (?, ?, ?, ?)", (row["user_id"], row["name"], _hash(token), _now()))
    core.set_cookie(response, request, token)
    return {"paired": True, "name": row["name"]}


def tv_of(token: str) -> Optional[dict]:
    """the TV this session belongs to, if it's a TV's, with who it acts as now (viewer: None while that is its owner)"""
    t = one("""SELECT t.id, t.user_id, t.name, v.id AS vid, v.name AS vname FROM tvs t
               LEFT JOIN users v ON v.id = t.viewer_id AND v.household = 1 WHERE t.session = ?""", _hash(token)) if token else None
    return t and {"id": t["id"], "user_id": t["user_id"], "name": t["name"], "viewer": {"id": t["vid"], "name": t["vname"]} if t["vid"] else None}


@lru_cache(1)
def version() -> str:
    """the page's version (the service worker's): a TV left on reloads itself when a newer Parlor answers"""
    return re.search(r'"([0-9a-f]{12})"', pwa.worker())[1]


@router.get("/me")
def whoami(request: Request):
    return {"tv": tv_of(core.session_cookie(request)), "version": version()}


def household(user_id: int) -> bool:
    """an admin made this account part of the household"""
    return bool(one("SELECT 1 AS x FROM users WHERE id = ? AND household = 1", user_id))


def _viewer(tid: int) -> Optional[int]:
    """who the TV acts as (a household account), None while it is its owner"""
    v = one("SELECT v.id FROM tvs t JOIN users v ON v.id = t.viewer_id AND v.household = 1 WHERE t.id = ?", tid)
    return v and v["id"]


def audience(tid: int) -> set:
    """the accounts that hear a TV and may drive it: its owner and the household (the viewer is always one of them)"""
    t = one("SELECT user_id FROM tvs WHERE id = ?", tid)
    return {r["id"] for r in rows("SELECT id FROM users WHERE household = 1 OR id = ?", t["user_id"] if t else 0)}


@router.get("")
def my_tvs(everyone: bool = False, user=Depends(me)):
    """this account's TVs, or all of them for a household account; an admin asks for everyone's (Settings → TVs)"""
    every = everyone and user["admin"] or household(user["id"])
    found = rows("""SELECT t.id, t.name, t.user_id, t.created, u.name AS owner, v.id AS vid, v.name AS vname FROM tvs t
                    JOIN users u ON u.id = t.user_id LEFT JOIN users v ON v.id = t.viewer_id AND v.household = 1"""
                 + ("" if every else " WHERE t.user_id = ?") + " ORDER BY t.created", *(() if every else (user["id"],)))
    for t in found:
        t.update(online=hub.online(t["id"]), state=hub.state.get(t["id"]), viewer={"id": t.pop("vid"), "name": t.pop("vname")} if t["vid"] else None)
    return {"tvs": found}


def _mine(tid: int, user: dict, everyone: bool = False, owner: bool = False) -> dict:
    """a TV this account may drive: its owner's, its viewer's or a household account's; anyone else's only for an admin asking
    from Settings → TVs (everyone). owner: only its owner (or that admin), for what a household account may not do"""
    t = one("SELECT id, user_id, name, session FROM tvs WHERE id = ?", tid)
    if not t or not (t["user_id"] == user["id"] or (everyone and user["admin"]) or (not owner and household(user["id"]))):
        raise HTTPException(404, "No such TV")
    return t


@router.delete("/{tid}")
async def unpair(tid: int, everyone: bool = False, user=Depends(me)):
    t = await anyio.to_thread.run_sync(_mine, tid, user, everyone, True)
    people = await anyio.to_thread.run_sync(audience, tid)
    await anyio.to_thread.run_sync(lambda: db().execute("DELETE FROM sessions WHERE token = ?", (t["session"],)))  # the TV row goes with it
    hub.close_tv(tid)
    hub.tell(people, {"type": "gone", "tv": tid})
    return {"ok": True}


# --- who the TV is for ----------------------------------------------------------------------

@router.get("/{tid}/people")
def people(tid: int, everyone: bool = False, user=Depends(me)):
    """who this TV can act as: its owner and the household, and whether each has a PIN (the TV's own picker and a remote's)"""
    t = _mine(tid, user, everyone)
    found = rows("SELECT id, name, pin IS NOT NULL AS pin, id = ? AS owner FROM users WHERE household = 1 OR id = ? ORDER BY owner DESC, name",
                 t["user_id"], t["user_id"])
    return {"people": [{**p, "pin": bool(p["pin"]), "owner": bool(p["owner"])} for p in found], "viewer": _viewer(tid)}


class Pick(BaseModel):
    user_id: Optional[int] = None  # null (or the owner): back to the owner
    pin: Optional[str] = Field(None, max_length=20)


def _pick(t: dict, body: Pick, user: dict, by_tv: bool, address: str) -> Optional[dict]:
    """switch the TV to the person picked, after their PIN if they have one (the PIN of the person it is already acting as, or
    of the one asking from their own phone, isn't asked); who it acts as now, None for the owner"""
    owner = t["user_id"]
    target = body.user_id if body.user_id not in (None, owner) else owner
    row = one("SELECT id, name, pin FROM users WHERE id = ? AND (household = 1 OR id = ?)", target, owner)
    if not row:
        raise HTTPException(404, "They aren't part of the household")
    if row["pin"] and target != (_viewer(t["id"]) or owner) and (by_tv or user["id"] != target):
        keys = (f"pin:{target}", f"pinip:{address}")  # wrong tries count against the person and against the address
        if auth._throttled(*keys):
            raise HTTPException(429, "Too many wrong PINs. Wait a few minutes and try again.")
        if not body.pin:
            raise HTTPException(403, f"{row['name']} has a PIN: enter it")
        if not core.check_password(body.pin, row["pin"]):
            auth._failed(*keys)
            raise HTTPException(403, "That PIN isn't right")
    db().execute("UPDATE tvs SET viewer_id = ? WHERE id = ?", (None if target == owner else target, t["id"]))
    return None if target == owner else {"id": row["id"], "name": row["name"]}


@router.post("/{tid}/viewer")
async def pick_viewer(tid: int, body: Pick, request: Request, everyone: bool = False, user=Depends(me)):
    """The TV acts as the person picked from now on: its owner, or a household account (with their PIN, if they set one).
    The owner, the viewer and the household may pick, from a phone or the TV's own screen."""
    token, address = core.session_cookie(request), request.client.host if request.client else "?"
    t = await anyio.to_thread.run_sync(_mine, tid, user, everyone)
    viewer = await anyio.to_thread.run_sync(lambda: _pick(t, body, user, bool(tv_of(token)), address))
    msg = {"type": "viewer", "tv": tid, "viewer": viewer}
    for q in hub.tvs.get(tid, ()):  # the TV reloads what it shows for the new person
        _put(q, msg)
    hub.tell(await anyio.to_thread.run_sync(audience, tid), msg)
    return {"ok": True, "viewer": viewer}


# --- the live part: casting and the remote --------------------------------------------

class Hub:
    """Who's listening: each TV's event streams, and each account's remotes (admins may listen to every TV)."""

    def __init__(self):
        self.tvs: dict = defaultdict(set)  # tv id -> queues
        self.remotes: dict = defaultdict(set)  # user id -> queues
        self.everyone: set = set()  # admins' queues from Settings → TVs
        self.state: dict = {}  # tv id -> what it last said it's doing
        self.seen: dict = {}  # tv id -> when it last said anything (it does every few seconds)
        self.sent: dict = defaultdict(dict)  # tv id -> {a command's number: when it was passed on}, until the TV answers it
        self.number = itertools.count(1)

    def online(self, tid: int) -> bool:
        """listening, and heard from lately: a TV switched off at the wall leaves its connection open for minutes"""
        return bool(self.tvs.get(tid)) and _now() - self.seen.get(tid, 0) < 3 * PING

    def tell(self, users, msg: dict):
        """to the remotes of these accounts (a TV's audience) and Settings → TVs"""
        for q in [*(q for u in users for q in self.remotes.get(u, ())), *self.everyone]:
            _put(q, msg)

    def close_tv(self, tid: int):
        for q in self.tvs.pop(tid, ()):
            _put(q, None)
        self.state.pop(tid, None)
        self.seen.pop(tid, None)
        self.sent.pop(tid, None)


def _put(q: asyncio.Queue, msg):
    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:  # a listener that stopped reading: it misses this one
        pass


hub = Hub()


@router.get("/events")
async def events(request: Request, everyone: bool = False, user=Depends(me)):
    """The event stream: a TV hears commands; a remote hears its TVs come and go and what they're playing."""
    token = core.session_cookie(request)
    if not token:
        raise HTTPException(403, "Event streams are for signed-in pages, not keys")
    tv = await anyio.to_thread.run_sync(tv_of, token)
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    if tv:
        hub.tvs[tv["id"]].add(q)
        hub.seen[tv["id"]] = _now()
        hub.tell(await anyio.to_thread.run_sync(audience, tv["id"]), {"type": "online", "tv": tv["id"], "name": tv["name"]})
    else:
        (hub.everyone.add(q) if everyone and user["admin"] else hub.remotes[user["id"]].add(q))
        _put(q, {"type": "tvs", "tvs": (await anyio.to_thread.run_sync(lambda: my_tvs(everyone, user)))["tvs"]})

    async def stream():
        try:
            yield "retry: 2000\n\n"  # a dropped stream comes back by itself, two seconds later
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), PING)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return  # gone: say nothing more
                    now = await anyio.to_thread.run_sync(core.session_user, token)
                    if not now or (q in hub.everyone and not now["admin"]):
                        return  # signed out (everywhere, or unpaired), or no longer an admin, since: stop at once
                    if tv and not hub.online(tv["id"]):
                        return  # the TV went quiet (switched off, say): its remotes hear it's off
                    yield ": still here\n\n"
                    continue
                if msg is None:
                    return
                yield f"data: {json.dumps(msg)}\n\n"
        finally:
            if tv:
                hub.tvs[tv["id"]].discard(q)
                if not hub.tvs[tv["id"]]:
                    hub.tvs.pop(tv["id"], None)
                    hub.tell(audience(tv["id"]), {"type": "offline", "tv": tv["id"]})  # not awaited: this runs while a closed stream is cancelled
            else:
                hub.remotes[user["id"]].discard(q)
                hub.everyone.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})  # a proxy passes each event on as it comes: no compressing, no buffering


class Cast(BaseModel):
    kind: Literal["jf", "yt", "queue"]  # a Jellyfin film or episode, a YouTube video, a queue of songs (an album or a song is a queue too)
    id: str = Field("", max_length=200)
    at: float = Field(0, ge=0, le=10**7)  # start here (seconds)
    items: list = Field(default_factory=list, max_length=500)  # for a queue: dock items (ids and titles only)


class Do(BaseModel):
    cmd: Literal["key", "toggle", "play", "pause", "seek", "skip", "volume", "type", "cast", "stop"]
    key: Optional[Literal["up", "down", "left", "right", "enter", "back", "home"]] = None
    by: Optional[float] = Field(None, ge=-36000, le=36000)  # seek by seconds; volume by a step
    to: Optional[float] = Field(None, ge=0, le=10**7)  # seek to seconds; volume to 0..1
    text: Optional[str] = Field(None, max_length=200)
    cast: Optional[Cast] = None


@router.post("/{tid}/do")
async def do(tid: int, body: Do, everyone: bool = False, user=Depends(me)):
    """A remote's command, or a cast: on to the TV, in order. A TV that isn't listening says so. Each gets a number;
    the TV answers with it in what it says next, which times the whole way there and back (state, below)."""
    t = await anyio.to_thread.run_sync(_mine, tid, user, everyone)
    if body.cmd == "cast" and not body.cast:
        raise HTTPException(422, "What to cast?")
    listening = hub.tvs.get(tid)
    if not listening or not hub.online(tid):
        raise HTTPException(409, f"{t['name']} is off or not showing Parlor")
    n, waiting = next(hub.number), hub.sent[tid]
    waiting[n] = time.monotonic()
    for old in list(waiting)[:-50]:  # a TV that never answers: only the last few are kept
        del waiting[old]
    msg = {"type": "do", **body.model_dump(exclude_none=True), "from": user["name"], "n": n}
    for q in listening:
        _put(q, msg)
    return {"ok": True, "n": n}


class State(BaseModel):
    state: dict


@router.post("/state")
async def state(body: State, request: Request, user=Depends(me)):
    """The TV says what it's doing (title, playing or not, position, volume, page): every remote of its account hears it.
    With the number of the command it just acted on (`ack`), the server adds `lag`: milliseconds from that command
    arriving here to this answer, the TV's own drawing included (it answers after the frame that shows the change)."""
    tv = await anyio.to_thread.run_sync(tv_of, core.session_cookie(request))
    if not tv:
        raise HTTPException(403, "Only a paired TV says what it's playing")
    if len(json.dumps(body.state)) > 4000:
        raise HTTPException(413, "Too much state")
    sent = hub.sent[tv["id"]].pop(body.state.get("ack"), None) if type(body.state.get("ack")) is int else None
    hub.state[tv["id"]] = {**body.state, "seen": _now(), **({"lag": round((time.monotonic() - sent) * 1000)} if sent else {})}  # "at" is where it is in what's playing
    hub.seen[tv["id"]] = _now()
    hub.tell(await anyio.to_thread.run_sync(audience, tv["id"]), {"type": "state", "tv": tv["id"], "state": hub.state[tv["id"]]})
    return {"ok": True}
