"""Live streams: YouTube lives and Twitch, played in Parlor's own player. Parlor fetches every playlist and segment
itself (the browser never talks to YouTube or Twitch), rewriting each address to a signed one of its own, so it isn't
an open proxy. Twitch's stitched ads are taken out of the playlist on the way (marked as gaps: the player holds on
the last picture until the stream is back) and never reach the browser. Twitch needs no account: the public web
client's anonymous playback tokens, its GraphQL API for channels, videos and clips, and its anonymous chat."""
import asyncio
import concurrent.futures
import hashlib
import hmac
import html
import json
import logging
import random
import re
import secrets
import time
from typing import Optional
from urllib.parse import quote, urljoin, urlparse

import requests
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import yt
from core import db, me, rows

log = logging.getLogger("parlor.live")
router = APIRouter(prefix="/api/live")
_KEY = secrets.token_bytes(32)  # signs the addresses Parlor hands out; a restart makes old ones stale, which players refetch
HOSTS = (".googlevideo.com", ".ttvnw.net", ".live-video.net", ".jtvnw.net", ".cloudfront.net", ".twitch.tv", ".twitchcdn.net")  # what Parlor fetches for a player
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
TW_GQL, TW_USHER, TW_CHAT = "https://gql.twitch.tv/gql", "https://usher.ttvnw.net", "wss://irc-ws.chat.twitch.tv:443"
TW_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"  # Twitch's own web client id: anonymous playback, as its site does
LOGIN = re.compile(r"[a-z0-9_]{2,25}")
_now = time.time


def _sign(url: str) -> str:
    return hmac.new(_KEY, url.encode(), hashlib.sha256).hexdigest()[:32]


def via(kind: str, url: str) -> str:
    """Parlor's own address for a playlist (pl) or a segment, picture or clip (seg)"""
    return f"/api/live/{kind}?u={quote(url, safe='')}&s={_sign(url)}"


def _checked(u: str, s: str) -> str:
    host = urlparse(u).hostname or ""
    if not (s or "").isascii() or not hmac.compare_digest(_sign(u), s or "") or urlparse(u).scheme != "https" or not host.endswith(HOSTS):
        raise HTTPException(404, "Not an address Parlor handed out")
    return u


def rewrite(text: str, base: str) -> str:
    """A playlist with every address through Parlor. Twitch's: its ad segments (any whose title isn't "live") become
    gaps, its ad markers and prefetch hints go, so an ad is never fetched and never shown."""
    twitch, out, ad = (urlparse(base).hostname or "").endswith((".ttvnw.net", ".live-video.net")), [], False
    for line in text.splitlines():
        if twitch and (line.startswith("#EXT-X-TWITCH-PREFETCH") or (line.startswith("#EXT-X-DATERANGE") and ("twitch-stitched-ad" in line or "X-TV-TWITCH-AD" in line))):
            continue
        if line.startswith("#EXTINF"):
            ad = twitch and not line.rstrip().endswith(",live")
        elif line and not line.startswith("#"):
            url = urljoin(base, line.strip())
            if ad:  # a gap, at an address of Parlor's own that has nothing: a player that skips gaps never asks, one that doesn't gets no ad
                out += ["#EXT-X-GAP", "/api/live/gap.ts"]
                ad = False
                continue
            line = via("pl" if urlparse(url).path.endswith(".m3u8") else "seg", url)
        elif 'URI="' in line:
            line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{via("pl" if ".m3u8" in m[1] else "seg", urljoin(base, m[1]))}"', line)
        out.append(line)
    text = "\n".join(out) + "\n"
    return re.sub(r"#EXT-X-VERSION:\d+", "#EXT-X-VERSION:8", text) if "#EXT-X-GAP" in text else text  # gaps are version 8


def _get(url: str, **kw) -> requests.Response:
    try:
        return requests.get(url, headers={"User-Agent": UA, **kw.pop("headers", {})}, timeout=kw.pop("timeout", 15), allow_redirects=False, **kw)
    except requests.RequestException:
        raise HTTPException(502, "The stream's server isn't answering")


def _m3u8(text: str, base: str) -> Response:
    return Response(rewrite(text, base), media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})


