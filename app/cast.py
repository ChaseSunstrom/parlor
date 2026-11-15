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
import json
import logging
import re
import secrets
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Literal, Optional
from urllib.parse import urljoin, urlsplit

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import acl
import jf
import yt
import tv
import tvhw
import core
from core import me
from tvhw import Failed, Refused

router = APIRouter(prefix="/api/tv")
links = APIRouter()  # /cast/<a link's name>/…: outside /api, where every call needs a session a TV has none of
log = logging.getLogger("parlor.cast")
PORTS = range(1024, 10000)  # where a TV that doesn't answer the search is looked for: an LG's player sits low (1326 on a C6)
STOP_WAIT = 45  # seconds a TV stuck loading takes to answer Stop (an LG C6: over 15)
SET_WAIT = 20  # seconds a TV gets to fetch a link and say yes (an LG C6 gives up on an address it can't reach after 5)
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
SOUND = {"aac", "ac3", "eac3", "mp3", "mp2"}  # sound a TV's own player plays; TrueHD, DTS and FLAC play silent on many (a real LG)
PICTURE = {"h264", "hevc", "mpeg2video", "mpeg4", "vc1"}  # AV1 is newer than many TVs
NO_PICTURE = {"DOVI", "DOVIInvalid"}  # Dolby Vision with no plain picture under it: black in a TV's network player (a real LG)
MAX_TEXT = 512 * 1024  # a description or an answer bigger than this isn't one
HEAD = 256 * 1024  # bytes of a Matroska film read for its track list (a few KB in, after its SeekHead and Info)
PLAYS = ("A_AAC", "A_AC3", "A_EAC3", "A_MPEG/L2", "A_MPEG/L3")  # Matroska's names for SOUND
EBML, SEGMENT, TRACKS, ENTRY, CLUSTER, CRC = 0x1A45DFA3, 0x18538067, 0x1654AE6B, 0xAE, 0x1F43B675, 0xBF
KIND, CODEC, DEFAULT, FORCED, LACING, LANG, LANG47, NAME, COMMENTARY = 0x83, 0x86, 0x88, 0x55AA, 0x9C, 0x22B59C, 0x22B59D, 0x536E, 0x55AF
MIME = {"mkv": "video/x-matroska", "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo",
        "ts": "video/mp2t", "m2ts": "video/mp2t", "mpg": "video/mpeg", "mpeg": "video/mpeg", "webm": "video/webm", "wmv": "video/x-ms-wmv"}
DLNA = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"  # seek by bytes, the file as it is, streamed
NO_PLAYER = ("The TV didn't answer as a network player. It must be on, and the server must be able to reach it "
             "(UDP port 1900 or TCP ports 1024-9999, then the port the TV names)")
NOTHING = "Nothing from Parlor is playing on that TV"
STATES = {"PLAYING": "playing", "PAUSED_PLAYBACK": "paused", "TRANSITIONING": "starting"}  # anything else: stopped
STALL = 25  # seconds a film may say it's playing without its time moving before Parlor sends it again (the stream was cut)

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


def _scan(ip: str) -> list:
    """the TV's open ports as addresses to ask for a description, for a TV that doesn't answer the search: an LG on
    another network segment than the server doesn't, and describes its player at the root of a port it picks"""
    def is_open(port):
        try:
            with socket.create_connection((ip, port), timeout=0.7):
                return port
        except OSError:
            return None

    host = f"[{ip}]" if ":" in ip else ip
    with ThreadPoolExecutor(200) as pool:
        return [f"http://{host}:{p}/" for p in pool.map(is_open, PORTS) if p][:40]


def _text(r, wait: float = 0) -> str:
    """a device's answer: at most MAX_TEXT bytes, read within `wait` seconds (a button's wait by default), so a device
    sending it a byte at a time can't hold a thread and the set's lock for long. read1 returns what has come so far."""
    end, body = time.monotonic() + (wait or tvhw.TIMEOUT), bytearray()
    try:
        while chunk := r.raw.read1(64 * 1024, decode_content=True):
            body += chunk
            if len(body) > MAX_TEXT:
                raise ValueError("too big")
            if time.monotonic() > end:
                raise ValueError("too slow")  # a scan skips it like any non-answer; a press says the TV didn't answer
    finally:
        r.close()
    return body.decode("utf-8", "replace")


