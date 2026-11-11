"""Movies and TV through Seerr (also works with Overseerr and Jellyseerr): discover,
search, details, people, and requests made as each person's own Seerr user."""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Union
from urllib.parse import quote

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import admin, db, me, one, setting
from yt import Cache

log = logging.getLogger("parlor.seerr")
router = APIRouter(prefix="/api/seerr")
IMG = "https://image.tmdb.org/t/p"

# Seerr's numbers for where a title is
MEDIA = {1: "unknown", 2: "pending", 3: "processing", 4: "partial", 5: "available", 6: "blocked", 7: "deleted"}
REQUEST = {1: "pending", 2: "approved", 3: "declined", 4: "failed", 5: "completed"}


def call(method, path, *, as_user=None, params=None, json=None, timeout=20):
    url, key = setting("seerr_url").rstrip("/"), setting("seerr_key")
    if not url or not key:
        raise HTTPException(503, "Seerr isn't connected yet. An admin can add its address and API key in Settings.")
    headers = {"X-Api-Key": key, "Accept": "application/json"}
    if as_user:
        headers["X-Api-User"] = str(as_user)  # Seerr acts as this user: their quotas, permissions and name
    try:
        r = requests.request(method, f"{url}/api/v1/{path}", headers=headers, params=params, json=json, timeout=timeout)
    except requests.RequestException as exc:
        raise HTTPException(502, f"Couldn't reach Seerr at {url}: {type(exc).__name__}") from exc
    if r.status_code == 401 or (r.status_code == 403 and not as_user):
        raise HTTPException(502, "Seerr refused the API key. Check it in Settings.")
    if not r.ok:
        try:
            msg = r.json().get("message") or r.text
        except ValueError:
            msg = r.text
        raise HTTPException(r.status_code if r.status_code in (403, 404, 409, 422) else 502, f"Seerr: {msg[:300]}")
    return r.json() if r.content else None


def img(path, size="w342"):
    return f"{IMG}/{size}{path}" if path else None


def year(item):
    date = item.get("releaseDate") or item.get("firstAirDate") or ""
    return date[:4] or None


def status_of(media_info):
    m = media_info or {}
    return {"status": MEDIA.get(m.get("status"), "unknown"), "status4k": MEDIA.get(m.get("status4k"), "unknown"),
            "url": m.get("mediaUrl") or m.get("plexUrl") or m.get("jellyfinUrl"),
            "requested": bool(m.get("requests")),
            "jellyfin": m.get("jellyfinMediaId") if re.fullmatch(r"[0-9a-f]{32}", m.get("jellyfinMediaId") or "") else None}


def card(item):
    kind = item.get("mediaType") or ("tv" if "firstAirDate" in item or "name" in item and "title" not in item else "movie")
    if kind == "person":
        return {"type": "person", "id": item["id"], "name": item.get("name"), "photo": img(item.get("profilePath"), "w185"),
                "known_for": [k.get("title") or k.get("name") for k in item.get("knownFor") or []][:3]}
    return {"type": kind, "id": item["id"], "title": item.get("title") or item.get("name") or "", "year": year(item),
            "poster": img(item.get("posterPath")), "backdrop": img(item.get("backdropPath"), "w1280"),
            "overview": item.get("overview") or "", "rating": round(item.get("voteAverage") or 0, 1) or None,
            **status_of(item.get("mediaInfo"))}


def cards(results):
    out = []
    for r in results or []:
        try:
            if r.get("mediaType") in (None, "movie", "tv", "person"):
                out.append(card(r))
        except (KeyError, TypeError):
            pass
    return out


@router.get("/status")
def status(_=Depends(me)):
    if not setting("seerr_url") or not setting("seerr_key"):
        return {"connected": False, "reason": "not set up"}
    try:
        s = call("GET", "status", timeout=8)
        call("GET", "settings/main", timeout=8)  # needs the key, so this checks it
        return {"connected": True, "version": s.get("version")}
    except HTTPException as exc:
        return {"connected": False, "reason": exc.detail}


_discover = Cache(1800, 50)
SHELVES = {
    "trending": "discover/trending",
    "movies": "discover/movies",
    "tv": "discover/tv",
    "upcoming": "discover/movies/upcoming",
    "upcoming_tv": "discover/tv/upcoming",
}


@router.get("/discover")
def discover(_=Depends(me)):
    hit = _discover.get("all")
    if hit is None:
        with ThreadPoolExecutor(len(SHELVES)) as pool:
            got = dict(zip(SHELVES, pool.map(lambda p: _shelf(p), SHELVES.values())))
        if not any(got.values()):
            call("GET", "status")  # nothing came back: surface why
        hit = _discover.put("all", got)
    return hit


