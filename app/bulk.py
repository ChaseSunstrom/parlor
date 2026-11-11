"""Whole sets of films and shows through Seerr, asked for in one go: a film collection (every Avengers film), a
studio's films, a network's shows, or everything with a keyword, narrowed by years and rating.

Asking is two steps, both on the server. A plan looks the set up and answers with exactly which titles are missing
(Seerr's own status decides: there, partly there, requested and blocked ones are left out), at most CAP of them.
Confirming names that plan, so nothing the person didn't see can be requested. The requests are then made one at a
time by a worker here, each as the person's own Seerr user and under Seerr's rules, with every title's outcome
written to the database as it goes: closing the tab changes nothing, and a restart carries on with the next title."""

import json
import logging
import queue
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import acl
import seerr
from core import db, me, one, rows
from seerr import MEDIA, REQUEST, call, cards, img
from yt import Cache

log = logging.getLogger("parlor.bulk")
router = APIRouter(prefix="/api/seerr/sets")
CAP = 250  # titles one confirm may request; filters narrow a bigger set
PER_PAGE = 20  # Seerr's (TMDB's) page size
PAGES = CAP // PER_PAGE + 1  # how far a plan reads: enough to fill the cap and know there's more
PACE = 0.25  # seconds between requests, to go easy on Seerr and the apps behind it
KINDS = {"collection": ("movie",), "studio": ("movie",), "network": ("tv",), "keyword": ("movie", "tv")}
ACTIVE = ("queued", "running")
# well-known sets to start from; Seerr gives each its name and picture
FRANCHISES = (10, 1241, 119, 86311, 645, 328, 10194, 263, 87359, 9485, 295, 2344, 84, 264, 8091, 404609, 131635, 2150)
STUDIOS = (2, 3, 420, 1, 174, 33, 4, 34, 127928, 521, 10342, 41077)
NETWORKS = (213, 49, 2739, 1024, 2552, 453, 4, 16, 6, 2, 19, 174, 88, 67, 318, 4330, 3353, 56)

_pages = Cache(300, 400)  # a page of a set, five minutes: paging and planning read the same ones
_names = Cache(24 * 3600, 600)  # a set's name and picture
_home = Cache(3600, 4)
_plans = Cache(600, 200)  # plan id -> what was shown, until it's confirmed
_confirming = threading.Lock()


class Filters(BaseModel):
    media: str = "movie"  # a keyword has films and shows: which
    year_from: Optional[int] = Field(None, ge=1870, le=2200)
    year_to: Optional[int] = Field(None, ge=1870, le=2200)
    rating: float = Field(0, ge=0, le=10)  # at least this, out of ten
    released: bool = True  # leave out what isn't out yet


def _kind(kind: str, f: Filters) -> str:
    if kind not in KINDS:
        raise HTTPException(404, "A set is a collection, studio, network or keyword")
    if f.media not in ("movie", "tv"):
        raise HTTPException(422, "media is movie or tv")
    return f.media if kind == "keyword" else KINDS[kind][0]


def about(kind: str, sid: int) -> dict:
    """the set's name and picture"""
    hit = _names.get((kind, sid))
    if hit is None:
        d = call("GET", f"{kind}/{sid}")
        logo = d.get("logoPath")
        hit = _names.put((kind, sid), {
            "kind": kind, "id": sid, "name": d.get("name") or "", "overview": d.get("overview") or "",
            "poster": img(d.get("posterPath")), "backdrop": img(d.get("backdropPath"), "w1280"),
            # a logo in one colour, light for the dark theme and dark for the light one (TMDB recolours it)
            "logo": f"{seerr.IMG}/w300_filter(duotone,ffffff,bababa){logo}" if logo else None,
            "logo_dark": f"{seerr.IMG}/w300_filter(duotone,1c1a24,5e5b68){logo}" if logo else None})
    return hit


def _fits(c: dict, f: Filters, today: str) -> bool:
    year = int(c["year"]) if c.get("year") else None
    if f.released and (not c.get("date") or c["date"] > today):
        return False
    if f.year_from and (year is None or year < f.year_from) or f.year_to and (year is None or year > f.year_to):
        return False
    return (c.get("rating") or 0) >= f.rating


def _dated(results) -> list:
    dates = {(r.get("mediaType"), r.get("id")): r.get("releaseDate") or r.get("firstAirDate") or "" for r in results or []}
    out = cards(results)
    for c in out:
        c["date"] = dates.get((c["type"], c["id"]), "")
    return out