def _player(sid: int, ip: str) -> str:
    """the control address of the TV's AVTransport service, at the TV's own address"""
    hit = _found.get(sid)
    if hit and hit[0] == ip and time.monotonic() - hit[2] < KNOWN:
        return hit[1]
    for loc in _search(ip) or _scan(ip):
        if not _own(loc, ip):
            continue
        try:
            r = tvhw._http.get(loc, timeout=tvhw.TIMEOUT, allow_redirects=False, stream=True)
            if r.status_code != 200:
                r.close()
                continue
            text = _text(r)
        except (requests.RequestException, ValueError):  # a port that isn't a description (the scan meets many)
            continue
        base = re.search(r"<URLBase>\s*([^<\s]+)\s*</URLBase>", text)
        for service in text.split("<service>")[1:]:  # no regex over text a device wrote: a split can't be made slow
            service = service.split("</service>")[0]
            m = re.search(r"<controlURL>\s*([^<\s]+)\s*</controlURL>", service)
            if m and ":AVTransport:" in service:
                if ctl := _own(urljoin(html.unescape(base[1]) if base else loc, html.unescape(m[1])), ip):
                    _found[sid] = (ip, ctl, time.monotonic())
                    return ctl
    raise Failed(NO_PLAYER)


def _soap(ctl: str, action: str, args: Optional[dict] = None, wait: Optional[float] = None) -> str:
    """one AVTransport action; the TV's answer as text"""
    inner = "".join(f"<{k}>{html.escape(str(v))}</{k}>" for k, v in (args or {}).items())
    body = ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{AVT}"><InstanceID>0</InstanceID>{inner}</u:{action}></s:Body></s:Envelope>')
    r = tvhw._http.post(ctl, data=body.encode(), timeout=wait or tvhw.TIMEOUT, allow_redirects=False, stream=True,
                        headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{AVT}#{action}"'})
    text = _text(r, wait or tvhw.TIMEOUT)
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

_recalled = False


def _recall():
    """the links from before a restart, once: an update of Parlor doesn't cut off a film that is playing"""
    global _recalled
    with _lock:
        if _recalled:
            return
        db = core.db()
        db.execute("CREATE TABLE IF NOT EXISTS cast_links(token TEXT PRIMARY KEY, link TEXT NOT NULL, until REAL NOT NULL)")
        db.execute("DELETE FROM cast_links WHERE until < ?", (time.time(),))
        for r in core.rows("SELECT token, link FROM cast_links"):
            try:
                link = {**json.loads(r["link"]), "saved": 0.0, "sent": time.monotonic(), "moved": time.monotonic(), "woke": 0.0}
                sid = link["sid"]
            except (ValueError, KeyError, TypeError):  # one bad row mustn't stop every film
                _drop(r["token"])
                continue
            if _links.setdefault(r["token"], link) is link:
                _on.setdefault(sid, r["token"])
        _recalled = True


def _keep(token: str, link: dict):
    core.db().execute("INSERT OR REPLACE INTO cast_links(token, link, until) VALUES (?, ?, ?)",
                      (token, json.dumps({k: v for k, v in link.items() if k not in ("saved", "sent", "moved", "woke")}), link["until"]))


def _drop(*tokens):
    for token in tokens:
        core.db().execute("DELETE FROM cast_links WHERE token = ?", (token,))


def _link(token: str) -> dict:
    _recall()
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
            _drop(_on[sid])
            del _links[_on.pop(sid)]


def _playing(sid: int) -> dict:
    """the link this set is playing, or 409"""
    _recall()
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
    if link.get("yt"):  # a Watch video: its place is in Parlor's own history
        if at > 0:
            done = ended and length and at >= 0.9 * length
            yt.db().execute("INSERT INTO history(user_id, video_id, item, position, watched) VALUES (?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET "
                            "position = excluded.position, watched = excluded.watched", (link["uid"], link["yt"], link["card"], 0 if done else at, time.time()))
        return
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
    if link.get("yt"):
        return _video(link, request)
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
    body = r.iter_content(64 * 1024)
    if link.get("patch"):  # its track list, rearranged for a TV (_patch)
        m = re.match(r"bytes (\d+)-", r.headers.get("Content-Range", ""))
        if r.status_code == 200 or m:
            body = _over(body, int(m[1]) if m else 0, link["patch"])
    return StreamingResponse(body, status_code=r.status_code, media_type=link["mime"], headers=out, background=BackgroundTask(r.close))


def _video(link: dict, request: Request):
    """a Watch video: the downloaded file, or YouTube's one-file stream fetched range by range (yt.py's way), as one
    answer as long as the TV asked for: a TV's player doesn't come back for the next piece the way a browser does"""
    dlna = {"transferMode.dlna.org": "Streaming", "contentFeatures.dlna.org": DLNA}
    if link["path"]:
        path = yt.inside_videos(link["path"])
        if not path:
            raise HTTPException(404, "The file is gone")
        r = yt.send_file(path, request, link["mime"])
        r.headers.update({**dlna, "Cache-Control": "no-store"})
        return r
    vid, fmt, uid = link["yt"], link["fmt"], link["uid"]
    def first(start, end):
        f, r = yt._upstream(vid, fmt, start, end, uid)
        if r.status_code in (403, 410):  # YouTube's address for it expired: look the video up again once
            r.close()
            f, r = yt._upstream(vid, fmt, start, end, uid, fresh=True)
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", r.headers.get("content-range", ""))
        if r.status_code != 206 or not m or int(m[1]) != start:
            r.close()
            if r.status_code == 416:
                raise HTTPException(416, "Range not satisfiable", headers={"Content-Range": r.headers.get("content-range") or "bytes */0"})
            raise HTTPException(502, f"YouTube answered {r.status_code} for that video")
        return r, int(m[3])

    asked = request.headers.get("range")
    rng = yt.parse_range(asked, None)
    if asked and not rng and (tail := re.fullmatch(r"bytes=-(\d+)", asked.strip())):  # the last n bytes (a player reading an
        probe, size = first(0, 0)                                                        # MP4's index): the length comes first
        probe.close()
        rng = max(size - int(tail[1]), 0), None
    if not rng:
        asked = None  # a range this can't read: the whole video, said as such
    start, end = rng or (0, None)
    r, size = first(start, end)
    last = size - 1 if end is None else min(end, size - 1)
    out = {**dlna, "Cache-Control": "no-store", "Accept-Ranges": "bytes", "Content-Length": str(last - start + 1)}
    if asked:
        out["Content-Range"] = f"bytes {start}-{last}/{size}"
    if request.method == "HEAD":
        r.close()
        return Response(status_code=206 if asked else 200, media_type=link["mime"], headers=out)

    def body():
        piece, at = r, start
        try:
            while True:
                got = 0
                for chunk in piece.iter_content(64 * 1024):
                    got += len(chunk)
                    yield chunk
                at += got
                piece.close()
                if at > last or not got:
                    return
                _, piece = yt._upstream(vid, fmt, at, last, uid)
                if piece.status_code != 206:
                    return  # the TV sees a short answer and asks again from where it stopped
        finally:
            piece.close()

    return StreamingResponse(body(), status_code=206 if asked else 200, media_type=link["mime"], headers=out)


# --- the API: play it there, and the buttons while it plays --------------------------------------

class Play(BaseModel):
    id: str = Field(pattern=r"^([0-9a-fA-F]{32}|[A-Za-z0-9_-]{11})$")  # a Library item, or a Watch video
    kind: Literal["jf", "yt"] = "jf"
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


def _hard(src: dict, moved: str = "") -> str:
    """what to say when a film is sent whose sound or picture TVs' own players commonly can't play; '' when it is
    fine or Jellyfin doesn't say what is in it. The film still goes as it is: a TV plays files, and its remote picks
    the track (a stream converted on the way wouldn't start on a real LG)"""
    streams = src.get("MediaStreams") or []
    kind = lambda x: str(x.get("Codec") or "").lower()  # noqa: E731
    video = next((x for x in streams if x.get("Type") == "Video"), {})
    sounds = [x for x in streams if x.get("Type") == "Audio"]
    first = next((x for x in sounds if x.get("Index") == src.get("DefaultAudioStreamIndex")), sounds[0] if sounds else {})
    said = []
    if kind(first) and kind(first) not in SOUND:
        other = next((kind(x).upper() for x in sounds if kind(x) in SOUND), "")
        said.append(f"Its first sound track is {kind(first).upper()}, which many TVs play silent: " + (
            f"Parlor sends its {moved} track first instead; if the TV is still silent, choose that track with the TV's own remote" if moved
            else f"choose the {other} track with the TV's own remote" if other else "it has no other, so it may play without sound"))
    if video.get("VideoRangeType") in NO_PICTURE:
        said.append("Its picture is Dolby Vision only, which many TVs' own players show black")
    elif kind(video) and kind(video) not in PICTURE:
        said.append(f"Its picture is {kind(video).upper()}, which many TVs' own players can't show")
    return ". ".join(said)


def _vint(b: bytes, i: int, marker: bool = False) -> tuple:
    """a Matroska (EBML) number at b[i]: its value and its length; an id keeps its length marker"""
    n = 9 - b[i].bit_length()
    if not 1 <= n <= 8 or i + n > len(b):
        raise ValueError("not EBML")
    v = b[i] if marker else b[i] & (0xFF >> n)
    for x in b[i + 1:i + n]:
        v = v << 8 | x
    return v, n


def _kids(b: bytes, start: int, end: int):
    """(id, start, data start, end) of each element in b[start:end]; one of unknown size runs to `end`"""
    while start < end:
        eid, n = _vint(b, start, True)
        size, m = _vint(b, start + n)
        data = start + n + m
        stop = end if size == (1 << 7 * m) - 1 else data + size
        yield eid, start, data, stop
        start = stop


def _patch(head: bytes) -> tuple:
    """([[offset, hex]] to lay over a Matroska film as it is served, the sound track moved first) so a TV's own
    player, which starts the first default tracks, starts sound it can play and no subtitles. The first playable
    track (AC3, AAC…) in the same language that isn't a commentary moves ahead of a TrueHD or DTS one and is the
    default, and subtitles that aren't forced stop being default (a FlagLacing 0 becomes a FlagDefault 0, the same
    three bytes, when the flag was left out). Only same-size changes inside the track list: the length and every
    other byte and offset stay as they are. ([], '') when nothing needs changing or it isn't a header this reads."""
    try:
        return _rearranged(head)
    except (ValueError, IndexError, StopIteration, OverflowError):  # a header this doesn't read: the film as it is
        return [], ""


def _rearranged(head: bytes) -> tuple:
    """_patch's work; raises on a header it can't read"""
    top = _kids(head, 0, len(head))
    if next(top)[0] != EBML or (seg := next(top))[0] != SEGMENT:
        return [], ""
    tracks = next((x for x in _kids(head, seg[2], seg[3]) if x[0] in (TRACKS, CLUSTER)), None)
    if not tracks or tracks[0] != TRACKS or tracks[3] > len(head):
        return [], ""

    def tiled(start, end):  # the elements that fill b[start:end] exactly, as a player reads them
        out = list(_kids(head, start, end))
        if out and out[-1][3] != end:
            raise ValueError("an element overruns its parent")
        return out
    kids = tiled(tracks[2], tracks[3])
    fields = [{k: (s, d, e) for k, s, d, e in tiled(x[2], x[3])} if x[0] == ENTRY else {} for x in kids]
    if any(x[0] == CRC for x in kids) or any(CRC in f for f in fields):  # a checksum this would break: as it is
        return [], ""
    pieces = [bytearray(head[x[1]:x[3]]) for x in kids]

    def val(i, k, default=0):
        s, d, e = fields[i].get(k, (0, 0, 0))
        return int.from_bytes(head[d:e], "big") if k in fields[i] else default

    def text(i, k, default=""):
        s, d, e = fields[i].get(k, (0, 0, 0))
        return head[d:e].rstrip(b"\0").decode("utf-8", "replace") if k in fields[i] else default

    def flag(i, k, to):  # a flag that is there, set in place
        s, d, e = fields[i][k]
        if not 1 <= e - d <= 8:
            raise ValueError("not a flag")
        pieces[i][d - kids[i][1]:e - kids[i][1]] = (to).to_bytes(e - d, "big")

    lang = lambda i: text(i, LANG47) or text(i, LANG, "eng")  # noqa: E731
    plays = lambda i: text(i, CODEC).startswith(PLAYS)  # noqa: E731
    sounds = [i for i, f in enumerate(fields) if val(i, KIND) == 2]
    first = next((i for i in sounds if val(i, DEFAULT, 1)), None)  # None: every sound track says it isn't default
    order, moved = list(range(len(kids))), ""
    if sounds and (first is None or not plays(first)):
        like = None if first is None else lang(first)  # None: no track says it's the one, any language
        better = next((i for i in sounds if plays(i) and like in (None, lang(i)) and not val(i, COMMENTARY)
                       and "comment" not in text(i, NAME).lower()), None)
        if better is not None:
            if better != sounds[0]:
                order.remove(better)
                order.insert(order.index(sounds[0]), better)
            codec = text(better, CODEC)
            moved = {"A_MPEG/L3": "MP3", "A_MPEG/L2": "MP2"}.get(codec) or codec[2:].split("/")[0]
            for i in sounds:
                if DEFAULT in fields[i]:
                    flag(i, DEFAULT, int(i == better))
    for i, f in enumerate(fields):
        if val(i, KIND) == 17 and not val(i, FORCED) and val(i, DEFAULT, 1):
            if DEFAULT in f:
                flag(i, DEFAULT, 0)
            elif LACING in f:
                at = f[LACING][0] - kids[i][1]
                pieces[i][at] = DEFAULT
                flag(i, LACING, 0)
    new, old = b"".join(pieces[i] for i in order), head[tracks[2]:tracks[3]]
    if new == old:
        return [], ""
    a = next(i for i in range(len(new)) if new[i] != old[i])
    z = max(i for i in range(len(new)) if new[i] != old[i]) + 1
    return [[tracks[2] + a, new[a:z].hex()]], moved


def _head(iid: str, source: str, uid: int) -> bytes:
    """the first HEAD bytes of a film, or b'' when Jellyfin won't give them"""
    try:
        r = jf.fetch("GET", f"/Videos/{iid}/stream", params={"static": "true", "mediaSourceId": source}, stream=True,
                     headers={"Range": f"bytes=0-{HEAD - 1}", "Accept-Encoding": "identity"}, device=f"parlor-{uid}")
    except HTTPException:
        return b""
    try:
        out = bytearray()
        if r.status_code in (200, 206):
            for chunk in r.iter_content(64 * 1024):
                out += chunk
                if len(out) >= HEAD:
                    break
        return bytes(out[:HEAD])
    except requests.RequestException:
        return b""
    finally:
        r.close()


def _over(chunks, at: int, patch: list):
    """the film's bytes from offset `at`, with the patch laid over the ones it covers"""
    patch = [(off, bytes.fromhex(new)) for off, new in patch]
    last = max((off + len(new) for off, new in patch), default=0)
    for chunk in chunks:
        if at >= last:
            yield chunk
            continue
        end, chunk = at + len(chunk), bytearray(chunk)
        for off, new in patch:
            a, z = max(off, at), min(off + len(new), end)
            if a < z:
                chunk[a - at:z - at] = new[a - off:z - off]
        yield bytes(chunk)
        at = end


def _library(body: Play, user: dict):
    """a film, an episode or a video in the Jellyfin library: what its link needs, and where it was left"""
    if lacking := acl.allowed(user, "/api/jf/items"):  # a person kept out of the Library can't play it on a TV either
        raise HTTPException(403, f"Your account can't use {lacking}")
    if len(body.id) != 32:
        raise HTTPException(422, "Not a Library item")
    who = jf.who(user)["id"]
    i = jf.get_item(body.id, who)  # a 404 here: their Jellyfin user can't see it
    src = (i.get("MediaSources") or [{}])[0]
    if i.get("MediaType") != "Video" or not src.get("Id"):
        raise HTTPException(409, "Only a film, an episode or a video plays on a TV set")
    ext = str(src.get("Container") or "").split(",")[0].lower()
    ext = ext if re.fullmatch(r"[a-z0-9]{1,5}", ext) else "mp4"
    patch, moved = _patch(_head(body.id, src["Id"], user["id"])) if ext == "mkv" else ([], "")
    source = {"iid": body.id, "source": src["Id"], "jf": who, "runtime": jf.secs(src.get("RunTimeTicks") or i.get("RunTimeTicks")),
              "note": _hard(src, moved), "patch": patch}
    return source, ext, MIME.get(ext, "video/mp4"), _title(i), jf.secs((i.get("UserData") or {}).get("PlaybackPositionTicks"))


def _watch(body: Play, user: dict):
    """a Watch video: its downloaded file when there is one (full quality), else YouTube's one-file stream, which has
    picture and sound together and is small. ponytail: HD without a download needs the two streams joined as they
    come (ffmpeg), which a TV can't seek in; add it if the small picture is what people get most."""
    if lacking := acl.allowed(user, "/api/yt/video"):
        raise HTTPException(403, f"Your account can't use {lacking}")
    if len(body.id) != 11:
        raise HTTPException(422, "Not a Watch video")
    info = yt.video_info(body.id, user["id"])
    title = str(info.get("title") or "A video")[:200]
    card = yt.dumps({"title": title[:300], "channel": str(info.get("channel") or info.get("uploader") or "")[:200],
                     "channel_id": info.get("channel_id") if yt.CHANNEL_ID.fullmatch(info.get("channel_id") or "") else "",
                     "duration": yt._int(info.get("duration")), "views": info.get("view_count")})
    source = {"yt": body.id, "path": None, "fmt": None, "card": card, "runtime": float(info.get("duration") or 0)}
    h = yt.one("SELECT position FROM history WHERE user_id = ? AND video_id = ?", user["id"], body.id)
    at = float((h or {}).get("position") or 0)
    saved = None if acl.allowed(user, "/api/yt/file/x") else yt.one("SELECT path FROM videos WHERE id = ?", body.id)
    if saved and (path := yt.inside_videos(saved["path"])):
        ext = path.suffix.lower().lstrip(".")
        return {**source, "path": saved["path"]}, ext, MIME.get(ext, "video/mp4"), title, at
    whole = [f for f in info.get("formats") or [] if f.get("protocol") == "https" and f.get("url") and not f.get("has_drm")
             and (f.get("vcodec") or "none") != "none" and (f.get("acodec") or "none") != "none"]
    if not whole:
        if info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            raise HTTPException(409, "A live stream can't play on a TV set")
        raise HTTPException(409, "This video only plays on a TV set once it is downloaded: download it, then press On TV")
    f = max(whole, key=lambda f: (f.get("ext") == "mp4", f.get("height") or 0))
    ext = "mp4" if f.get("ext") == "mp4" else "webm"
    note = f"It plays at {f['height']}p: download it first for full quality" if f.get("height") else ""
    return {**source, "fmt": f["format_id"], "note": note}, ext, MIME.get(ext, "video/mp4"), title, at


def _send(ctl: str, link: dict, seek: float = 0):
    """(re)start the film on the TV's own player from its link, and seek to `seek` seconds once it lets us. Used to
    start a film and to send it again when it froze (the stream was cut by a restart)."""
    url = link["url"]
    didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="1" parentID="0" restricted="1">'
            f'<dc:title>{html.escape(link["title"])}</dc:title><upnp:class>object.item.videoItem</upnp:class>'
            f'<res protocolInfo="http-get:*:{link["mime"]}:{DLNA}">{html.escape(url)}</res></item></DIDL-Lite>')
    send = lambda: _soap(ctl, "SetAVTransportURI", {"CurrentURI": url, "CurrentURIMetaData": didl}, wait=SET_WAIT)  # noqa: E731
    try:  # the TV fetches the link before it answers: longer than a button's wait
        try:
            send()
        except Failed as e:
            if "701" not in str(e.detail):
                raise
            _soap(ctl, "Stop", wait=STOP_WAIT)  # still loading the last thing it was sent (a real LG, for minutes): end that
            send()
    except Failed as e:
        if "716" not in str(e.detail):
            raise
        raise Failed(f"The TV couldn't fetch it from {url.split('/cast/')[0]}: set the address TVs reach Parlor at "
                     "(Settings → TVs), and let the TV through any firewall to it") from None
    _soap(ctl, "Play", {"Speed": 1}, wait=SET_WAIT)  # a real LG reads the start of the file before it answers
    for n in range(SEEK_TRIES if seek > 5 else 0):  # from where you left off, once the TV lets it
        time.sleep(SEEK_WAIT)
        try:
            return _soap(ctl, "Seek", {"Unit": "REL_TIME", "Target": _clock(seek)})
        except Failed:
            if n == SEEK_TRIES - 1:
                log.warning("TV set %s wouldn't seek to where the film was left: it plays from the start", link["sid"])