def _shelf(path, page=1):
    try:
        return cards(call("GET", path, params={"page": page}).get("results"))
    except HTTPException as exc:
        log.warning("Seerr %s failed: %s", path, exc.detail)
        return []


@router.get("/discover/{shelf}")
def more(shelf: str, page: int = 1, _=Depends(me)):
    if shelf not in SHELVES:
        raise HTTPException(404, "No such list")
    return {"items": cards(call("GET", SHELVES[shelf], params={"page": max(1, min(page, 50))}).get("results"))}


@router.get("/search")
def search(q: str, page: int = 1, _=Depends(me)):
    q = q.strip()[:200]
    if not q:
        return {"items": [], "pages": 0}
    # Seerr rejects '+' for spaces and some unescaped characters; encode it all
    data = call("GET", f"search?query={quote(q, safe='')}&page={max(1, min(page, 50))}")
    return {"items": cards(data.get("results")), "pages": data.get("totalPages"), "total": data.get("totalResults")}


_details = Cache(6 * 3600, 500)


def details(kind, tmdb_id, fresh=False):
    key = (kind, tmdb_id)
    hit = None if fresh else _details.get(key)
    if hit is None:
        hit = _details.put(key, call("GET", f"{kind}/{tmdb_id}"))
    return hit


def _people(credits):
    cast = [{"id": c["id"], "name": c.get("name"), "role": c.get("character"), "photo": img(c.get("profilePath"), "w185")}
            for c in (credits or {}).get("cast", [])[:20]]
    crew = [{"id": c["id"], "name": c.get("name"), "role": c.get("job")}
            for c in (credits or {}).get("crew", []) if c.get("job") in ("Director", "Creator", "Screenplay", "Writer")][:6]
    return cast, crew


def _trailer(d):
    videos = d.get("relatedVideos") or []
    for kind in ("Trailer", "Teaser"):
        for v in videos:
            if v.get("site") == "YouTube" and v.get("type") == kind and v.get("key"):
                return {"id": v["key"], "name": v.get("name")}
    return None


@router.get("/movie/{tmdb_id}")
def movie(tmdb_id: int, user=Depends(me)):
    return title("movie", tmdb_id, user)


@router.get("/tv/{tmdb_id}")
def tv(tmdb_id: int, user=Depends(me)):
    return title("tv", tmdb_id, user)


def title(kind, tmdb_id, user):
    with ThreadPoolExecutor(2) as pool:
        recs = pool.submit(lambda: _shelf(f"{kind}/{tmdb_id}/recommendations"))
        d = details(kind, tmdb_id, fresh=True)  # request status changes; always fresh here
        cast, crew = _people(d.get("credits"))
        info = d.get("mediaInfo") or {}
        seasons = []
        if kind == "tv":
            have = {s.get("seasonNumber"): MEDIA.get(s.get("status"), "unknown") for s in info.get("seasons") or []}
            asked = {}
            for r in info.get("requests") or []:
                if REQUEST.get(r.get("status")) in ("pending", "approved"):
                    for s in r.get("seasons") or []:
                        asked[s.get("seasonNumber")] = "requested"
            seasons = [{"number": s["seasonNumber"], "name": s.get("name"), "episodes": s.get("episodeCount"),
                        "air": (s.get("airDate") or "")[:4] or None, "poster": img(s.get("posterPath"), "w185"),
                        "status": have.get(s["seasonNumber"]) if have.get(s["seasonNumber"]) not in (None, "unknown")
                        else asked.get(s["seasonNumber"], "missing")}
                       for s in d.get("seasons") or [] if s.get("seasonNumber")]
        return {
            **card({**d, "mediaType": kind}),
            "tagline": d.get("tagline"),
            "runtime": d.get("runtime") or (d.get("episodeRunTime") or [None])[0],
            "genres": [g["name"] for g in d.get("genres") or []],
            "certification": _cert(d),
            "network": ", ".join(n["name"] for n in (d.get("networks") or [])[:2]) or None,
            "studio": ", ".join(c["name"] for c in (d.get("productionCompanies") or [])[:2]) or None,
            # whole sets this title belongs to (bulk.py): its film collection, studios, networks and keywords
            "collection": {"id": d["collection"]["id"], "name": d["collection"].get("name")} if (d.get("collection") or {}).get("id") else None,
            "studios": [{"id": c["id"], "name": c.get("name")} for c in (d.get("productionCompanies") or [])[:3] if c.get("id")],
            "networks": [{"id": n["id"], "name": n.get("name")} for n in (d.get("networks") or [])[:3] if n.get("id")],
            "keywords": [{"id": k["id"], "name": k.get("name")} for k in (d.get("keywords") or [])[:12] if k.get("id")],
            "seasons_count": d.get("numberOfSeasons"),
            "show_status": d.get("status"),
            "cast": cast,
            "crew": crew,
            "trailer": _trailer(d),
            "seasons": seasons,
            "watch": [{"name": p.get("name"), "logo": img(p.get("logoPath"), "w92")}
                      for region in (d.get("watchProviders") or [])[:1] for p in (region.get("flatrate") or [])[:6]],
            "requests": [_request_brief(r) for r in info.get("requests") or []],
            "recommended": recs.result()[:20],
            "can_request": can_request(user),
        }


