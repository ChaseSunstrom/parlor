"""Games: each person's Steam library (the Steam Web API, with an admin's key that never leaves the server), retro games
from the GAMES folder played in Parlor, and streaming from a gaming PC. Steam's answers are kept an hour, and its
art comes through Parlor from Steam's image server only, kept on disk."""
import hashlib
import html
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import anyio
import requests
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import acl
import core
import security
import vault
from core import DATA, admin, db, me, one, set_setting, setting
from yt import send_file

log = logging.getLogger("parlor.games")
router = APIRouter(prefix="/api/games")
STEAM_API = "https://api.steampowered.com"
STEAM_ART = "https://shared.steamstatic.com/store_item_assets/steam/apps/{appid}/{kind}.jpg"
ART = {"header", "capsule_616x353", "library_600x900", "library_hero"}  # the art Steam has for every game
HOUR = 3600
_now = time.time  # the clock (tests move it)
_cache: dict = {}  # steam id -> (when, answer)


class GameSettings(BaseModel):
    steam_key: Optional[str] = Field(None, max_length=100)


@router.get("")
def overview(_=Depends(me)):
    """what this server offers: whether Steam has its key, and whether the bridge to a PC is on and set up"""
    return {"steam_key_set": bool(setting("steam_key")), "stream": bool(STREAM), "stream_ready": setting("games_stream_admin") == "1"}


@router.get("/settings")
def get_settings(_=Depends(admin)):
    return {"steam_key_set": bool(setting("steam_key"))}  # the key itself is never sent back


@router.put("/settings")
def put_settings(body: GameSettings, user=Depends(admin)):
    if body.steam_key is not None:
        key = body.steam_key.strip()
        if key and not re.fullmatch(r"[0-9A-Fa-f]{32}", key):
            raise HTTPException(422, "A Steam Web API key is 32 letters and digits (steamcommunity.com/dev/apikey)")
        if key:
            try:  # a typo shows now, not when someone links a profile
                steam("ISteamUser/ResolveVanityURL/v1/", key=key, vanityurl="parlor")
            except HTTPException as exc:
                if "refused" in exc.detail:
                    raise HTTPException(422, "Steam refused that key: check it at steamcommunity.com/dev/apikey")
                # Steam not answering: keep it, it's checked again on use
        set_setting("steam_key", key.upper())
        _cache.clear()
    return get_settings(user)


# --- Steam ------------------------------------------------------------------------------

def steam(path: str, key: str = "", **params) -> dict:
    key = key or setting("steam_key")
    if not key:
        raise HTTPException(409, "Steam isn't set up: an admin adds a Steam Web API key on the Games page")
    try:
        r = requests.get(f"{STEAM_API}/{path}", params={"key": key, "format": "json", **params}, timeout=15, allow_redirects=False)
    except requests.RequestException as exc:
        log.warning("Steam: %s", type(exc).__name__)  # never the address: it carries the key
        raise HTTPException(502, "Steam isn't answering; try again in a while")
    if r.status_code in (401, 403):
        raise HTTPException(502, "Steam refused Parlor's key: an admin replaces it on the Games page")
    if r.status_code != 200:
        raise HTTPException(502, f"Steam isn't answering ({r.status_code}); try again in a while")
    try:
        return r.json().get("response") or {}
    except ValueError:
        raise HTTPException(502, "Steam sent something Parlor can't read")


PROFILE = re.compile(r"(?:https?://)?(?:www\.)?steamcommunity\.com/(profiles|id)/([^/?#]+)/?(?:[?#].*)?")