@router.post("/hw/{sid}/play")
def play(sid: int, body: Play, request: Request, user=Depends(me)):
    tv._throttle(request, "hw")
    s = _set(sid, user)
    source, ext, mime, title, at = (_watch if body.kind == "yt" else _library)(body, user)
    _recall()
    token = secrets.token_urlsafe(32)
    url = f"{tvhw.parlor_address(request)}/cast/{token}/video.{ext}"
    at = at if body.start is None else body.start
    with _lock:
        now = time.time()
        for old in [t for t, link in _links.items() if link["until"] < now]:
            _links.pop(old, None)
            _drop(old)
        prev = _on.get(sid)  # the film the set has now: its link ends only once the TV takes this one
        if len(_links) >= MAX_LINKS:
            raise HTTPException(429, "Too many films are being sent to TVs: stop one first")
        link = _links[token] = {**source, "uid": user["id"], "sid": sid, "mime": mime, "title": title, "until": now + LIFE, "saved": 0.0,
                                "seen": False, "sent": time.monotonic(), "url": url, "moved": time.monotonic(), "woke": 0.0}
        _on[sid] = token
        _keep(token, link)

    def go(ip):
        return _send(_player(sid, ip), link, seek=at)

    try:
        tvhw._run(s, "", go, tvhw.TIMEOUT)
    except BaseException:
        _forget(sid, link)
        with _lock:
            if prev in _links and sid not in _on:  # the TV refused this one: the film it had is still its own
                _on[sid] = prev
        _found.pop(sid, None)  # its player may have moved: look again next time
        raise
    with _lock:
        if prev and prev != token and _links.pop(prev, None) is not None:
            _drop(prev)
    return {"ok": True, "title": title, "note": source.get("note", "")}