def page_of(kind: str, sid: int, f: Filters, page: int = 1) -> tuple:
    """(titles on this page, pages, titles in all) of a set, with the filters applied"""
    media, today = _kind(kind, f), time.strftime("%Y-%m-%d")
    if kind == "collection":  # one answer holds every film: filtered and sorted here
        parts = _pages.get(("collection", sid))
        if parts is None:
            d = call("GET", f"collection/{sid}")
            parts = _pages.put(("collection", sid), [{**p, "mediaType": "movie"} for p in d.get("parts") or []])
        found = sorted((c for c in _dated(parts) if _fits(c, f, today)), key=lambda c: c["date"] or "9999")
        return found, 1, len(found)
    dates = ("primaryReleaseDate", "firstAirDate")[media == "tv"]
    params = {"page": max(1, page), {"studio": "studio", "network": "network", "keyword": "keywords"}[kind]: sid}
    if f.year_from:
        params[dates + "Gte"] = f"{f.year_from}-01-01"
    upto = min(filter(None, [f"{f.year_to}-12-31" if f.year_to else "", today if f.released else ""]), default="")
    if upto:
        params[dates + "Lte"] = upto
    if f.rating:
        params["voteAverageGte"] = f.rating
    key = (media, tuple(sorted(params.items())))
    hit = _pages.get(key)
    if hit is None:
        d = call("GET", "discover/movies" if media == "movie" else "discover/tv", params=params)
        hit = _pages.put(key, (_dated([{**r, "mediaType": media} for r in d.get("results") or []]), d.get("totalPages") or 1, d.get("totalResults") or 0))
    return hit


def _forget_pages():
    with _pages.lock:
        _pages.data.clear()


@router.get("")
def home(_=Depends(me)):
    """Where to start: franchises (the collections of films you have first), studios and networks."""
    hit = _home.get("all")
    if hit is not None:
        return hit

    def named(kind, sid):
        try:
            return about(kind, sid)
        except HTTPException:
            return None

    def collection_of(tmdb):
        try:
            return (seerr.details("movie", tmdb).get("collection") or {}).get("id")
        except HTTPException:
            return None

    with ThreadPoolExecutor(8) as pool:
        try:
            have = [m["tmdbId"] for m in call("GET", "media", params={"take": 100, "filter": "available", "sort": "mediaAdded"}).get("results") or []
                    if m.get("mediaType") == "movie" and m.get("tmdbId")]
        except HTTPException:
            have = []
        owned: dict = {}
        for cid in pool.map(collection_of, have):
            if cid:
                owned[cid] = owned.get(cid, 0) + 1
        mine = sorted(owned, key=lambda c: -owned[c])[:24]
        franchises = [dict(a, have=owned.get(a["id"], 0)) for a in pool.map(lambda c: named("collection", c), mine + [c for c in FRANCHISES if c not in owned]) if a]
        studios = [a for a in pool.map(lambda s: named("studio", s), STUDIOS) if a]
        networks = [a for a in pool.map(lambda n: named("network", n), NETWORKS) if a]
    if not (franchises or studios or networks):
        call("GET", "status")  # nothing came back: say why
    return _home.put("all", {"franchises": franchises, "studios": studios, "networks": networks})


@router.get("/search")
def search(q: str, _=Depends(me)):
    """Studios and keywords by name (Seerr has no search for networks: those come from the list)."""
    q = q.strip()[:80]
    if not q:
        return {"studios": [], "keywords": []}
    with ThreadPoolExecutor(2) as pool:
        companies = pool.submit(lambda: call("GET", "search/company", params={"query": q}))
        words = call("GET", "search/keyword", params={"query": q})
    return {"studios": [{"kind": "studio", "id": c["id"], "name": c.get("name") or ""} for c in (companies.result() or {}).get("results") or []][:12],
            "keywords": [{"kind": "keyword", "id": k["id"], "name": k.get("name") or ""} for k in (words or {}).get("results") or []][:12]}


@router.get("/jobs")
def jobs(user=Depends(me)):
    return {"jobs": [_job(j) for j in rows("SELECT * FROM bulk_jobs WHERE user_id = ? ORDER BY created DESC LIMIT 20", user["id"])]}


@router.get("/{kind}/{sid}")
def show(kind: str, sid: int, page: int = 1, f: Filters = Depends(), user=Depends(me)):
    items, pages, total = page_of(kind, sid, f, max(1, min(page, 500)))
    return {**about(kind, sid), "media": _kind(kind, f), "items": items, "page": page, "pages": pages, "total": total, "can_request": seerr.can_request(user)}


# --- asking for all of it -------------------------------------------------------------------------------------------

LEFT_OUT = {"available": "there", "partial": "partly", "pending": "requested", "processing": "requested", "blocked": "blocked"}


class Plan(Filters):
    kind: str
    id: int


