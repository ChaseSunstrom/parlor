"""TV hardware from Parlor's remote: power, input and volume on the TV set itself, over the set's own protocol
(Roku ECP, LG webOS, Samsung Tizen, Android TV through adb) or an HDMI-CEC adapter plugged into the server.

An admin adds each set in Settings → TVs and picks who may use it. Parlor only ever connects to an address on the
LAN's private ranges: the name is resolved once, every address it has is checked, and the connection goes to that
checked address, again on every press. An LG's key and a Samsung's token live in the (encrypted) database and are
never sent to a browser or written to the log. Every button is a name from a fixed list; nothing a person types
reaches a TV, adb or cec-client.
"""
import base64
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

import core
import tv
from core import admin, db, me, one, rows

router = APIRouter(prefix="/api/tv")
KINDS = {"roku": "Roku", "webos": "LG (webOS)", "tizen": "Samsung (Tizen)", "adb": "Android TV (adb)", "cec": "HDMI-CEC adapter"}
PLAYS = ("webos",)  # these play a film in their own player (cast.py). ponytail: only an LG is listed; any DLNA player could be, once tried on one
PAIRS = ("webos", "tizen")  # these ask on their screen once, then give a key Parlor keeps
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
LAN = [ipaddress.ip_network(n.strip(), strict=False) for n in os.environ.get("LAN_SUBNET", "192.168.0.0/16").split(",") if n.strip()]  # compose's own setting
ROUTES, ROUTES6, INSIDE = "/proc/net/route", "/proc/net/if_inet6", "/.dockerenv"
ALSO: set = set()  # for the tests only: their fake TVs listen on 127.0.0.1. Nothing else adds to it, and no setting does
PORTS = {"roku": 8060, "webos": 3001, "tizen": 8002, "adb": 5555}  # each protocol's own port, fixed
OWN = False  # for the tests only: "Open Parlor" with a Parlor on 127.0.0.1, which no real TV could open
TLS = True  # LG and Samsung take wss only; the tests' fakes speak plain ws
TIMEOUT = 5  # seconds a TV has to answer a button
PAIR_WAIT = 30  # seconds someone has to press Accept on the TV
RESOLVE_WAIT = 3  # seconds a name has to resolve
WOL_PORT = 9
ADB, CEC = "adb", "cec-client"
MAX_SETS = 50
HOST = re.compile(r"[A-Za-z0-9.:][A-Za-z0-9.:-]{0,252}\Z")
OFF = "The TV didn't answer: is it on, and on the network?"
NO_ADB = "adb isn't installed on the server (Parlor's own image has it)"
NO_CEC = "cec-client isn't installed on the server (Parlor's own image has it)"

HDMI = "1234"
ROKU = {"power": "Power", "volup": "VolumeUp", "voldown": "VolumeDown", "mute": "VolumeMute", **{f"hdmi{n}": f"InputHDMI{n}" for n in HDMI}}
LG = {"power": "ssap://system/turnOff", "volup": "ssap://audio/volumeUp", "voldown": "ssap://audio/volumeDown", "mute": "ssap://audio/setMute",
      **{f"hdmi{n}": "ssap://tv/switchInput" for n in HDMI},
      "parlor": "ssap://system.launcher/open"}  # its browser, at this Parlor's TV screen (press() says which address)
LG_ASKS = {"forcePairing": False, "pairingType": "PROMPT", "manifest": {
    "manifestVersion": 1, "permissions": ["CONTROL_POWER", "CONTROL_AUDIO", "CONTROL_INPUT_TV", "READ_INPUT_DEVICE_LIST", "LAUNCH"]}}
TIZEN = {"power": "KEY_POWER", "volup": "KEY_VOLUP", "voldown": "KEY_VOLDOWN", "mute": "KEY_MUTE", "input": "KEY_SOURCE",
         **{f"hdmi{n}": f"KEY_HDMI{n}" for n in HDMI}}