def _playlist(url: str) -> Response:
    r = _get(url)
    if r.status_code != 200:
        raise HTTPException(404 if r.status_code in (403, 404) else 502, "The stream isn't there any more")
    return _m3u8(r.text, url)


@router.get("/gap.ts", include_in_schema=False)
def gap():
    raise HTTPException(404, "An ad break: nothing here")


@router.get("/pl")
def playlist(u: str, s: str, _=Depends(me)):
    return _playlist(_checked(u, s))


@router.get("/seg")
def segment(u: str, s: str, request: Request, _=Depends(me)):
    """a segment, a picture or a clip's file, byte ranges passed on"""
    url = _checked(u, s)
    r = _get(url, headers={"Range": request.headers["range"]} if "range" in request.headers else {}, stream=True, timeout=(10, 60))
    if r.status_code not in (200, 206):
        r.close()
        raise HTTPException(404 if r.status_code in (403, 404, 410) else 502, "That piece of the stream isn't there")
    keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}
    path = urlparse(url).path.lower()
    kind = "video/mp4" if path.endswith(".mp4") else "video/mp2t" if path.endswith(".ts") else None
    if kind and "octet-stream" in r.headers.get("content-type", "octet-stream"):
        keep["Content-Type"] = kind  # Twitch's clip files say "binary/octet-stream": what they are, so every player takes them
    return StreamingResponse(r.iter_content(64 * 1024), status_code=r.status_code, headers={**keep, "Cache-Control": "private, max-age=60"},
                             background=BackgroundTask(r.close))


# --- YouTube lives --------------------------------------------------------------------------------------------------

@router.get("/yt/{vid}.m3u8")
def youtube(vid: str, user=Depends(me)):
    """a YouTube live's playlist (its last hour or so, for going back), through Parlor"""
    if not re.fullmatch(r"[\w-]{11}", vid):
        raise HTTPException(404, "No such video")
    for fresh in (False, True):  # its address lasts hours; a stale one gets one fresh look
        info = yt.video_info(vid, user["id"], fresh=fresh)
        if info.get("live_status") != "is_live":
            if fresh:
                raise HTTPException(409, "This live stream hasn't started yet" if info.get("live_status") == "is_upcoming" else "This isn't live (any more)")
            continue  # what was kept may be from before it started
        manifest = next((f["manifest_url"] for f in info.get("formats") or [] if f.get("manifest_url")), None)
        if not manifest:
            raise HTTPException(502, "YouTube gave no live playlist")
        r = _get(manifest)
        if r.status_code == 200:
            return _m3u8(r.text, manifest)
    raise HTTPException(502, "YouTube's live playlist isn't answering")


# --- Twitch ---------------------------------------------------------------------------------------------------------

def gql(query: str, **variables) -> dict:
    try:
        r = requests.post(TW_GQL, headers={"Client-ID": TW_CLIENT, "User-Agent": UA}, json={"query": query, "variables": variables}, timeout=15)
        data = r.json()
    except (requests.RequestException, ValueError):
        raise HTTPException(502, "Twitch isn't answering")
    if r.status_code != 200 or data.get("errors"):
        log.warning("twitch: %s %s", r.status_code, str(data.get("errors"))[:200])
        raise HTTPException(502, "Twitch didn't answer as expected")
    return data.get("data") or {}


def _login(login: str) -> str:
    """a channel's name from its name, @name, or any twitch.tv address of it"""
    text = (login or "").strip().lower()
    if "twitch.tv" in text:
        u = urlparse(text if "//" in text else "https://" + text)
        text = (u.path.strip("/").split("/") or [""])[0] if (u.hostname or "").endswith("twitch.tv") else ""
    login = text.strip("@ /")
    if not LOGIN.fullmatch(login):
        raise HTTPException(422, "A Twitch channel is its name: letters, digits and _")
    return login


TOKEN = """query($login: String!, $vod: ID!, $isLive: Boolean!, $isVod: Boolean!, $type: String!) {
  streamPlaybackAccessToken(channelName: $login, params: {platform: "web", playerBackend: "mediaplayer", playerType: $type}) @include(if: $isLive) { value signature }
  videoPlaybackAccessToken(id: $vod, params: {platform: "web", playerBackend: "mediaplayer", playerType: $type}) @include(if: $isVod) { value signature } }"""