def steam_id(profile: str) -> str:
    """the 17-digit id from a profile address, a custom name (steamcommunity.com/id/<name>) or the id itself"""
    text, bad = profile.strip(), "Give your Steam profile's address (steamcommunity.com/…), its custom name, or its 17-digit id"
    m = PROFILE.fullmatch(text)
    if m:
        text = m[2]
    elif "/" in text or ":" in text:
        raise HTTPException(422, bad)  # an address, but not Steam's
    if re.fullmatch(r"7656119\d{10}", text):
        return text
    if not re.fullmatch(r"[\w.-]{2,32}", text):
        raise HTTPException(422, bad)
    found = steam("ISteamUser/ResolveVanityURL/v1/", vanityurl=text)
    if found.get("success") != 1 or not found.get("steamid"):
        raise HTTPException(404, f"No Steam profile is called “{text}”")
    return found["steamid"]


class Link(BaseModel):
    profile: str = Field(min_length=1, max_length=200)


@router.put("/steam")
def link(body: Link, user=Depends(me)):
    sid = steam_id(body.profile)
    db().execute("UPDATE users SET steam_id = ? WHERE id = ?", (sid, user["id"]))
    return {"steam_id": sid}


@router.delete("/steam")
def unlink(user=Depends(me)):
    db().execute("UPDATE users SET steam_id = NULL WHERE id = ?", (user["id"],))
    return {"ok": True}


def _game(g: dict) -> dict:
    return {"appid": g["appid"], "name": g.get("name") or f"App {g['appid']}", "minutes": g.get("playtime_forever", 0),
            "last": g.get("rtime_last_played") or 0, "art": f"/api/games/steam/art/{g['appid']}/header"}


@router.get("/steam")
def library(user=Depends(me)):
    """this person's Steam games: most played first, and the last two weeks'"""
    sid = (one("SELECT steam_id FROM users WHERE id = ?", user["id"]) or {}).get("steam_id")
    if not sid:
        return {"linked": False, "key_set": bool(setting("steam_key"))}
    hit = _cache.get(sid)
    if hit and _now() - hit[0] < HOUR:
        return hit[1]
    players = steam("ISteamUser/GetPlayerSummaries/v2/", steamids=sid).get("players") or [{}]
    p = players[0]
    owned = steam("IPlayerService/GetOwnedGames/v1/", steamid=sid, include_appinfo=1, include_played_free_games=1)
    recent = steam("IPlayerService/GetRecentlyPlayedGames/v1/", steamid=sid, count=12) if owned else {}
    hidden = "game_count" not in owned  # a private profile, or private game details, answer with nothing at all
    answer = {"linked": True, "steam_id": sid, "private": hidden,
              "profile": {"name": p.get("personaname") or ""},
              "games": sorted((_game(g) for g in owned.get("games", [])), key=lambda g: (-g["minutes"], g["name"])),
              "recent": [{**_game(g), "minutes_2weeks": g.get("playtime_2weeks", 0)} for g in recent.get("games", [])],
              "note": "Steam keeps this profile's games private. In Steam: Profile → Edit Profile → Privacy Settings, set "
                      "My profile and Game details to Public." if hidden else ""}
    _cache[sid] = (_now(), answer)
    return answer


@router.get("/steam/art/{appid}/{kind}")
def art(appid: int, kind: str, _=Depends(me)):
    if kind not in ART:
        raise HTTPException(422, "Not one of Steam's art kinds")
    folder = DATA / "cache" / "steam-art"
    path = folder / f"{appid}-{kind}.jpg"
    missing = folder / f"{appid}-{kind}.none"
    if not path.exists():
        if missing.exists() and _now() - missing.stat().st_mtime < 7 * 86400:
            raise HTTPException(404, "Steam has no such art")
        try:
            r = requests.get(STEAM_ART.format(appid=appid, kind=kind), timeout=15, allow_redirects=False)
        except requests.RequestException:
            raise HTTPException(502, "Steam's image server isn't answering")
        folder.mkdir(parents=True, exist_ok=True)
        if r.status_code == 404:
            missing.touch()  # asked again in a week
            raise HTTPException(404, "Steam has no such art")
        if r.status_code != 200 or not r.content.startswith(b"\xff\xd8"):
            raise HTTPException(502, "Steam's image server isn't answering")
        tmp = path.with_suffix(f".{os.getpid()}.part")
        tmp.write_bytes(r.content)
        tmp.replace(path)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