ANDROID = {"power": 26, "volup": 24, "voldown": 25, "mute": 164, "input": 178, **{f"hdmi{n}": 242 + int(n) for n in HDMI}}  # KeyEvent's numbers
CEC_LINES = {"power": "", "volup": "volup", "voldown": "voldown", "mute": "mute",  # power asks the TV's state first (_cec)
             **{f"hdmi{n}": f"tx 1F:82:{n}0:00" for n in HDMI}}  # "active source" from HDMI n
KEYMAPS = {"roku": ROKU, "webos": LG, "tizen": TIZEN, "adb": ANDROID, "cec": CEC_LINES}
Key = Literal["power", "volup", "voldown", "mute", "input", "hdmi1", "hdmi2", "hdmi3", "hdmi4", "parlor"]

_busy: dict = defaultdict(threading.Lock)  # one press at a time on a set
_http = requests.Session()
_http.trust_env = False  # no proxy from the environment between Parlor and a TV
log = logging.getLogger("parlor.tvhw")
_quiet = logging.getLogger("parlor.tvhw.ws")
_quiet.disabled = True  # websockets logs each address it opens at DEBUG, and a Samsung's carries its token


class Refused(HTTPException):
    """an address Parlor won't connect to, or a button this set doesn't have"""

    def __init__(self, detail: str):
        super().__init__(400, detail)


class Unpaired(HTTPException):
    def __init__(self, detail: str = "Pair this TV first: Settings → TVs → Pair, then accept on the TV"):
        super().__init__(409, detail)


class Failed(HTTPException):
    def __init__(self, detail: str = OFF):
        super().__init__(502, detail)


class Off(Exception):
    """nothing listens at the TV's address: it's off, or in standby"""


# --- the address check -----------------------------------------------------------------------

def _dockers():
    """The networks this container sits on itself: the other containers and Docker's gateway are there, and none of
    them is a TV. Empty outside a container. None when it can't be read (then only LAN_SUBNET passes)."""
    if not os.path.exists(INSIDE):
        return []
    try:
        nets = []
        for line in Path(ROUTES).read_text().splitlines()[1:]:
            f = line.split()
            if len(f) > 7 and f[2] == "00000000" and f[1] != "00000000":  # attached, not through a gateway
                nets.append(ipaddress.ip_network((bytes.fromhex(f[1])[::-1], bin(int(f[7], 16)).count("1"))))
        if os.path.exists(ROUTES6):
            for line in Path(ROUTES6).read_text().splitlines():
                f = line.split()
                nets.append(ipaddress.ip_network((bytes.fromhex(f[0]), int(f[2], 16)), strict=False))
        return nets
    except (OSError, ValueError, IndexError):
        return None