def _usher(path: str, token: dict) -> str:
    return (f"{TW_USHER}/{path}?sig={token['signature']}&token={quote(token['value'], safe='')}&allow_source=true&allow_audio_only=true"
            f"&p={random.randint(1, 9_999_999)}&player=twitchweb&fast_bread=true&playlist_include_framerate=true")


@router.get("/tw/{login}.m3u8")
def twitch_live(login: str, _=Depends(me)):
    login = _login(login)
    token = gql(TOKEN, login=login, vod="", isLive=True, isVod=False, type="embed").get("streamPlaybackAccessToken")
    if not token:
        raise HTTPException(404, "No such Twitch channel")
    r = _get(_usher(f"api/channel/hls/{login}.m3u8", token))
    if r.status_code == 404:
        raise HTTPException(409, f"{login} isn't live right now")
    if r.status_code != 200:
        raise HTTPException(502, "Twitch's stream server isn't answering")
    return _m3u8(r.text, r.url)


@router.get("/tw/vod/{vod}.m3u8")
def twitch_vod(vod: str, _=Depends(me)):
    if not vod.isdigit() or len(vod) > 20:
        raise HTTPException(404, "No such broadcast")
    token = gql(TOKEN, login="", vod=vod, isLive=False, isVod=True, type="embed").get("videoPlaybackAccessToken")
    if not token:
        raise HTTPException(404, "No such broadcast")
    r = _get(_usher(f"vod/{vod}.m3u8", token))
    if r.status_code != 200:
        raise HTTPException(404 if r.status_code in (403, 404) else 502, "This broadcast can't be played (deleted, or for subscribers)")
    return _m3u8(r.text, r.url)


@router.get("/tw/clip/{slug}")
def twitch_clip(slug: str, _=Depends(me)):
    if not re.fullmatch(r"[\w-]{1,100}", slug):
        raise HTTPException(404, "No such clip")
    clip = gql("""query($s: ID!) { clip(slug: $s) { title durationSeconds broadcaster { login displayName }
        playbackAccessToken(params: {platform: "web", playerType: "site"}) { signature value } videoQualities { quality sourceURL } } }""", s=slug).get("clip")
    if not clip or not clip.get("videoQualities"):
        raise HTTPException(404, "No such clip")
    best = max(clip["videoQualities"], key=lambda q: int(re.sub(r"\D", "", q.get("quality") or "0") or 0))
    tok = clip["playbackAccessToken"]
    url = f"{best['sourceURL']}?sig={tok['signature']}&token={quote(tok['value'], safe='')}"
    return {"title": clip["title"], "seconds": clip["durationSeconds"], "channel": clip["broadcaster"], "src": via("seg", url)}


CHANNEL = """query($login: String!) { user(login: $login) { login displayName description profileImageURL(width: 150)
  stream { title viewersCount createdAt game { name } previewImageURL(width: 640, height: 360) }
  videos(first: 24, sort: TIME) { edges { node { id title lengthSeconds publishedAt viewCount previewThumbnailURL(width: 320, height: 180) } } }
  clips(first: 24, criteria: {period: LAST_MONTH, sort: VIEWS_DESC}) { edges { node { slug title durationSeconds viewCount createdAt thumbnailURL } } } } }"""


def _img(url: Optional[str]) -> Optional[str]:
    return via("seg", url) if url and url.startswith("https://") and (urlparse(url).hostname or "").endswith(HOSTS) else None


def _stream(s: Optional[dict]) -> Optional[dict]:
    return s and {"title": s.get("title") or "", "viewers": s.get("viewersCount") or 0, "game": (s.get("game") or {}).get("name"),
                  "since": s.get("createdAt"), "preview": _img(s.get("previewImageURL"))}


@router.get("/tw/channel/{login}")
def twitch_channel(login: str, user=Depends(me)):
    login = _login(login)
    u = gql(CHANNEL, login=login).get("user")
    if not u:
        raise HTTPException(404, "No such Twitch channel")
    return {"login": u["login"], "name": u["displayName"], "about": u.get("description") or "", "avatar": _img(u.get("profileImageURL")),
            "live": _stream(u.get("stream")), "followed": bool(rows("SELECT 1 AS x FROM twitch_follows WHERE user_id = ? AND login = ?", user["id"], login)),
            "videos": [{"id": v["id"], "title": v["title"], "seconds": v["lengthSeconds"], "at": v["publishedAt"], "views": v["viewCount"],
                        "thumb": _img(v["previewThumbnailURL"])} for v in (e["node"] for e in u["videos"]["edges"])],
            "clips": [{"slug": c["slug"], "title": c["title"], "seconds": c["durationSeconds"], "views": c["viewCount"], "at": c["createdAt"],
                       "thumb": _img(c["thumbnailURL"])} for c in (e["node"] for e in u["clips"]["edges"])]}