# --- retro games: ROMs from the GAMES folder, played in Parlor with EmulatorJS ------------------------------------------

GAMES = Path(os.environ.get("GAMES_DIR", "/data/games"))
CARTS = {"zip", "7z"}  # EmulatorJS opens these for any cartridge system
# a folder under GAMES/roms -> its name, EmulatorJS's system, the files it plays
SYSTEMS = {
    "nes": ("NES", "nes", {"nes", "fds", "unf", "unif"} | CARTS), "snes": ("Super Nintendo", "snes", {"smc", "sfc", "fig", "swc", "bs"} | CARTS),
    "n64": ("Nintendo 64", "n64", {"n64", "z64", "v64"} | CARTS), "gb": ("Game Boy", "gb", {"gb"} | CARTS),
    "gbc": ("Game Boy Color", "gb", {"gbc", "gb"} | CARTS), "gba": ("Game Boy Advance", "gba", {"gba"} | CARTS),
    "nds": ("Nintendo DS", "nds", {"nds"} | CARTS), "vb": ("Virtual Boy", "vb", {"vb", "vboy"} | CARTS),
    "genesis": ("Mega Drive", "segaMD", {"md", "gen", "smd", "bin"} | CARTS), "megadrive": ("Mega Drive", "segaMD", {"md", "gen", "smd", "bin"} | CARTS),
    "sms": ("Master System", "segaMS", {"sms"} | CARTS), "gamegear": ("Game Gear", "segaGG", {"gg"} | CARTS),
    "32x": ("32X", "sega32x", {"32x"} | CARTS), "segacd": ("Mega-CD", "segaCD", {"chd", "iso"}),
    "psx": ("PlayStation", "psx", {"chd", "pbp"}), "pce": ("PC Engine", "pce", {"pce"} | CARTS),
    "atari2600": ("Atari 2600", "atari2600", {"a26", "bin"} | CARTS), "atari7800": ("Atari 7800", "atari7800", {"a78"} | CARTS),
    "lynx": ("Lynx", "lynx", {"lnx"} | CARTS), "ngp": ("Neo Geo Pocket", "ngp", {"ngp", "ngc"} | CARTS),
    "ws": ("WonderSwan", "ws", {"ws", "wsc"} | CARTS), "coleco": ("ColecoVision", "coleco", {"col"} | CARTS),
    "arcade": ("Arcade", "arcade", {"zip"}), "c64": ("Commodore 64", "c64", {"d64", "t64", "prg", "crt"} | CARTS),
    "dos": ("DOS", "dos", {"zip", "jsdos"}), "psp": ("PSP", "psp", {"iso", "cso"}),
}
THREADED = {"dos", "psp"}  # their cores need threads, so their play page needs cross-origin isolation
ARTS = (".png", ".jpg", ".jpeg", ".webp")
SLOTS = {"sram", "state"}  # the game's own save, and the emulator's save state
SAVE_MAX = 64 << 20


def _system_dir(system: str) -> Path:
    """GAMES/roms/<system>, if it's a system and really inside the folder (not a link out of it)"""
    if system not in SYSTEMS:
        raise HTTPException(404, "No such system")
    root = (GAMES / "roms").resolve()
    d = (GAMES / "roms" / system).resolve()
    if d.parent != root or not d.is_dir():
        raise HTTPException(404, "No such system")
    return d


def _inside(system: str, name: str) -> Optional[Path]:
    """a file right inside the system's folder by this name: nothing hidden, no separators, no link out"""
    d = _system_dir(system)
    if not name or name.startswith(".") or any(c in name for c in "/\\\0"):
        return None
    f = (d / name).resolve()
    return f if f.parent == d and f.is_file() else None


def _rom(system: str, name: str) -> Path:
    """a game's file: one its system plays"""
    f = _inside(system, name)
    if not f or f.suffix[1:].lower() not in SYSTEMS[system][2]:
        raise HTTPException(404, "No such game")
    return f


