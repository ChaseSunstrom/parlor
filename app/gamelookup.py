"""Questarr's game lookups, answered by Parlor from Steam's public store, so nobody has to register for IGDB keys.

Questarr asks IGDB (and Twitch, for a token) for everything it knows about a game. app/questarr-lookup.cjs, loaded
into Questarr, sends those calls here, and this answers in IGDB's shape: its paths, its field names, and its query
language as far as Questarr 1.6.0 writes it. A game's id is its Steam app id. A game Steam doesn't sell isn't found.

Only Questarr may ask: each call carries the key that setup wrote into Questarr's own folder (stack.init_files).
Steam's answers are kept in memory for 15 minutes: Questarr words one title several ways in a row, and its lists share
games. (Questarr keeps each of its own questions too: 15 minutes for a search, a day for a game.)"""
import hmac
import json
import re
import time

import anyio
import requests
from fastapi import APIRouter, Depends, HTTPException, Request

import arr

router = APIRouter(prefix="/lookup/games", include_in_schema=False)
KEY = arr.STACK / "questarr" / "parlor-lookup.key"
STORE = "https://store.steampowered.com/api"
API = "https://api.steampowered.com"
PICTURES = "//shared.akamai.steamstatic.com/store_item_assets/"  # no scheme: Questarr puts "https:" before a picture's address
CONTEXT = {"language": "english", "country_code": "US"}
WANT = {"include_assets": True, "include_release": True, "include_basic_info": True, "include_tag_count": 20, "include_reviews": True,
        "include_platforms": True, "include_screenshots": True}
POPULAR = 12  # Steam's order for a list with no search in it: what's being played and bought now
KEPT = 900  # seconds one of Steam's answers is reused
_now = time.time  # the clock (tests move it)
_kept: dict = {}  # (address, what was asked) -> (when, Steam's answer)

KINDS = {0: 0, 4: 1}  # Steam's kind of item -> IGDB's game_type: a game, an add-on. Anything else (hardware, films, tools) isn't a game
PLATFORMS = [("windows", {"id": 6, "name": "PC (Microsoft Windows)"}), ("mac", {"id": 14, "name": "Mac"}), ("steamos_linux", {"id": 3, "name": "Linux"})]
EROTIC = 42
TAGS = {  # IGDB's field -> IGDB's id -> (IGDB's name, Steam's tag for the same thing)
    "genres": {
        2: ("Point-and-click", 1698), 4: ("Fighting", 1743), 5: ("Shooter", 1774), 7: ("Music", 1621), 8: ("Platform", 1625), 9: ("Puzzle", 1664),
        10: ("Racing", 699), 11: ("Real Time Strategy (RTS)", 1676), 12: ("Role-playing (RPG)", 122), 13: ("Simulator", 599), 14: ("Sport", 701),
        15: ("Strategy", 9), 16: ("Turn-based strategy (TBS)", 1741), 24: ("Tactical", 1708), 25: ("Hack and slash/Beat 'em up", 1646),
        26: ("Quiz/Trivia", 10437), 30: ("Pinball", 6621), 31: ("Adventure", 21), 32: ("Indie", 492), 33: ("Arcade", 1773), 34: ("Visual Novel", 3799),
        35: ("Card & Board Game", 1666), 36: ("MOBA", 1718)},
    "themes": {
        1: ("Action", 19), 17: ("Fantasy", 1684), 18: ("Science fiction", 3942), 19: ("Horror", 1667), 20: ("Thriller", 4064), 21: ("Survival", 1662),
        22: ("Historical", 3987), 23: ("Stealth", 1687), 27: ("Comedy", 1719), 28: ("Business", 12472), 33: ("Sandbox", 3810), 34: ("Educational", 1036),
        35: ("Kids", 5350), 38: ("Open world", 1695), 39: ("Warfare", 1678), 40: ("Party", 7178), 41: ("4X (explore, expand, exploit, and exterminate)", 1670),
        EROTIC: ("Erotic", 9130), 43: ("Mystery", 5716), 44: ("Romance", 4947)},
    "game_modes": {
        1: ("Single player", 4182), 2: ("Multiplayer", 3859), 3: ("Co-operative", 1685), 4: ("Split screen", 10816),
        5: ("Massively Multiplayer Online (MMO)", 128), 6: ("Battle Royale", 176981)},
    "player_perspectives": {
        1: ("First person", 3839), 2: ("Third person", 1697), 3: ("Bird view / Isometric", 5851), 4: ("Side view", 3798), 5: ("Text", 31275),
        7: ("Virtual Reality", 21978)},
}


