"""Play a film or episode from the library on a TV set's own video player, over the home network: no browser on the
TV, no account with its maker, no internet. The phone picks and works it; the TV fetches the file from Parlor.

It is DLNA. Parlor asks the set's own (checked) address which player it has (a unicast SSDP search, so it crosses
VLANs where the multicast one doesn't), reads that player's description, and tells it what to play and when to pause,
seek and stop (UPnP AVTransport). Every address in the TV's answers must be the TV's own: nothing a TV says can send
Parlor anywhere else.

A TV has no sign-in, so what it fetches is a link with a long random name that stands for one film, for a few hours,
kept in memory only. The file comes through Parlor: the Jellyfin key never reaches the TV, and the link's name is
kept out of the access log.
"""
import html
import ipaddress
import logging
import re
import secrets
import socket
import threading
import time
from typing import Literal, Optional
from urllib.parse import urljoin, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import acl
import jf
import tv
import tvhw
from core import me
from tvhw import Failed, Refused

router = APIRouter(prefix="/api/tv")
links = APIRouter()  # /cast/<a link's name>/…: outside /api, where every call needs a session a TV has none of
log = logging.getLogger("parlor.cast")
SSDP_PORT = 1900  # the tests' stand-in player listens elsewhere
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
SEARCH = (AVT, "urn:schemas-upnp-org:device:MediaRenderer:1")
SEARCH_WAIT = 2  # seconds the TV has to say where its player is
SEEK_TRIES, SEEK_WAIT = 4, 1.5  # a TV refuses to seek until the film has started: ask again a few times
NO_START = 60  # seconds a TV has to start a film before Parlor says it didn't
LIFE = 12 * 3600  # seconds a link lasts: a long film, paused for dinner
KNOWN = 600  # seconds a player's address is remembered
SAVE_EVERY = 10  # seconds between saves of where the film got to
MAX_LINKS = 200
MAX_TEXT = 512 * 1024  # a description or an answer bigger than this isn't one
MIME = {"mkv": "video/x-matroska", "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo",
        "ts": "video/mp2t", "m2ts": "video/mp2t", "mpg": "video/mpeg", "mpeg": "video/mpeg", "webm": "video/webm", "wmv": "video/x-ms-wmv"}
DLNA = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"  # seek by bytes, the file as it is, streamed
NO_PLAYER = ("The TV didn't answer as a network player. It must be on, and the server must be able to reach it "
             "(UDP port 1900, then the port the TV names)")
NOTHING = "Nothing from Parlor is playing on that TV"
STATES = {"PLAYING": "playing", "PAUSED_PLAYBACK": "paused", "TRANSITIONING": "starting"}  # anything else: stopped

_links: dict = {}  # a link's name -> the film it stands for
_on: dict = {}  # a set's id -> the name of the link it is playing
_found: dict = {}  # a set's id -> (its address, its player's control address, when)
_lock = threading.Lock()