def _art(f: Path) -> Optional[Path]:
    return next((a for a in (f.with_suffix(x) for x in ARTS) if a.is_file() and a.resolve().parent == f.parent), None)


@router.get("/retro")
def retro(_=Depends(me)):
    """the games in GAMES/roms, by system"""
    out = []
    for system, (label, _core, exts) in sorted(SYSTEMS.items()):
        try:
            d = _system_dir(system)
        except HTTPException:
            continue
        found = []
        for f in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if f.name.startswith(".") or f.suffix[1:].lower() not in exts:
                continue
            try:
                _rom(system, f.name)
            except HTTPException:
                continue  # a link out, or not a file
            art = _art(d / f.name)
            found.append({"name": f.stem, "file": f.name, "size": f.stat().st_size,
                          "art": f"/api/games/retro/art/{system}/{quote(art.name)}" if art else None,
                          "play": f"/api/games/play/{system}/{quote(f.name)}"})
        if found:
            out.append({"id": system, "name": label, "games": found})
    return {"systems": out, "folder": str(GAMES / "roms")}


@router.get("/retro/rom/{system}/{name}")
def rom(system: str, name: str, request: Request, _=Depends(me)):
    return send_file(_rom(system, name), request, "application/octet-stream")  # with byte ranges


@router.get("/retro/art/{system}/{name}")
def rom_art(system: str, name: str, _=Depends(me)):
    f = _inside(system, name)
    if not f or f.suffix.lower() not in ARTS:
        raise HTTPException(404, "No such picture")
    return FileResponse(f, headers={"Cache-Control": "private, max-age=86400"})


def _save_path(user: dict, system: str, name: str, slot: str) -> Path:
    if slot not in SLOTS:
        raise HTTPException(422, "A save is sram (the game's own) or state (the emulator's)")
    _rom(system, name)
    key = hashlib.sha256(f"{system}/{name}".encode()).hexdigest()[:32]  # no names from the client on disk
    return DATA / "games-saves" / str(int(user["id"])) / f"{key}.{slot}"