USERS = """query($l: [String!]) { users(logins: $l) { login displayName profileImageURL(width: 70)
  stream { title viewersCount createdAt game { name } previewImageURL(width: 440, height: 248) } } }"""
_live_cache: dict = {}  # frozenset of logins -> (when, answer): who's live, for a minute


def _channels(logins: list) -> list:
    key = frozenset(logins)
    hit = _live_cache.get(key)
    if hit and _now() - hit[0] < 60:
        return hit[1]
    found = []
    for i in range(0, len(logins), 100):
        found += [u for u in gql(USERS, l=logins[i:i + 100]).get("users") or [] if u]
    out = [{"login": u["login"], "name": u["displayName"], "avatar": _img(u.get("profileImageURL")), "live": _stream(u.get("stream"))} for u in found]
    out.sort(key=lambda c: (not c["live"], -(c["live"] or {}).get("viewers", 0), c["name"].lower()))
    if len(_live_cache) > 500:
        _live_cache.clear()
    _live_cache[key] = (_now(), out)
    return out


@router.get("/tw/follows")
def follows(user=Depends(me)):
    logins = [r["login"] for r in rows("SELECT login FROM twitch_follows WHERE user_id = ? ORDER BY added", user["id"])]
    return {"channels": _channels(logins) if logins else []}


class Follow(BaseModel):
    login: str = Field(max_length=60)


@router.post("/tw/follows")
def follow(body: Follow, user=Depends(me)):
    login = _login(body.login)
    if not (gql(USERS, l=[login]).get("users") or [None])[0]:
        raise HTTPException(404, f"There's no Twitch channel called {login}")
    db().execute("INSERT OR IGNORE INTO twitch_follows(user_id, login, added) VALUES (?, ?, ?)", (user["id"], login, _now()))
    _live_cache.clear()
    return {"ok": True, "login": login}


@router.delete("/tw/follows/{login}")
def unfollow(login: str, user=Depends(me)):
    db().execute("DELETE FROM twitch_follows WHERE user_id = ? AND login = ?", (user["id"], _login(login)))
    _live_cache.clear()
    return {"ok": True}


class Import(BaseModel):
    logins: str = Field(max_length=20000)


@router.post("/tw/follows/import")
def import_follows(body: Import, user=Depends(me)):
    """many at once: names or twitch.tv addresses, separated by spaces, commas or lines (Twitch's own export works)"""
    wanted, bad = [], []
    for x in re.split(r"[\s,;]+", body.logins):
        if not x.strip() or x.lower() in ("login", "username", "channel") or x.startswith("#"):
            continue  # a header line from an export
        try:
            wanted.append(_login(x))
        except HTTPException:
            bad.append(x[:60])
    wanted = list(dict.fromkeys(wanted))[:500]
    real = {c["login"] for c in _channels(wanted)} if wanted else set()
    for login in wanted:
        if login in real:
            db().execute("INSERT OR IGNORE INTO twitch_follows(user_id, login, added) VALUES (?, ?, ?)", (user["id"], login, _now()))
    _live_cache.clear()
    return {"added": sorted(real), "unknown": sorted(set(wanted) - real) + bad}


@router.get("/tw/search")
def search(q: str, _=Depends(me)):
    q = q.strip()[:60]
    if len(q) < 2:
        return {"channels": []}
    found = gql("""query($q: String!) { searchFor(userQuery: $q, platform: "web") { channels { edges { item { ... on User {
        login displayName profileImageURL(width: 70) stream { title viewersCount createdAt game { name } previewImageURL(width: 440, height: 248) } } } } } } }""",
                q=q).get("searchFor") or {}
    items = [e["item"] for e in ((found.get("channels") or {}).get("edges") or []) if e.get("item", {}).get("login")]
    return {"channels": [{"login": u["login"], "name": u["displayName"], "avatar": _img(u.get("profileImageURL")), "live": _stream(u.get("stream"))} for u in items[:20]]}