def steam(url: str, **params) -> dict:
    asked = (url, *sorted(params.items()))
    hit = _kept.get(asked)
    if hit and _now() - hit[0] < KEPT:
        return hit[1]
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, allow_redirects=False)
        if r.status_code == 200:
            if len(_kept) >= 500:  # ponytail: emptied whole when full; least-recently-used if Steam's limits ever bite
                _kept.clear()
            _kept[asked] = (_now(), r.json())
            return _kept[asked][1]
    except (requests.RequestException, ValueError):
        raise HTTPException(502, "Steam's store can't be reached")
    raise HTTPException(429 if r.status_code == 429 else 502, "Steam's store didn't answer")  # 429: Questarr waits and asks again


def service(name: str, body: dict) -> dict:
    return steam(f"{API}/{name}/v1", input_json=json.dumps({**body, "context": CONTEXT, "data_request": WANT})).get("response") or {}


def items(ids: list) -> list:
    """Steam's store entries for these app ids, in the order asked."""
    out = []
    for i in range(0, len(ids), 50):
        part = ids[i:i + 50]
        by = {it.get("appid"): it for it in service("IStoreBrowseService/GetItems", {"ids": [{"appid": n} for n in part]}).get("store_items") or []}
        out += [by[n] for n in part if n in by]
    return out


def game(it: dict):
    """A Steam store entry as IGDB would describe the game; None for what isn't a game or isn't there."""
    if it.get("success") != 1 or it.get("type") not in KINDS or not it.get("appid"):
        return None
    info, release, assets = it.get("basic_info") or {}, it.get("release") or {}, it.get("assets") or {}
    reviews = (it.get("reviews") or {}).get("summary_filtered") or {}
    tags = {t.get("tagid") for t in it.get("tags") or []}
    kind, on = KINDS[it["type"]], it.get("platforms") or {}
    g = {"id": it["appid"], "name": it.get("name") or "", "summary": info.get("short_description") or "", "game_type": kind, "category": kind,
         "platforms": [p for k, p in PLATFORMS if on.get(k)],
         "websites": [{"category": 13, "url": f"https://store.steampowered.com/app/{it['appid']}"}],  # 13: IGDB's number for a Steam page
         "involved_companies": [{"company": {"name": c["name"]}, "developer": role == "developers", "publisher": role == "publishers"}
                                for role in ("developers", "publishers") for c in info.get(role) or [] if c.get("name")],
         "screenshots": [{"url": PICTURES + s["filename"]} for s in (it.get("screenshots") or {}).get("all_ages_screenshots") or [] if s.get("filename")][:10]}
    for field, table in TAGS.items():
        g[field] = [{"id": i, "name": name} for i, (name, tag) in table.items() if tag in tags]
    described = it.get("content_descriptorids") or []  # Steam's own: 3 is adult-only sexual content, 4 frequent nudity or sexual content
    if 3 in described and not any(t["id"] == EROTIC for t in g["themes"]):
        g["themes"].append({"id": EROTIC, "name": TAGS["themes"][EROTIC][0]})
    if 3 in described or 4 in described:
        g["age_ratings"] = [{"category": 2, "rating": 5}]  # PEGI 18, the mark Questarr's age filter reads
    picture = assets.get("library_capsule") or assets.get("header")  # the upright cover first
    if picture and assets.get("asset_url_format"):
        g["cover"] = {"url": PICTURES + assets["asset_url_format"].replace("${FILENAME}", picture)}
    when = release.get("steam_release_date") or release.get("original_release_date")
    if when:
        g["first_release_date"] = when
    if reviews.get("review_count"):
        g["rating"] = g["aggregated_rating"] = reviews.get("percent_positive") or 0
        g["rating_count"] = g["aggregated_rating_count"] = reviews["review_count"]
    if release.get("is_early_access"):
        g["status"] = 4  # IGDB's number for early access
    return g


def clauses(text: str) -> dict:
    """IGDB's query language, as {word: the rest}: 'fields name; where id = 3; limit 5;' has three."""
    return {word: rest.strip() for word, _, rest in (part.strip().partition(" ") for part in text.split(";")) if word}


def number(q: dict, word: str, default: int, most: int) -> int:
    return min(int(q[word]), most) if q.get(word, "").isdecimal() else default


def wanted(where: str, said: str) -> list:
    """What a where clause asks of a game's genres, themes, modes and perspectives: [(field, wanted or unwanted, ids)].
    `where` is the clause with its quoted words emptied, `said` the clause as it came."""
    out = [(field, not no, {int(n) for n in ids.split(",") if n}) for field, no, ids in re.findall(r"(?<![\w.])(%s) = (!?)\(([\d,]+)\)" % "|".join(TAGS), where)]
    names = [n.lower() for n in re.findall(r'genres\.name ~ \*"([^"]*)"\*', said)]
    if names:
        out.append(("genres", True, {i for i, (name, _) in TAGS["genres"].items() if any(n in name.lower() for n in names)}))
    return out