@router.get("/retro/save/{system}/{name}/{slot}")
def get_save(system: str, name: str, slot: str, user=Depends(me)):
    path = _save_path(user, system, name, slot)
    if not path.is_file():
        raise HTTPException(404, "Nothing saved yet")
    return Response(vault.read(path), media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@router.put("/retro/save/{system}/{name}/{slot}")
async def put_save(system: str, name: str, slot: str, request: Request, user=Depends(me)):
    path = await anyio.to_thread.run_sync(_save_path, user, system, name, slot)
    if int(request.headers.get("content-length") or 0) > SAVE_MAX:
        raise HTTPException(413, "That save is too big")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > SAVE_MAX:
            raise HTTPException(413, "That save is too big")
    path.parent.mkdir(parents=True, exist_ok=True)
    await anyio.to_thread.run_sync(vault.write, path, bytes(data))  # sealed, like everything Parlor keeps
    return {"ok": True, "size": len(data)}


# EmulatorJS itself: each file fetched once from its CDN, kept only if it matches the sha256 pinned in emulatorjs.json
_EJS = json.loads((Path(__file__).parent / "emulatorjs.json").read_text())
EJS_CDN, EJS_FILES = _EJS["source"], _EJS["files"]


@router.get("/emulator/{path:path}")
def emulator(path: str, _=Depends(me)):
    want = EJS_FILES.get(path)
    if not want:
        raise HTTPException(404, "Not part of the emulator")
    f = DATA / "cache" / "emulatorjs" / _EJS["version"] / path
    if not f.is_file():
        try:
            r = requests.get(EJS_CDN + path, timeout=(10, 120), allow_redirects=False)
        except requests.RequestException:
            raise HTTPException(502, "The emulator's download server isn't answering")
        if r.status_code != 200 or hashlib.sha256(r.content).hexdigest() != want:
            log.warning("emulator file %s: %s", path, r.status_code if r.status_code != 200 else "sha256 doesn't match the pinned one")
            raise HTTPException(502, "The emulator's file didn't match the one Parlor expects, so it wasn't used")
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.{secrets.token_hex(4)}.part")
        tmp.write_bytes(r.content)
        tmp.replace(f)
    kind = "application/wasm" if path.endswith(".wasm") else None
    return FileResponse(f, media_type=kind, headers={"Cache-Control": "private, max-age=604800"})


PLAY = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Parlor</title><style>html,body{{margin:0;height:100%;background:#05060a;color:#eee;font:15px system-ui,sans-serif}}
#game{{width:100%;height:100%}}.back{{position:fixed;top:10px;left:10px;z-index:9;color:#eee;background:#0009;padding:6px 12px;border-radius:999px;text-decoration:none}}</style>
</head><body><a class="back" href="/#/games/retro">← Games</a><div id="game"></div>
<script type="application/json" id="retro">{config}</script><script src="/js/retro.js"></script>
<script src="/api/games/emulator/loader.js"></script></body></html>"""


@router.get("/play/{system}/{name}", include_in_schema=False)
def play(system: str, name: str, _=Depends(me)):
    """the game's own page: EmulatorJS with this person's saves; cross-origin isolated only when its core needs threads"""
    f = _rom(system, name)
    config = {"core": SYSTEMS[system][1], "threads": system in THREADED, "name": f.stem,
              "rom": f"/api/games/retro/rom/{system}/{quote(f.name)}", "save": f"/api/games/retro/save/{system}/{quote(f.name)}"}
    body = PLAY.format(title=html.escape(f.stem), config=json.dumps(config).replace("<", "\\u003c"))
    headers = {"Cache-Control": "no-store"}
    if system in THREADED:
        headers |= {"Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp"}
    return HTMLResponse(body, headers=headers)


# --- streaming from a gaming PC: Moonlight Web (moonlight-web-stream) behind Parlor's sign-in -------------------------
# The bridge is an opt-in compose profile on Parlor's own network, its port never published. Parlor passes its pages,
# API and WebSocket on at /games/stream/, saying who's asking in a header the bridge trusts (so its own login never
# shows); the picture goes from the bridge straight to the browser over WebRTC, on the bridge's published UDP ports.

page = APIRouter()  # /games/stream/: the bridge's own page, outside /api (its page sends no X-Requested-With)
STREAM = os.environ.get("GAMES_STREAM_URL", "")  # e.g. http://moonlight-web:8080; empty: the profile is off
PREFIX = "/games/stream"  # the bridge runs with PATH_PREFIX set to this
WHO = "X-Parlor-User"
FORWARD = ("content-type", "accept", "accept-language", "if-none-match", "if-modified-since")
BACK = ("content-type", "cache-control", "etag", "last-modified", "location")


def _who(user: dict) -> str:
    return f"parlor-{user['id']}"  # stable through renames: the bridge keeps each person's PCs under it


def _same_site(headers, host: str) -> bool:
    origin = headers.get("origin")
    return bool(origin and urlparse(origin).netloc == host) or headers.get("sec-fetch-site") == "same-origin"


async def _person(cookies, path: str):
    """who this is, if they may stream: signed in, with Games open to them, and the bridge set up for them"""
    user = await anyio.to_thread.run_sync(core.session_user, cookies.get(core.COOKIE) or cookies.get(core.OLD_COOKIE) or "")
    if not user:
        raise HTTPException(401, "Sign in first")
    if lacking := await anyio.to_thread.run_sync(acl.allowed, user, f"{PREFIX}/{path}"):
        raise HTTPException(403, f"{lacking} isn't open to your account; ask an admin")
    if not STREAM:
        raise HTTPException(409, "Streaming from a PC is off: an admin turns on the games-stream profile (docs/games.md)")
    if not user["admin"] and setting("games_stream_admin") != "1":
        raise HTTPException(409, "An admin opens Games → Play from your PC once first, to set it up")
    if path.startswith("/") or any(c in path for c in "\\%#@?") or ".." in path.split("/"):
        raise HTTPException(404, "No such page")
    return user


@page.get(PREFIX, include_in_schema=False)
def to_stream():
    return RedirectResponse(PREFIX + "/", 307)


@page.api_route(PREFIX + "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def stream_http(path: str, request: Request):
    user = await _person(request.cookies, path)
    if request.method != "GET" and not _same_site(request.headers, request.headers.get("host", "")):
        raise HTTPException(403, "Not from this site")
    if int(request.headers.get("content-length") or 0) > 1_000_000:
        raise HTTPException(413, "Too big")
    body = bytearray()
    async for chunk in request.stream():  # its API takes small JSON: never hold more than that
        body += chunk
        if len(body) > 1_000_000:
            raise HTTPException(413, "Too big")
    body = bytes(body)
    headers = {**{k: v for k, v in request.headers.items() if k.lower() in FORWARD}, WHO: _who(user), "Accept-Encoding": "identity"}
    try:  # streamed: pairing answers with the PIN at once, then waits (minutes) for it to be entered on the PC
        r = await anyio.to_thread.run_sync(lambda: requests.request(
            request.method, f"{STREAM}{PREFIX}/{path}", params=list(request.query_params.multi_items()), data=body or None,
            headers=headers, timeout=(10, 600), allow_redirects=False, stream=True))
    except requests.RequestException:
        raise HTTPException(502, "The streaming bridge isn't answering")
    if user["admin"] and r.status_code < 300 and setting("games_stream_admin") != "1":
        set_setting("games_stream_admin", "1")  # the bridge made its first visitor its admin: an admin here
    out = {k: v for k, v in r.headers.items() if k.lower() in BACK}  # never its cookies: who's asking is Parlor's to say
    return StreamingResponse(r.iter_content(None), status_code=r.status_code, headers=out, background=BackgroundTask(r.close))


@page.websocket(PREFIX + "/{path:path}")
async def stream_ws(path: str, ws: WebSocket):
    """the bridge's WebSocket (the stream's signalling and input): checked here, as the sign-in guard sees only HTTP"""
    try:
        if not security.is_open(f"{PREFIX}/{path}"):
            raise HTTPException(423, "Parlor is locked")
        user = await _person(ws.cookies, path)
        if not _same_site(ws.headers, ws.headers.get("host", "")):
            raise HTTPException(403, "Not from this site")
    except HTTPException:
        await ws.close(code=4403)
        return
    url = f"{STREAM.replace('http', 'ws', 1)}{PREFIX}/{path}" + (f"?{ws.url.query}" if ws.url.query else "")
    try:
        upstream = await websockets.connect(url, additional_headers={WHO: _who(user)}, max_size=None, open_timeout=10)
    except (OSError, websockets.WebSocketException):
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=upstream.subprotocol)

    async def down():
        async for msg in upstream:
            await (ws.send_bytes(msg) if isinstance(msg, bytes) else ws.send_text(msg))

    async def up():
        while (msg := await ws.receive())["type"] != "websocket.disconnect":
            await upstream.send(msg["bytes"] if msg.get("bytes") is not None else msg["text"])

    async def still():  # a stream runs for hours: signing out, or losing Games, ends it
        while True:
            await anyio.sleep(30)
            try:
                await _person(ws.cookies, path)
            except HTTPException:
                return

    async with anyio.create_task_group() as tg:
        async def pump(f):
            try:
                await f()
            except Exception:  # either side gone: close both
                pass
            tg.cancel_scope.cancel()
        tg.start_soon(pump, down)
        tg.start_soon(pump, up)
        tg.start_soon(pump, still)
    await upstream.close()
    try:
        await ws.close()
    except RuntimeError:  # already closed
        pass