@router.post("/plan")
def plan(body: Plan, user=Depends(me)):
    """Which titles Request all would ask for: the missing ones, counted, with what's left out and why."""
    seerr.requester(user)  # no Seerr user: say so now, not title by title
    _forget_pages()  # a plan counts what's missing now, not what a page showed five minutes ago
    first, pages, total = page_of(body.kind, body.id, body, 1)
    more = range(2, min(pages, PAGES) + 1)
    with ThreadPoolExecutor(4) as pool:
        seen = first + [c for got in pool.map(lambda n: page_of(body.kind, body.id, body, n)[0], more) for c in got]
    missing, left = [], {}
    for c in {(c["type"], c["id"]): c for c in seen}.values():  # TMDB can repeat a title across pages
        why = LEFT_OUT.get(c["status"])
        if why:
            left[why] = left.get(why, 0) + 1
        else:
            missing.append({k: c[k] for k in ("type", "id", "title", "year", "poster")})
    titles = missing[:CAP]
    pid = secrets.token_urlsafe(18)
    _plans.put(pid, {"user": user["id"], "kind": body.kind, "set": body.id, "name": about(body.kind, body.id)["name"], "titles": titles})
    return {"plan": pid, "name": about(body.kind, body.id)["name"], "count": len(titles), "titles": titles, "left_out": left, "looked_at": len(seen),
            "total": total, "more": total > len(seen) or len(missing) > CAP, "cap": CAP}


class Confirm(BaseModel):
    plan: str = Field(max_length=64)