def games(q: dict) -> list:
    said = q.get("where", "")
    where = re.sub(r'"[^"]*"', '""', said)  # without what's in quotes: a title that reads "id = 5" is a title
    limit, offset = number(q, "limit", 10, 100), number(q, "offset", 0, 10_000)
    asked = wanted(where, said)
    since, until = (int(m.group(1)) if (m := re.search(rf"first_release_date {op} (\d+)", where)) else None for op in (">=", "<="))
    ids = re.search(r"(?<![\w.])id = \(?([\d,\s]+)", where)
    text = q.get("search", "").strip('"') or " ".join(re.findall(r'(?<![\w.])name ~=? ?\*?"([^"]*)"', said))
    if "version_parent" in where:  # the editions of a game: Steam has no such link
        return []
    if ids:
        found = items([int(n) for n in re.findall(r"\d+", ids.group(1))][:100])
    elif text:
        hits = steam(f"{STORE}/storesearch/", term=text, l="english", cc="us").get("items") or []
        found = items([h["id"] for h in hits if h.get("type") == "app" and isinstance(h.get("id"), int)])
    elif "platforms.name" in where and not re.search(r'platforms\.name ~ \*"(PC|Mac|Linux)', said):
        return []  # games for a console: none of Steam's
    else:
        groups = [[str(TAGS[field][i][1]) for i in ids_ if i in TAGS[field]] for field, yes, ids_ in asked if yes]
        if not all(groups):
            return []  # asked for something Steam has no tag for
        upcoming = since is not None and since >= _now() - 7200
        filters = {"type_filters": {"include_games": True}, "coming_soon_only" if upcoming else "released_only": True,
                   "tagids_must_match": [{"tagids": g} for g in groups],
                   "tagids_exclude": [str(TAGS[field][i][1]) for field, yes, ids_ in asked if not yes for i in ids_ if i in TAGS[field]]}
        # between two dates (new this month, out soon): the most popular hundred, and those in the window are kept below
        found = service("IStoreQueryService/Query", {"query": {"start": offset, "count": 100 if until else limit, "sort": POPULAR, "filters": filters}}).get("store_items") or []
        offset = 0
    left_out = {int(n) for part in re.findall(r"(?<![\w.])id != \(([\d,\s]+)\)", where) for n in re.findall(r"\d+", part)}

    def keep(g):
        when, has = g.get("first_release_date") or 0, lambda field: {x["id"] for x in g[field]}
        return (g["id"] not in left_out and (since is None or when >= since) and (until is None or 0 < when <= until)
                and all(bool(has(field) & ids_) == yes for field, yes, ids_ in asked))
    out = [g for g in map(game, found) if g and (ids or keep(g))]
    if q.get("sort", "").startswith("first_release_date"):
        out.sort(key=lambda g: g.get("first_release_date") or 0, reverse=q["sort"].endswith("desc"))
    return out[offset:offset + limit]


def listed(rows: list):
    return lambda q: sorted(rows, key=lambda r: r["name"])[number(q, "offset", 0, 10_000):][:number(q, "limit", 10, 500)]


ENDPOINTS = {
    "games": games,
    "genres": listed([{"id": i, "name": name} for i, (name, _) in TAGS["genres"].items()]),
    "platforms": listed([p for _, p in PLATFORMS]),
    # which game has this Steam app id: the same number here
    "external_games": lambda q: [{"game": int(n), "uid": n} for n in re.findall(r'"(\d{1,10})"', q.get("where", ""))],
}


def answer(endpoint: str, text: str) -> list:
    if endpoint == "multiquery":  # several at once: query games "q0" { fields name; where name ~ *"doom"*; limit 1; };
        return [{"name": name, "result": answer(what, body)} for what, name, body in re.findall(r'query (\w+) "([^"]*)" \{(.*?)\}\s*;', text, re.S)[:10]]
    return ENDPOINTS[endpoint](clauses(text)) if endpoint in ENDPOINTS else []  # time to beat and the rest: nothing known


def questarr(request: Request):
    try:
        key = KEY.read_text().strip()
    except (OSError, ValueError):  # not there yet, or not text
        key = ""
    if not key or not hmac.compare_digest(key.encode(), (request.headers.get("x-parlor-lookup") or "").encode()):
        raise HTTPException(401, "Only Parlor's own Questarr may ask")


@router.post("/oauth2/token", dependencies=[Depends(questarr)])
def token():
    """Twitch's part: the token IGDB wants. Nothing checks it; the key above is what counts."""
    return {"access_token": "parlor", "expires_in": 600, "token_type": "bearer"}  # short: Questarr given IGDB keys of its own asks IGDB for a real one soon


@router.post("/v4/{endpoint}", dependencies=[Depends(questarr)])
async def igdb(endpoint: str, request: Request):
    body = await request.body()
    if len(body) > 16_384:  # Questarr's longest (500 Steam ids at once) is a third of this
        raise HTTPException(413, "Too long a question")
    return await anyio.to_thread.run_sync(answer, endpoint, body.decode(errors="replace"))