def _cert(d):
    for r in (d.get("releases") or {}).get("results", []):
        if r.get("iso_3166_1") == "US":
            for x in r.get("release_dates") or []:
                if x.get("certification"):
                    return x["certification"]
    for r in (d.get("contentRatings") or {}).get("results", []):
        if r.get("iso_3166_1") == "US" and r.get("rating"):
            return r["rating"]
    return None


def _request_brief(r):
    by = r.get("requestedBy") or {}
    return {"id": r.get("id"), "status": REQUEST.get(r.get("status"), "unknown"), "by": by.get("displayName") or by.get("username"),
            "seasons": [s.get("seasonNumber") for s in r.get("seasons") or []], "created": r.get("createdAt"), "is4k": r.get("is4k")}


@router.get("/person/{person_id}")
def person(person_id: int, _=Depends(me)):
    with ThreadPoolExecutor(2) as pool:
        work = pool.submit(lambda: call("GET", f"person/{person_id}/combined_credits"))
        p = call("GET", f"person/{person_id}")
        credits = work.result()
    seen, known = set(), []
    for c in sorted((credits or {}).get("cast", []) + (credits or {}).get("crew", []), key=lambda c: -(c.get("popularity") or 0)):
        if c.get("id") in seen or c.get("mediaType") not in ("movie", "tv"):
            continue
        seen.add(c["id"])
        known.append({**card(c), "role": c.get("character") or c.get("job")})
    return {"id": person_id, "name": p.get("name"), "photo": img(p.get("profilePath"), "h632"), "bio": p.get("biography") or "",
            "born": p.get("birthday"), "died": p.get("deathday"), "place": p.get("placeOfBirth"),
            "department": p.get("knownForDepartment"), "items": known[:60]}


# --- requests, made as you -----------------------------------------------------------------

_seerr_users = Cache(300, 2)


def seerr_users():
    hit = _seerr_users.get("all")
    if hit is None:
        hit = _seerr_users.put("all", call("GET", "user", params={"take": 1000}).get("results") or [])
    return hit


def match_user(user, people):
    """The Seerr user for one of our accounts: only the same Jellyfin account
    counts. A matching name could be anyone (open sign-up), so names are only
    suggested to an admin (see suggest_user)."""
    if user.get("jellyfin_id"):
        want = user["jellyfin_id"].replace("-", "").lower()
        for p in people:
            if (p.get("jellyfinUserId") or "").replace("-", "").lower() == want:
                return p["id"]
    return None


def suggest_user(user, people):
    """A Seerr user with the same name (Seerr username, Jellyfin username, display name or email name)."""
    name = user["name"].casefold()
    for p in people:
        names = {(p.get(k) or "").casefold() for k in ("username", "jellyfinUsername", "plexUsername", "displayName")}
        names.add((p.get("email") or "").split("@")[0].casefold())
        if name in names - {""}:
            return p["id"]
    return None


def seerr_user(user) -> Optional[int]:
    """Which Seerr user this account requests as; remembered once found."""
    if user.get("seerr_id"):
        return user["seerr_id"]
    found = match_user(user, seerr_users())
    if found:
        db().execute("UPDATE users SET seerr_id = ? WHERE id = ?", (found, user["id"]))
        user["seerr_id"] = found
    return found


def can_request(user) -> bool:
    try:
        return bool(user["admin"] or seerr_user(user))
    except HTTPException:
        return False


def requester(user) -> Optional[int]:
    found = seerr_user(user)
    if found is None and not user["admin"]:
        raise HTTPException(403, "Your account isn't linked to a Seerr user yet. Ask an admin to link it in Settings → Users.")
    return found  # None for an unlinked admin: Seerr's API-key owner makes the request