@router.get("/hw/{sid}/playing")
def playing(sid: int, user=Depends(me)):
    """what Parlor is playing on this set and where it got to; the film's place is saved from here"""
    s = _set(sid, user)
    _recall()
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
    rel = _tag(pos, "RelTime")
    at, length = _secs(rel), _secs(_tag(pos, "TrackDuration")) or link["runtime"]
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
        now = time.monotonic()
        if state != "playing" or at != link.get("at"):  # paused/starting, or the time moved (even a seek): not frozen
            link["moved"] = now
        link["seen"], link["at"] = True, at
        _save(link, at, length)
        # it says playing, gave a real time that stood still, isn't near the end, and the link can be re-sent:
        # the stream was cut (a restart). Send it back from there. HTTPException covers a busy set (429) too.
        if (state == "playing" and ":" in rel and at < length - 5 and link.get("url")
                and now - link.get("moved", now) > STALL and now - link.get("woke", 0) > STALL):
            link["woke"] = now

            def resend(ip):
                if _links.get(_on.get(sid, "")) is not link:  # a newer film took the set while we waited
                    return None
                return _send(_player(sid, ip), link, seek=at)

            try:
                tvhw._run(s, "", resend, tvhw.TIMEOUT)
            except HTTPException:
                log.warning("TV set %s: a frozen film wouldn't take being sent again", sid)
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