@router.post("/request")
def request_all(body: Confirm, user=Depends(me)):
    """Start requesting a plan's titles. The plan is used once and only by the person it was made for."""
    with _confirming:  # two presses at once: the second finds the plan gone
        p = _plans.get(body.plan)
        if not p or p["user"] != user["id"]:
            raise HTTPException(410, "That list is more than ten minutes old (or already sent). Look again, then confirm.")
        if not p["titles"]:
            raise HTTPException(422, "Nothing is missing from this set")
        if one(f"SELECT 1 AS x FROM bulk_jobs WHERE user_id = ? AND kind = ? AND set_id = ? AND status IN ({','.join('?' * len(ACTIVE))})",
               user["id"], p["kind"], p["set"], *ACTIVE):
            raise HTTPException(409, "This set is already being requested for you: see Downloads")
        _plans.drop(body.plan)
        jid = secrets.token_hex(8)
        titles = [{**t, "state": "waiting", "why": ""} for t in p["titles"]]
        db().execute("INSERT INTO bulk_jobs(id, user_id, kind, set_id, name, created, status, titles) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                     (jid, user["id"], p["kind"], p["set"], p["name"], time.time(), json.dumps(titles)))
    _pending.put(jid)
    return _job(one("SELECT * FROM bulk_jobs WHERE id = ?", jid))


def _mine(jid: str, user) -> dict:
    job = one("SELECT * FROM bulk_jobs WHERE id = ?", jid)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(404, "No such job")
    return job


@router.post("/jobs/{jid}/retry")
def retry(jid: str, user=Depends(me)):
    """Try again what failed or was left when the job stopped."""
    job = _mine(jid, user)
    if job["status"] in ACTIVE:
        raise HTTPException(409, "It's still going")
    titles = [{**t, "state": "waiting", "why": ""} if t["state"] in ("failed", "left") else t for t in json.loads(job["titles"])]
    if not any(t["state"] == "waiting" for t in titles):
        raise HTTPException(422, "Nothing in it failed")
    db().execute("UPDATE bulk_jobs SET status = 'queued', note = '', titles = ? WHERE id = ?", (json.dumps(titles), jid))
    _pending.put(jid)
    return _job(one("SELECT * FROM bulk_jobs WHERE id = ?", jid))


@router.delete("/jobs/{jid}")
def remove(jid: str, user=Depends(me)):
    """Stop a job that's going (what was requested stays requested), or clear a finished one from the list."""
    job = _mine(jid, user)
    if job["status"] in ACTIVE:
        db().execute(f"UPDATE bulk_jobs SET status = 'stopped', note = 'Stopped' WHERE id = ? AND status IN ({','.join('?' * len(ACTIVE))})", (jid, *ACTIVE))
        return {"ok": True, "stopped": True}
    db().execute("DELETE FROM bulk_jobs WHERE id = ?", (jid,))
    return {"ok": True}


def _job(j: dict) -> dict:
    titles = json.loads(j["titles"])
    count = {s: sum(t["state"] == s for t in titles) for s in ("waiting", "requested", "skipped", "failed", "left")}
    return {"id": j["id"], "kind": j["kind"], "set": j["set_id"], "name": j["name"], "created": j["created"], "status": j["status"], "note": j["note"],
            "total": len(titles), "done": len(titles) - count["waiting"], **count,
            # polled every few seconds: only the titles with something to say, without their pictures
            "titles": [{k: t[k] for k in ("type", "id", "title", "state", "why")} for t in titles if t["why"]]}


# --- the worker: one title at a time ------------------------------------------------------------------------------------

_pending: queue.Queue = queue.Queue()


def one_title(user: dict, t: dict) -> tuple:
    """(state, why) after looking at the title once more and, if it's still missing, requesting it"""
    d = seerr.details(t["type"], t["id"], fresh=True)
    info = d.get("mediaInfo") or {}
    left = LEFT_OUT.get(MEDIA.get(info.get("status"), "unknown"))
    if left:
        return "skipped", {"there": "already in the library", "partly": "partly in the library: pick seasons on its page",
                           "requested": "already requested", "blocked": "blocked in Seerr"}[left]
    if any(REQUEST.get(r.get("status")) == "declined" for r in info.get("requests") or []):
        return "skipped", "declined before: ask an admin"
    seerr.ask(user, t["type"], t["id"])
    return "requested", ""


def run(jid: str):
    down = 0
    while True:
        job = one("SELECT * FROM bulk_jobs WHERE id = ?", jid)
        if not job or job["status"] not in ACTIVE:  # stopped, cleared, or its person's account is gone
            return
        titles = json.loads(job["titles"])
        t = next((t for t in titles if t["state"] == "waiting"), None)

        def close(status, note="", rest=None):
            for x in titles:
                if rest and x["state"] == "waiting":
                    x.update(state="left", why=rest)
            db().execute(f"UPDATE bulk_jobs SET status = ?, note = ?, titles = ? WHERE id = ? AND status IN ({','.join('?' * len(ACTIVE))})",
                         (status, note, json.dumps(titles), jid, *ACTIVE))

        if t is None:
            return close("done")
        # the person as they are now: removed from Movies & TV, unlinked from Seerr or no longer an admin all count
        user = one("SELECT id, name, admin, seerr_id, jellyfin_id FROM users WHERE id = ?", job["user_id"])
        user["admin"] = bool(user["admin"])
        lacking = acl.allowed(user, "/api/seerr/sets/request")
        if lacking:
            return close("stopped", f"{lacking} was closed to this account", "not requested")
        refused = None
        try:
            state, why = one_title(user, t)
            down = 0
        except HTTPException as exc:
            state, why = "failed", str(exc.detail)[:200]
            refused = exc.status_code == 403  # not this title's trouble: the rest would be refused the same way
            down = down + 1 if exc.status_code in (502, 503) else 0
        except Exception as exc:  # noqa: BLE001  (an answer Seerr never gives: that title's alone, the job goes on)
            log.exception("requesting %s %s", t["type"], t["id"])
            state, why, down = "failed", f"{type(exc).__name__}"[:200], 0
        # Stop and Carry on may have rewritten the list meanwhile: this title's outcome goes into the list as it is now
        job = one("SELECT * FROM bulk_jobs WHERE id = ?", jid)
        if not job:
            return
        titles = json.loads(job["titles"])
        t = next(x for x in titles if (x["type"], x["id"]) == (t["type"], t["id"]))
        if refused:
            t.update(state="left", why="")
            quota = "quota" in why.lower()
            return close("stopped", "Seerr's request limit for this account was reached. Carry on when it has reset." if quota else why, "over the limit" if quota else "not requested")
        t.update(state=state, why=why)
        if down >= 3:  # Seerr isn't answering (or fails every time): stop here and keep the rest for Carry on
            for x in titles[-1::-1]:
                if x["state"] == "failed" and down:
                    x.update(state="left", why="")
                    down -= 1
            return close("stopped", f"Seerr isn't answering ({why}). Carry on when it's back.", "not tried")
        db().execute("UPDATE bulk_jobs SET titles = ?, status = CASE WHEN status = 'queued' THEN 'running' ELSE status END WHERE id = ?", (json.dumps(titles), jid))
        if state == "requested":
            _forget_pages()
            _home.drop("all")
        time.sleep(PACE)


def worker():
    while True:
        jid = _pending.get()
        try:
            run(jid)
        except Exception:  # one job's trouble mustn't end the worker
            log.exception("requesting a set failed")
            db().execute("UPDATE bulk_jobs SET status = 'stopped', note = 'Something went wrong here; Retry carries on' WHERE id = ? AND status IN ('queued', 'running')", (jid,))
        finally:
            _pending.task_done()


def start():
    """Carry on with what a restart interrupted, and drop finished jobs older than a month."""
    db().execute("DELETE FROM bulk_jobs WHERE status NOT IN ('queued', 'running') AND created < ?", (time.time() - 30 * 86400,))
    for j in rows("SELECT id FROM bulk_jobs WHERE status IN ('queued', 'running') ORDER BY created"):
        _pending.put(j["id"])
    threading.Thread(target=worker, daemon=True, name="bulk-requests").start()