def _lookup(host: str):
    """getaddrinfo with a deadline: a resolver that hangs holds a spare thread, not the request"""
    out: list = []

    def go():
        try:
            out.append(socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
        except BaseException as e:
            out.append(e)

    t = threading.Thread(target=go, daemon=True)  # ponytail: a hung lookup's thread lives until the resolver gives up; the press throttle bounds how many
    t.start()
    t.join(RESOLVE_WAIT)
    if not out:
        raise Refused("Looking up that name took too long: type the TV's number instead (like 192.168.1.40)")
    if isinstance(out[0], BaseException):
        raise out[0]
    return out[0]


def checked_ip(host: str) -> str:
    """The one address to connect to for `host`, or Refused. The name is resolved here, once; every address it has
    must be on the LAN's private ranges (so never loopback, link-local, the cloud's metadata address, multicast or
    the internet); the caller connects to the address returned, never to the name again."""
    if not HOST.match(host or ""):
        raise Refused("Type the TV's address as a name or a number like 192.168.1.40: no http://, no port")
    try:
        found = {info[4][0].split("%")[0] for info in _lookup(host)}
        ips = sorted((ipaddress.ip_address(ip) for ip in found), key=lambda ip: (ip.version, int(ip)))  # IPv4 first
    except (OSError, UnicodeError, ValueError):
        raise Refused("Can't find a TV at that address") from None
    if not ips or not all(any(ip in net for net in (*PRIVATE, *ALSO)) for ip in ips):
        raise Refused("That address isn't on your home network (10.x, 172.16–31.x, 192.168.x or fc00::/7)")
    mine = _dockers()
    for ip in ips:
        if any(ip in net for net in (*LAN, *ALSO)):  # the home network as compose was told it, even when attached (host networking)
            continue
        if mine is None or any(ip in net for net in mine):
            raise Refused("That address is on Docker's own network, where Parlor's other containers are, not a TV. "
                          "If your home network really uses it, add it to LAN_SUBNET")
    return str(ips[0])


def _at(ip: str, kind: str) -> str:
    return f"[{ip}]:{PORTS[kind]}" if ":" in ip else f"{ip}:{PORTS[kind]}"


# --- the drivers: each takes the set's row, a button from its own map and the checked address ---

def _roku(s, key, ip):
    r = _http.post(f"http://{_at(ip, 'roku')}/keypress/{ROKU[key]}", timeout=TIMEOUT, allow_redirects=False)
    if r.status_code == 403:
        raise Failed("The Roku refused: on it, set Settings → System → Advanced system settings → Control by mobile apps to Enabled")
    if r.status_code != 200:
        raise Failed("The Roku didn't take that button")


def _roku_test(s, ip):
    if _http.get(f"http://{_at(ip, 'roku')}/query/device-info", timeout=TIMEOUT, allow_redirects=False).status_code != 200:
        raise Failed("Something answered, but not as a Roku: is Control by mobile apps enabled on it?")
    return "The Roku answered"


def _ws(ip, kind, path="/"):
    """a WebSocket on a socket opened here, to the checked address: nothing in the handshake can point it elsewhere"""
    try:
        sock = socket.create_connection((ip, PORTS[kind]), TIMEOUT)
    except OSError:
        raise Off() from None
    ctx = None
    if TLS:  # ponytail: a TV signs its own certificate, so it isn't verified; pin it at pairing if the LAN isn't trusted
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        return connect(f"{'wss' if TLS else 'ws'}://{_at(ip, kind)}{path}", sock=sock, ssl=ctx, open_timeout=TIMEOUT, close_timeout=1,
                       max_size=65536, logger=_quiet)
    except TimeoutError:  # it took the connection and then said nothing: not a prompt on its screen
        sock.close()
        raise Off() from None
    except BaseException:
        sock.close()
        raise


def _heard(ws, wait, want):
    """the first message `want` picks out, within `wait` seconds however much else the TV says"""
    end = time.monotonic() + wait
    while (left := end - time.monotonic()) > 0:
        m = json.loads(ws.recv(timeout=left))
        if isinstance(m, dict) and (got := want(m)) is not None:
            return got
    raise TimeoutError


def _lg_open(s, ip, wait):
    """a registered connection and the client-key the TV gave (after its prompt, the first time)"""
    ws = _ws(ip, "webos")
    try:
        ws.send(json.dumps({"type": "register", "id": "reg", "payload": {**LG_ASKS, **({"client-key": s["secret"]} if s["secret"] else {})}}))

        def registered(m):
            if m.get("type") == "error":
                raise Failed("The TV refused to pair")
            return str(m["payload"]["client-key"])[:200] if m.get("type") == "registered" else None

        return ws, _heard(ws, wait, registered)
    except BaseException:
        ws.close()
        raise


def _lg_ask(ws, uri, payload=None):
    ws.send(json.dumps({"type": "request", "id": uri, "uri": uri, **({} if payload is None else {"payload": payload})}))

    def answer(m):
        if m.get("id") != uri:
            return None
        if m.get("type") == "error" or (m.get("payload") or {}).get("returnValue") is False:
            raise Failed("The TV didn't take that button")
        return m.get("payload") or {}

    return _heard(ws, TIMEOUT, answer)


def _lg(s, key, ip, url=""):
    if not s["secret"]:
        raise Unpaired()
    try:
        ws, secret = _lg_open(s, ip, TIMEOUT)
    except TimeoutError:  # it asks on its screen again: the key no longer counts
        raise Unpaired("The TV wants to pair again: Settings → TVs → Pair") from None
    with ws:
        if secret != s["secret"]:  # someone at the TV accepted just now
            _keep(s, secret)
        if key == "mute":
            _lg_ask(ws, LG[key], {"mute": not _lg_ask(ws, "ssap://audio/getStatus").get("mute")})
        elif key == "parlor":
            try:
                _lg_ask(ws, LG[key], {"target": url})
            except Failed:  # a pairing from before this button has no leave to open anything
                raise Failed("The TV wouldn't open Parlor. If it was paired a while ago, remove it in Settings → TVs and add it again") from None
        else:
            _lg_ask(ws, LG[key], {"inputId": f"HDMI_{key[4:]}"} if key.startswith("hdmi") else None)


def _lg_pair(s, ip):
    try:
        ws, secret = _lg_open(s, ip, PAIR_WAIT)
    except TimeoutError:
        raise Failed("Nobody pressed Accept on the TV: try again and accept there") from None
    ws.close()
    _keep(s, secret)
    return "Paired"


def _tizen_open(s, ip, wait):
    """a connection the TV has let in, and its token (new after someone presses Allow on it)"""
    ask = {"name": base64.b64encode(b"Parlor").decode(), **({"token": s["secret"]} if s["secret"] else {})}
    ws = _ws(ip, "tizen", "/api/v2/channels/samsung.remote.control?" + urlencode(ask))
    try:
        def let_in(m):
            if m.get("event") in ("ms.channel.unauthorized", "ms.channel.timeOut"):
                raise Unpaired("The TV said no: press Allow on it (Settings → TVs → Pair), or allow Parlor in its Device Connection Manager")
            return str((m.get("data") or {}).get("token") or s["secret"])[:200] if m.get("event") == "ms.channel.connect" else None

        return ws, _heard(ws, wait, let_in)
    except BaseException:
        ws.close()
        raise


def _tizen(s, key, ip):
    if not s["secret"]:
        raise Unpaired()
    try:
        ws, secret = _tizen_open(s, ip, TIMEOUT)
    except TimeoutError:
        raise Unpaired("The TV wants to pair again: Settings → TVs → Pair") from None
    with ws:
        if secret != s["secret"]:
            _keep(s, secret)
        ws.send(json.dumps({"method": "ms.remote.control",
                            "params": {"Cmd": "Click", "DataOfCmd": TIZEN[key], "Option": "false", "TypeOfRemote": "SendRemoteKey"}}))


def _tizen_pair(s, ip):
    try:
        ws, secret = _tizen_open(s, ip, PAIR_WAIT)
    except (TimeoutError, Unpaired):
        raise Failed("Nobody pressed Allow on the TV: try again and allow it there") from None
    ws.close()
    if not secret:
        raise Failed("The TV let Parlor in without a token: it will ask again each time (Allow it for good on the TV)")
    _keep(s, secret)
    return "Paired"


def _tool(argv, missing, stdin=None, wait=None, home=None):
    """one run of adb or cec-client: an argv list (no shell), a deadline, and none of Parlor's own environment"""
    if not shutil.which(argv[0]):
        raise Failed(missing)
    env = {"PATH": os.environ.get("PATH", ""), **({"HOME": home} if home else {})}
    try:
        return subprocess.run(argv, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", timeout=wait or TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        raise Failed() from None


_adb_home = None  # this run's private folder holding adb's key
_adb_lock = threading.Lock()


def _adb_folder() -> str:
    """adb signs in to a TV with its own private key, which the TV remembers once someone there allows it. The key is
    kept in the database; each run of Parlor writes it into a new folder only it can read (never the data folder)."""
    global _adb_home
    with _adb_lock:
        if not _adb_home:
            if not shutil.which(ADB):
                raise Failed(NO_ADB)
            home = Path(tempfile.mkdtemp(prefix="parlor-adb-"))  # 0700
            try:
                keyfile = home / ".android" / "adbkey"
                keyfile.parent.mkdir()
                if key := core.setting("tv_adb_key"):
                    keyfile.touch(mode=0o600)
                    keyfile.write_text(key)
                else:
                    _tool([ADB, "keygen", str(keyfile)], NO_ADB, home=str(home))
                    if not keyfile.is_file():
                        raise Failed("adb couldn't make its key")
                    core.set_setting("tv_adb_key", keyfile.read_text())
            except BaseException:
                shutil.rmtree(home, ignore_errors=True)
                raise
            _adb_home = str(home)
        return _adb_home


def _adb(s, key, ip):
    home, at = _adb_folder(), _at(ip, "adb")
    out = _tool([ADB, "connect", at], NO_ADB, home=home).stdout
    allow = "The TV hasn't let Parlor in: on it, turn on Developer options → Network debugging, press Test here, then tick Always allow and press Allow on the TV"
    if "connected to" not in out:
        raise Failed(allow if "authenticate" in out or "unauthorized" in out else OFF)
    if key and _tool([ADB, "-s", at, "shell", "input", "keyevent", str(ANDROID[key])], NO_ADB, home=home).returncode:
        raise Failed(allow)


def _cec_say(line: str) -> str:
    r = _tool([CEC, "-s", "-d", "1"], NO_CEC, stdin=line + "\n", wait=TIMEOUT * 3)  # the adapter takes a few seconds to open
    if r.returncode:
        raise Failed("The HDMI-CEC adapter didn't answer: is it plugged in, and passed into Parlor's container?")
    return r.stdout


def _cec(s, key, ip):
    _cec_say(CEC_LINES[key] or ("standby 0" if "power status: on" in _cec_say("pow 0") else "on 0"))


DRIVERS = {"roku": _roku, "webos": _lg, "tizen": _tizen, "adb": _adb, "cec": _cec}
TESTS = {"roku": _roku_test, "webos": _lg_pair, "tizen": _tizen_pair,
         "adb": lambda s, ip: _adb(s, "", ip) or "The TV let Parlor in",
         "cec": lambda s, ip: "The TV is on" if "power status: on" in _cec_say("pow 0") else "The adapter answered; the TV is off or in standby"}


def _keep(s, secret: str):
    db().execute("UPDATE tv_sets SET secret = ? WHERE id = ?", (secret, s["id"]))


def _wake(ip: str, mac: str):
    """Wake-on-LAN, for an LG or a Samsung that's off (their network side sleeps with them)"""
    # ponytail: sent to the TV's own checked address, not broadcast (a container can't): it works while the router remembers the TV
    try:
        with socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(b"\xff" * 6 + bytes.fromhex(mac) * 16, (ip, WOL_PORT))
    except OSError:
        raise Failed() from None


def _run(s, key, call, wait):
    """one press or test on a set: its address checked now, one at a time, and whatever goes wrong said in plain words
    (never the exception's own text: it can hold an address with a token in it)"""
    if key and key not in KEYMAPS[s["kind"]]:
        raise Refused(f"{KINDS[s['kind']]} has no such button")
    ip = "" if s["kind"] == "cec" else checked_ip(s["host"])
    lock = _busy[s["id"]]  # held here by name: removing the set meanwhile forgets it from _busy
    if not lock.acquire(timeout=wait):
        raise HTTPException(429, "That TV is still busy with the last button")
    try:
        return call(ip)
    except Off:
        if key == "power" and s["mac"]:
            return _wake(ip, s["mac"])
        log.warning("TV set %s (%s) didn't answer", s["id"], s["kind"])
        raise Failed() from None
    except HTTPException as e:  # Failed, Unpaired: already in plain words
        log.warning("TV set %s (%s) failed: %s", s["id"], s["kind"], e.detail)
        raise
    except (OSError, WebSocketException, requests.RequestException, subprocess.SubprocessError, ValueError, LookupError, TypeError, AttributeError) as e:
        log.warning("TV set %s (%s) failed: %s", s["id"], s["kind"], type(e).__name__)  # the class only: its text can hold a token
        raise Failed() from None
    finally:
        lock.release()


# --- the API: sets and who may use them (admins), the buttons (whoever was given the set) -------

class NewSet(BaseModel):
    kind: Literal["roku", "webos", "tizen", "adb", "cec"]
    name: str = Field(min_length=1, max_length=40, pattern=r"\S")
    host: str = Field("", max_length=253)
    mac: str = Field("", pattern=r"^(([0-9A-Fa-f]{2}[:-]?){5}[0-9A-Fa-f]{2})?$")  # for switching on an LG or a Samsung


class Users(BaseModel):
    users: list[int] = Field(max_length=500)


class Press(BaseModel):
    key: Key


def _shown(s: dict) -> dict:
    return {"id": s["id"], "kind": s["kind"], "name": s["name"], "keys": list(KEYMAPS[s["kind"]]), "plays": s["kind"] in PLAYS}


def _set(sid: int, user: dict) -> dict:
    """a set this person may use: an admin any, a member the ones an admin gave them"""
    s = one("SELECT id, kind, name, host, mac, secret FROM tv_sets WHERE id = ?", sid)
    if not s or not (user["admin"] or one("SELECT 1 AS x FROM tv_access WHERE set_id = ? AND user_id = ?", sid, user["id"])):
        raise HTTPException(404, "No such TV")
    return s


@router.get("/sets")
def all_sets(_=Depends(admin)):
    sets = rows("SELECT id, kind, name, host, mac, secret != '' AS paired FROM tv_sets ORDER BY name, id")
    return {"kinds": KINDS, "address": core.setting("tv_address"), "sets": [{**_shown(s), "host": s["host"], "mac": s["mac"], "pairs": s["kind"] in PAIRS, "paired": bool(s["paired"]),
                                      "users": [r["user_id"] for r in rows("SELECT user_id FROM tv_access WHERE set_id = ? ORDER BY user_id", s["id"])]}
                                     for s in sets]}


@router.post("/sets")
def add_set(body: NewSet, _=Depends(admin)):
    host = body.host.strip()
    if body.kind == "cec":
        if host:
            raise Refused("An HDMI-CEC adapter is plugged into the server: it has no address")
    else:
        checked_ip(host)
    if one("SELECT COUNT(*) AS n FROM tv_sets")["n"] >= MAX_SETS:
        raise Refused("That's a lot of TVs: remove one first")
    cur = db().execute("INSERT INTO tv_sets(kind, name, host, mac, created) VALUES (?, ?, ?, ?, ?)",
                       (body.kind, body.name.strip(), host, re.sub(r"[:-]", "", body.mac).lower(), time.time()))
    return {"id": cur.lastrowid}


@router.delete("/sets/{sid}")
def remove_set(sid: int, _=Depends(admin)):
    s = one("SELECT kind, host FROM tv_sets WHERE id = ?", sid)
    db().execute("DELETE FROM tv_sets WHERE id = ?", (sid,))  # its key or token and who could use it go with it
    _busy.pop(sid, None)
    if s and s["kind"] == "adb":  # best effort: adb keeps the connection to a TV that's no longer Parlor's
        try:
            _tool([ADB, "disconnect", _at(checked_ip(s["host"]), "adb")], NO_ADB, home=_adb_folder())
        except Exception:
            log.info("adb disconnect for removed set %s didn't run", sid)
    return {"ok": True}


@router.put("/sets/{sid}/users")
def set_users(sid: int, body: Users, user=Depends(admin)):
    _set(sid, user)
    want = set(body.users)
    if len(rows(f"SELECT id FROM users WHERE id IN ({','.join('?' * len(want))})", *want)) != len(want):
        raise HTTPException(404, "No such account")
    conn = db()
    conn.execute("BEGIN IMMEDIATE")  # both or neither: a failure between them would leave nobody ticked
    try:
        conn.execute("DELETE FROM tv_access WHERE set_id = ?", (sid,))
        conn.executemany("INSERT INTO tv_access(set_id, user_id) VALUES (?, ?)", [(sid, u) for u in want])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"users": sorted(want)}


@router.post("/sets/{sid}/test")
def test_set(sid: int, request: Request, user=Depends(admin)):
    """Reach the set: an LG or a Samsung asks on its screen and gives its key, an Android TV asks to allow debugging."""
    tv._throttle(request, "hwtest")
    s = _set(sid, user)
    said = _run(s, "", lambda ip: TESTS[s["kind"]](s, ip), TIMEOUT)
    return {"ok": True, "said": said, "paired": bool(one("SELECT secret FROM tv_sets WHERE id = ?", sid)["secret"])}


@router.get("/hw")
def my_sets(user=Depends(me)):
    """the sets this person's remote has buttons for; no addresses"""
    if user["admin"]:
        return {"sets": [_shown(s) for s in rows("SELECT id, kind, name FROM tv_sets ORDER BY name, id")]}
    return {"sets": [_shown(s) for s in rows("""SELECT s.id, s.kind, s.name FROM tv_sets s JOIN tv_access a ON a.set_id = s.id
                                                WHERE a.user_id = ? ORDER BY s.name, s.id""", user["id"])]}


NO_ADDRESS = ("Open Parlor by the server's address on your network (like http://192.168.1.20:8800, not localhost "
              "or a public name), then press this again: the TV opens the same address")


def _home(scheme: str, host: str, port, no: HTTPException) -> str:
    """scheme://host[:port] for a host on the home network like a TV's own (checked_ip), never one only this computer
    has: a TV would open nothing there"""
    try:
        own = ipaddress.ip_address(checked_ip(host)).is_loopback
    except (HTTPException, ValueError):
        raise no from None
    if (own and not OWN) or scheme not in ("http", "https"):
        raise no
    return f"{scheme}://{f'[{host}]' if ':' in host else host}{f':{port}' if port else ''}"


def parlor_address(request: Request) -> str:
    """Where a TV reaches this Parlor: the address an admin set in Settings → TVs, else the one the person pressing
    reached it by. That one comes from the request, which a member can write as they like, so it is checked and
    built again from its name and port alone."""
    if kept := core.setting("tv_address"):
        return kept
    try:
        port = request.url.port
    except ValueError:
        raise HTTPException(409, NO_ADDRESS) from None
    return _home(request.url.scheme, request.url.hostname or "", port, HTTPException(409, NO_ADDRESS))


class Address(BaseModel):
    address: str = Field(max_length=300)


@router.put("/address")
def set_address(body: Address, _=Depends(admin)):
    """the address TVs reach Parlor at, for a Parlor people open by a name a TV can't use; empty forgets it"""
    text = body.address.strip().rstrip("/")
    if text:
        no = Refused("Type it like http://192.168.1.20:8800: the server's own address on your home network, nothing after the port")
        try:
            u = urlsplit(text)
            port = u.port
        except ValueError:
            raise no from None
        if u.path or u.query or u.fragment or u.username or u.password:
            raise no
        text = _home(u.scheme, u.hostname or "", port, no)
    core.set_setting("tv_address", text)
    return {"address": text}


@router.post("/hw/{sid}/press")
def press(sid: int, body: Press, request: Request, user=Depends(me)):
    tv._throttle(request, "hw")
    s = _set(sid, user)
    if body.key == "parlor" and s["kind"] == "webos":
        url = parlor_address(request) + "/tv"
        _run(s, body.key, lambda ip: _lg(s, body.key, ip, url), TIMEOUT)
        return {"ok": True}
    _run(s, body.key, lambda ip: DRIVERS[s["kind"]](s, body.key, ip), TIMEOUT)
    return {"ok": True}