class MediaRequest(BaseModel):
    type: str
    id: int
    seasons: Union[List[int], str, None] = None  # TV: season numbers, or "all"
    is4k: bool = False


@router.post("/request")
def make_request(body: MediaRequest, user=Depends(me)):
    return {"ok": True, "status": ask(user, body.type, body.id, body.seasons, body.is4k)}


def ask(user, kind, tmdb_id, seasons=None, is4k=False) -> str:
    """Request one title as this person's Seerr user; what Seerr made of it (pending, approved…). Whole sets come
    through here too (bulk.py), one title at a time, so the rules are the same."""
    if kind not in ("movie", "tv"):
        raise HTTPException(422, "type is movie or tv")
    payload = {"mediaType": kind, "mediaId": tmdb_id, "is4k": is4k}
    if kind == "tv":
        if seasons in (None, "all"):
            d = details("tv", tmdb_id)
            payload["seasons"] = [s["seasonNumber"] for s in d.get("seasons") or [] if s.get("seasonNumber")]
        else:
            payload["seasons"] = sorted({int(s) for s in seasons if int(s) > 0})
        if not payload["seasons"]:
            raise HTTPException(422, "Pick at least one season")
    as_user = requester(user)
    r = call("POST", "request", as_user=as_user, json=payload)
    _details.drop((kind, tmdb_id))
    _discover.drop("all")
    return REQUEST.get((r or {}).get("status"), "pending")


@router.get("/requests")
def requests_list(scope: str = "mine", filter: str = "all", page: int = 1, user=Depends(me)):
    if filter not in ("all", "pending", "approved", "available", "processing", "unavailable", "failed"):
        raise HTTPException(422, "Unknown filter")
    params = {"take": 30, "skip": (max(1, page) - 1) * 30, "filter": filter, "sort": "added"}
    if scope != "all" or not user["admin"]:
        mine = seerr_user(user)
        if mine is None:
            return {"items": [], "pages": 0, "linked": False}
        params["requestedBy"] = mine
    data = call("GET", "request", params=params)
    reqs = data.get("results") or []

    def full(r):
        media = r.get("media") or {}
        kind, tmdb = media.get("mediaType") or r.get("type"), media.get("tmdbId")
        try:
            d = details(kind, tmdb) if kind in ("movie", "tv") and tmdb else {}
        except HTTPException:
            d = {}
        by = r.get("requestedBy") or {}
        return {**_request_brief(r), "type": kind, "tmdb": tmdb, "title": d.get("title") or d.get("name") or f"#{tmdb}",
                "year": year(d), "poster": img(d.get("posterPath"), "w185"), "backdrop": img(d.get("backdropPath"), "w780"),
                "media": MEDIA.get(media.get("status"), "unknown"), "by_id": by.get("id"),
                "avatar": by.get("avatar") if (by.get("avatar") or "").startswith("http") else None,
                "mine": by.get("id") is not None and by.get("id") == user.get("seerr_id")}

    with ThreadPoolExecutor(8) as pool:
        items = list(pool.map(full, reqs))
    info = data.get("pageInfo") or {}
    return {"items": items, "pages": info.get("pages"), "total": info.get("results"), "linked": True}


@router.post("/requests/{request_id}/{action}")
def decide(request_id: int, action: str, user=Depends(admin)):
    if action not in ("approve", "decline"):
        raise HTTPException(404, "Approve or decline")
    call("POST", f"request/{request_id}/{action}", as_user=seerr_user(user))
    return {"ok": True}


@router.delete("/requests/{request_id}")
def cancel(request_id: int, user=Depends(me)):
    r = call("GET", f"request/{request_id}")
    owner = (r.get("requestedBy") or {}).get("id")
    if not user["admin"] and (owner is None or owner != seerr_user(user)):
        raise HTTPException(403, "You can only cancel your own requests")
    call("DELETE", f"request/{request_id}", as_user=seerr_user(user))
    return {"ok": True}


@router.get("/users")
def users(_=Depends(admin)):
    _seerr_users.drop("all")
    people = seerr_users()
    ours = {u["id"]: suggest_user(u, people) for u in db().execute("SELECT id, name FROM users")}
    return {"users": [{"id": p["id"], "name": p.get("displayName") or p.get("username") or p.get("email"), "email": p.get("email"),
                       "jellyfin": p.get("jellyfinUsername")} for p in people],
            "suggested": {str(k): v for k, v in ours.items() if v}}


@router.get("/counts")
def counts(user=Depends(admin)):
    return call("GET", "request/count")