class _Mask(logging.Filter):
    """a link's name is all it takes to fetch the film: the access log gets the path without it"""
    link = re.compile(r"(/cast/)[\w-]+")

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(self.link.sub(r"\1-", a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_Mask())


# --- finding the TV's player --------------------------------------------------------------------

def _own(url: str, ip: str):
    """`url` if it is plain http at the TV's own address, else None"""
    try:
        u = urlsplit(url)
        if u.scheme == "http" and u.port and not u.username and ipaddress.ip_address(u.hostname or "") == ipaddress.ip_address(ip):
            return url
    except ValueError:
        pass
    return None


def _search(ip: str) -> list:
    """where the TV says its devices are described; only answers from the TV's own address are heard"""
    host = f"[{ip}]" if ":" in ip else ip
    found: list = []
    with socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for st in SEARCH:
            sock.sendto(f'M-SEARCH * HTTP/1.1\r\nHOST: {host}:{SSDP_PORT}\r\nMAN: "ssdp:discover"\r\nMX: 1\r\nST: {st}\r\n\r\n'.encode(), (ip, SSDP_PORT))
        end = time.monotonic() + SEARCH_WAIT
        while (left := end - time.monotonic()) > 0 and len(found) < 8:
            sock.settimeout(left)
            try:
                data, frm = sock.recvfrom(4096)
            except OSError:  # nothing more came (or the TV's port is closed)
                break
            m = re.search(rb"(?im)^location:[ \t]*(\S+)", data)
            if m and ipaddress.ip_address(frm[0].split("%")[0]) == ipaddress.ip_address(ip):
                loc = m[1].decode("ascii", "replace")
                if loc not in found:
                    found.append(loc)
                    end = min(end, time.monotonic() + 0.3)  # it answered: its other answers come right behind
    return found


def _text(r) -> str:
    # ponytail: the timeout is per read, so a device dripping bytes holds a thread up to MAX_TEXT reads; a wall-clock
    # deadline here and in tvhw if a hostile device on the LAN is ever in scope
    try:
        body = r.raw.read(MAX_TEXT + 1, decode_content=True)
    finally:
        r.close()
    if len(body) > MAX_TEXT:
        raise ValueError("too big")
    return body.decode("utf-8", "replace")


def _player(sid: int, ip: str) -> str:
    """the control address of the TV's AVTransport service, at the TV's own address"""
    hit = _found.get(sid)
    if hit and hit[0] == ip and time.monotonic() - hit[2] < KNOWN:
        return hit[1]
    for loc in _search(ip):
        if not _own(loc, ip):
            continue
        r = tvhw._http.get(loc, timeout=tvhw.TIMEOUT, allow_redirects=False, stream=True)
        if r.status_code != 200:
            r.close()
            continue
        text = _text(r)
        base = re.search(r"<URLBase>\s*([^<\s]+)\s*</URLBase>", text)
        for service in text.split("<service>")[1:]:  # no regex over text a device wrote: a split can't be made slow
            service = service.split("</service>")[0]
            m = re.search(r"<controlURL>\s*([^<\s]+)\s*</controlURL>", service)
            if m and ":AVTransport:" in service:
                if ctl := _own(urljoin(html.unescape(base[1]) if base else loc, html.unescape(m[1])), ip):
                    _found[sid] = (ip, ctl, time.monotonic())
                    return ctl
    raise Failed(NO_PLAYER)


def _soap(ctl: str, action: str, args: Optional[dict] = None) -> str:
    """one AVTransport action; the TV's answer as text"""
    inner = "".join(f"<{k}>{html.escape(str(v))}</{k}>" for k, v in (args or {}).items())
    body = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{AVT}"><InstanceID>0</InstanceID>{inner}</u:{action}></s:Body></s:Envelope>')
    r = tvhw._http.post(ctl, data=body.encode(), timeout=tvhw.TIMEOUT, allow_redirects=False, stream=True,
                        headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{AVT}#{action}"'})
    text = _text(r)
    if r.status_code != 200:
        code = re.search(r"<errorCode>\s*(\d{1,4})\s*</errorCode>", text)
        log.warning("a TV's player refused %s: HTTP %s, UPnP error %s", action, r.status_code, code[1] if code else "none")
        raise Failed("The TV's player refused that" + (f" (UPnP error {code[1]})" if code else ""))
    return text


def _tag(text: str, name: str) -> str:
    m = re.search(rf"<{name}>([^<]*)</{name}>", text)
    return html.unescape(m[1]).strip() if m else ""


def _secs(clock: str) -> float:
    """seconds from the player's H:MM:SS(.fff); 0 for anything else (NOT_IMPLEMENTED, empty)"""
    m = re.fullmatch(r"(\d+):(\d\d):(\d\d)(\.\d+)?", clock)
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + float(m[4] or 0) if m else 0.0


def _clock(secs: float) -> str:
    n = max(0, int(secs))
    return f"{n // 3600}:{n // 60 % 60:02d}:{n % 60:02d}"


# --- the link a TV fetches the film by ----------------------------------------------------------

def _link(token: str) -> dict:
    link = _links.get(token)
    if not link or link["until"] < time.time():
        with _lock:
            _links.pop(token, None)
        raise HTTPException(404, "No such film")
    return link


def _forget(sid: int, link: dict):
    """end this link; a newer film sent to the same set keeps its own"""
    with _lock:
        if _links.get(_on.get(sid, "")) is link:
            del _links[_on.pop(sid)]


def _playing(sid: int) -> dict:
    """the link this set is playing, or 409"""
    token = _on.get(sid, "")
    try:
        return _link(token)
    except HTTPException:
        _on.pop(sid, None)
        raise HTTPException(409, NOTHING) from None


def _save(link: dict, at: float, length: float, ended: bool = False):
    """where the film got to, on the Jellyfin user who sent it: at most every few seconds, and watched once it ends
    near its end. Best effort: the film plays on whatever Jellyfin says."""
    now = time.monotonic()
    if not ended and (at <= 0 or now - link["saved"] < SAVE_EVERY):
        return
    link["saved"] = now
    length = length or link["runtime"]
    save = {"Played": True, "PlaybackPositionTicks": 0} if ended and length and at >= 0.9 * length else {"PlaybackPositionTicks": round(at * jf.T)}
    if ended and not save.get("Played") and at <= 0:
        return
    try:
        jf.either("POST", f"/UserItems/{link['iid']}/UserData", f"/Users/{link['jf']}/Items/{link['iid']}/UserData",
                  params={"userId": link["jf"]}, json=save)
    except HTTPException as e:
        log.warning("couldn't save where a film on a TV got to: %s", e.detail)


@links.api_route("/cast/{token}/{name}", methods=["GET", "HEAD"], include_in_schema=False)
def film(token: str, name: str, request: Request):
    """the film a link stands for, with byte ranges, for a TV's player: no sign-in, so the link's name is the leave"""
    link = _link(token)
    headers = {"Accept-Encoding": "identity"}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    r = jf.fetch("GET", f"/Videos/{link['iid']}/stream", params={"static": "true", "mediaSourceId": link["source"]}, headers=headers,
                 stream=True, device=f"parlor-{link['uid']}")
    if r.status_code == 416:
        r.close()
        return Response(status_code=416, headers={"Content-Range": r.headers.get("Content-Range", "bytes */*")})
    if r.status_code not in (200, 206):
        jf.need(r)
    out = {"Cache-Control": "no-store", "Accept-Ranges": "bytes", "transferMode.dlna.org": "Streaming", "contentFeatures.dlna.org": DLNA}
    out.update({n: r.headers[n] for n in jf.PASS if n in r.headers})
    if request.method == "HEAD":
        r.close()
        return Response(status_code=r.status_code, media_type=link["mime"], headers=out)
    return StreamingResponse(r.iter_content(64 * 1024), status_code=r.status_code, media_type=link["mime"], headers=out,
                             background=BackgroundTask(r.close))


# --- the API: play it there, and the buttons while it plays --------------------------------------

class Play(BaseModel):
    id: str = Field(pattern=r"^[0-9a-fA-F]{32}$")
    start: Optional[float] = Field(None, ge=0, le=10**7)  # seconds; default is where you left off


class Do(BaseModel):
    do: Literal["play", "pause", "stop", "seek"]
    by: Optional[float] = Field(None, ge=-10**5, le=10**5)  # seek: seconds from where it is
    to: Optional[float] = Field(None, ge=0, le=10**7)  # seek: seconds from the start


def _title(i: dict) -> str:
    if i.get("Type") == "Episode" and i.get("SeriesName"):
        code = f"S{i['ParentIndexNumber']}:E{i['IndexNumber']} · " if i.get("ParentIndexNumber") is not None and i.get("IndexNumber") is not None else ""
        return f"{i['SeriesName']} · {code}{i.get('Name') or ''}"[:200]
    return str(i.get("Name") or "A film")[:200]


def _set(sid: int, user: dict) -> dict:
    s = tvhw._set(sid, user)
    if s["kind"] not in tvhw.PLAYS:
        raise Refused(f"{tvhw.KINDS[s['kind']]} can't play a film from Parlor in its own player")
    return s


@router.post("/hw/{sid}/play")
def play(sid: int, body: Play, request: Request, user=Depends(me)):
    tv._throttle(request, "hw")
    s = _set(sid, user)
    if lacking := acl.allowed(user, "/api/jf/items"):  # a person kept out of the Library can't play it on a TV either
        raise HTTPException(403, f"Your account can't use {lacking}")
    who = jf.who(user)["id"]
    i = jf.get_item(body.id, who)  # a 404 here: their Jellyfin user can't see it
    src = (i.get("MediaSources") or [{}])[0]
    if i.get("MediaType") != "Video" or not src.get("Id"):
        raise HTTPException(409, "Only a film, an episode or a video plays on a TV set")
    ext = str(src.get("Container") or "").split(",")[0].lower()
    ext = ext if re.fullmatch(r"[a-z0-9]{1,5}", ext) else "mp4"
    mime, title, token = MIME.get(ext, "video/mp4"), _title(i), secrets.token_urlsafe(32)
    url = f"{tvhw.parlor_address(request)}/cast/{token}/video.{ext}"
    at = jf.secs((i.get("UserData") or {}).get("PlaybackPositionTicks")) if body.start is None else body.start
    didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="1" parentID="0" restricted="1">'
            f'<dc:title>{html.escape(title)}</dc:title><upnp:class>object.item.videoItem</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime}:{DLNA}">{html.escape(url)}</res></item></DIDL-Lite>')
    with _lock:
        now = time.time()
        for old in [t for t, link in _links.items() if link["until"] < now] + ([_on[sid]] if sid in _on else []):
            _links.pop(old, None)
        if len(_links) >= MAX_LINKS:
            raise HTTPException(429, "Too many films are being sent to TVs: stop one first")
        link = _links[token] = {"iid": body.id, "source": src["Id"], "uid": user["id"], "jf": who, "sid": sid, "mime": mime, "title": title,
                         "runtime": jf.secs(src.get("RunTimeTicks") or i.get("RunTimeTicks")), "until": now + LIFE, "saved": 0.0, "seen": False, "sent": time.monotonic()}
        _on[sid] = token

    def go(ip):
        ctl = _player(sid, ip)
        _soap(ctl, "SetAVTransportURI", {"CurrentURI": url, "CurrentURIMetaData": didl})
        _soap(ctl, "Play", {"Speed": 1})
        for n in range(SEEK_TRIES if at > 5 else 0):  # from where you left off, once the TV lets it
            time.sleep(SEEK_WAIT)
            try:
                return _soap(ctl, "Seek", {"Unit": "REL_TIME", "Target": _clock(at)})
            except Failed:
                if n == SEEK_TRIES - 1:
                    log.warning("TV set %s wouldn't seek to where the film was left: it plays from the start", sid)

    try:
        tvhw._run(s, "", go, tvhw.TIMEOUT)
    except BaseException:
        _forget(sid, link)
        _found.pop(sid, None)  # its player may have moved: look again next time
        raise
    return {"ok": True, "title": title}


@router.get("/hw/{sid}/playing")
def playing(sid: int, user=Depends(me)):
    """what Parlor is playing on this set and where it got to; the film's place is saved from here"""
    s = _set(sid, user)
    if sid not in _on:
        return {"playing": None}
    link = _playing(sid)

    def go(ip):
        ctl = _player(sid, ip)
        return _tag(_soap(ctl, "GetTransportInfo"), "CurrentTransportState"), _soap(ctl, "GetPositionInfo")

    try:
        state, pos = tvhw._run(s, "", go, tvhw.TIMEOUT)
    except BaseException:
        _found.pop(sid, None)
        raise
    at, length = _secs(_tag(pos, "RelTime")), _secs(_tag(pos, "TrackDuration")) or link["runtime"]
    state = STATES.get(state, "stopped")
    if state == "stopped":
        if link["seen"]:  # it ended, or someone stopped it at the TV
            _save(link, link.get("at", 0), length, ended=True)
            _forget(sid, link)
            return {"playing": None}
        if time.monotonic() - link["sent"] > NO_START:
            _forget(sid, link)
            return {"playing": None, "said": "The TV didn't start the film: it may not play this kind of file"}
    else:
        link["seen"], link["at"] = True, at
        _save(link, at, length)
    return {"playing": {"title": link["title"], "state": state, "at": round(at), "length": round(length)}}


@router.post("/hw/{sid}/playing")
def control(sid: int, body: Do, request: Request, user=Depends(me)):
    tv._throttle(request, "hw")
    s = _set(sid, user)
    link = _playing(sid)

    def go(ip):
        ctl = _player(sid, ip)
        if body.do == "seek":
            if (to := body.to) is None:
                rel = _tag(_soap(ctl, "GetPositionInfo"), "RelTime")
                if ":" not in rel:  # NOT_IMPLEMENTED: skipping from 0:00 would rewind the film
                    raise Failed("The TV doesn't say where the film has got to: press along the line instead")
                to = _secs(rel) + (body.by or 0)
            _soap(ctl, "Seek", {"Unit": "REL_TIME", "Target": _clock(to)})
        else:
            _soap(ctl, body.do.capitalize(), {"Speed": 1} if body.do == "play" else None)

    tvhw._run(s, "", go, tvhw.TIMEOUT)
    if body.do == "stop":
        _save(link, link.get("at", 0), 0, ended=True)
        _forget(sid, link)
    return {"ok": True}