# --- chat, read-only: Twitch's anonymous chat, relayed so the browser never talks to Twitch ----------------------------

def _irc(line: str) -> Optional[dict]:
    """a chat line from Twitch's IRC, or None for anything that isn't a message"""
    tags = {}
    if line.startswith("@"):
        raw, line = line[1:].split(" ", 1)
        tags = dict(t.split("=", 1) for t in raw.split(";") if "=" in t)
    m = re.match(r":(\w+)!\S+ PRIVMSG #\w+ :(.*)", line)
    if not m:
        return None
    color = tags.get("color") or ""
    return {"name": (tags.get("display-name") or m[1])[:40], "color": color if re.fullmatch(r"#[0-9A-Fa-f]{6}", color) else "", "text": m[2][:500]}


@router.get("/tw/{login}/chat")
async def chat(login: str, request: Request, _=Depends(me)):
    login = _login(login)

    async def relay():
        yield "retry: 3000\n\n"
        try:
            async with websockets.connect(TW_CHAT, open_timeout=10, max_size=2 ** 20) as irc:
                await irc.send("CAP REQ :twitch.tv/tags")
                await irc.send(f"NICK justinfan{random.randint(10000, 99999)}")  # Twitch's anonymous, read-only sign-in
                await irc.send(f"JOIN #{login}")
                yield f"data: {json.dumps({'joined': login})}\n\n"
                while True:
                    try:
                        raw = await asyncio.wait_for(irc.recv(), 20)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": still here\n\n"
                        continue
                    for line in str(raw).splitlines():
                        if line.startswith("PING"):
                            await irc.send("PONG" + line[4:])
                        elif msg := _irc(line):
                            yield f"data: {json.dumps(msg)}\n\n"
        except (OSError, websockets.WebSocketException):
            yield f"data: {json.dumps({'error': 'Chat is not answering; it tries again by itself'})}\n\n"

    return StreamingResponse(relay(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# --- what's live now: YouTube lives from your subscriptions, and your Twitch channels ----------------------------------
# A subscription's feed doesn't say what's live, so each channel's own /live page is asked: it points at the stream
# when there is one (live or scheduled). Kept five minutes per channel, asked eight at a time.

_yt_live: dict = {}  # channel id -> (when, card or None)
YT_LIVE = "https://www.youtube.com/channel/{cid}/live"


def yt_live(cid: str, name: str = "") -> Optional[dict]:
    hit = _yt_live.get(cid)
    if hit and _now() - hit[0] < 300:
        return hit[1]
    try:
        r = requests.get(YT_LIVE.format(cid=cid), headers={"User-Agent": UA, "Accept-Language": "en"}, cookies={"SOCS": "CAI"}, timeout=15)
        page = r.text if r.status_code == 200 else ""
    except requests.RequestException:
        return hit[1] if hit else None
    watch = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', page)
    card = None
    if watch:
        start = re.search(r'"scheduledStartTime":"(\d+)"', page)
        title = re.search(r'<meta name="title" content="([^"]*)"', page)
        card = {"id": watch[1], "title": html.unescape(title[1]) if title else "", "channel": name, "channel_id": cid,
                "live": "is_live" if '"isLive":true' in page else "is_upcoming", "published": int(start[1]) if start else None}
    if len(_yt_live) > 5000:
        _yt_live.clear()
    _yt_live[cid] = (_now(), card)
    return card


@router.get("/now")
def now(user=Depends(me)):
    subs = rows("SELECT channel_id, name FROM subs WHERE user_id = ?", user["id"])
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        cards = [c for c in pool.map(lambda s: yt_live(s["channel_id"], s["name"] or ""), subs) if c]
    try:
        tws = follows(user)["channels"]
    except HTTPException:
        tws = []
    return {"youtube": [v for v in cards if v["live"] == "is_live"],
            "upcoming": sorted((v for v in cards if v["live"] == "is_upcoming"), key=lambda v: v["published"] or 0),
            "twitch": [c for c in tws if c["live"]], "followed": len(tws)}
