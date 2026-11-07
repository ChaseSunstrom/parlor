"""Setting up the whole stack so nobody signs in to each app, copies API keys or adds indexers:
Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent (behind Gluetun), FlareSolverr, Jellyfin, Seerr,
Bazarr and Cleanuparr, as Parlor's compose runs them.

Every step looks first and changes only what's missing, then says what it did: "ok" (already
right), "done" (changed), "skipped" (waiting for something, like your password) or "failed"
(why, in plain words). Parlor runs it at start-up (the steps that need no password) and when the
admin account is created; Settings → Services runs it again any time.

`python stack.py init` runs before the apps start (a one-shot in the compose): it opens
qBittorrent to the home network, which has to be in its config file before it starts."""

import base64
import calendar
import ipaddress
import json
import logging
import os
import re
import socket
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import arr
import core
from core import admin, one, setting

log = logging.getLogger("parlor.stack")
router = APIRouter(prefix="/api/stack")

STACK = arr.STACK
DATA = Path(os.environ.get("MEDIA_ROOT", "/data"))  # the shared media folder: /data in every container
# "at home" for qBittorrent: your network and the containers (the *arrs decide that for themselves: any private address)
HOME_NETS = [n.strip() for n in os.environ.get("LAN_SUBNET", "192.168.0.0/16").split(",") if n.strip()] + ["172.16.0.0/12"]
INDEXERS = [x.strip() for x in os.environ.get("PUBLIC_INDEXERS", "yts,eztv,thepiratebay,1337x,limetorrents,nyaasi,knaben").split(",") if x.strip() and x.strip() != "none"]
LIBRARIES = [("Movies", "movies", "/data/movies"), ("Shows", "tvshows", "/data/shows"), ("Music", "music", "/data/music"),
             ("YouTube", "homevideos", "/data/youtube"), ("Books", "books", "/data/books")]
JF_AUTH = 'MediaBrowser Client="Parlor", Device="Parlor", DeviceId="parlor-setup", Version="1"'


def on() -> bool:
    """Parlor's compose sets STACK=1: the apps run beside Parlor under their service names."""
    return os.environ.get("STACK") == "1"


def where(name, default):
    return (setting(f"{name}_url") or os.environ.get(f"{name.upper()}_URL") or default).rstrip("/")


qbit = lambda: where("qbittorrent", "http://gluetun:8080")  # noqa: E731  (qBittorrent shares Gluetun's network)
jellyfin_url = lambda: where("jellyfin", "http://jellyfin:8096")  # noqa: E731
seerr_url = lambda: where("seerr", "http://seerr:5055")  # noqa: E731
bazarr_url = lambda: where("bazarr", "http://bazarr:6767")  # noqa: E731
cleanuparr_url = lambda: where("cleanuparr", "http://cleanuparr:11011")  # noqa: E731
flaresolverr_url = lambda: where("flaresolverr", "http://flaresolverr:8191")  # noqa: E731

# the compose's service names: setup changes an app only when Parlor's address for it is one of these,
# never a server of yours elsewhere that Settings points at
SERVICES = {"sonarr": {"sonarr"}, "radarr": {"radarr"}, "lidarr": {"lidarr"}, "prowlarr": {"prowlarr"}, "qbittorrent": {"gluetun", "qbittorrent"},
            "jellyfin": {"jellyfin"}, "seerr": {"seerr"}, "bazarr": {"bazarr"}, "cleanuparr": {"cleanuparr"}}
LABELS = {"qbittorrent": "qBittorrent", "jellyfin": "Jellyfin", "seerr": "Seerr", "bazarr": "Bazarr", "cleanuparr": "Cleanuparr"}


def address(app) -> str:
    return arr.url(app) if app in arr.APPS else {"qbittorrent": qbit, "jellyfin": jellyfin_url, "seerr": seerr_url,
                                                 "bazarr": bazarr_url, "cleanuparr": cleanuparr_url}[app]()


def ours(app) -> bool:
    """The compose's own app, not one of yours elsewhere."""
    return (urlsplit(address(app)).hostname or "") in SERVICES[app]


class Wait(Exception):
    """The step can't run yet (say why); it isn't a failure."""


class Fail(Exception):
    """The step failed (say why, plainly)."""


def need(r, what="it"):
    """A response, or Fail with what the app said."""
    if r.status_code in (401, 403):
        raise Fail(f"{what} refused Parlor ({r.status_code})")
    if not r.ok:
        raise Fail(f"{what} said {r.status_code}: {arr.problem(r)}")
    return r


def http(method, url, **kw):
    kw.setdefault("timeout", 30)
    return requests.request(method, url, **kw)


def make_dir(path: str):
    """A media folder under /data, owned by the user the apps run as (Parlor itself runs as root)."""
    folder = DATA / path.removeprefix("/data/").lstrip("/")
    folder.mkdir(parents=True, exist_ok=True)
    hand_over(folder)
    return folder


def hand_over(folder: Path) -> bool:
    """Give an empty folder that Parlor or Docker just made (it's ours: both run as root) to the
    user the apps run as. A link, a folder with anything in it, or someone else's is left alone,
    and a folder that refuses (a network share) is too. True when it was handed over."""
    try:
        if folder.is_symlink() or not folder.is_dir() or any(folder.iterdir()):
            return False
        owner, uid = folder.stat().st_uid, os.environ.get("PUID")
        if not uid or not hasattr(os, "geteuid") or owner != os.geteuid() or str(owner) == uid:
            return False
        _own(folder)
        return True
    except OSError:
        return False


# --- Sonarr, Radarr, Lidarr, Prowlarr ------------------------------------------------------

def arr_login(app, name, password):
    host = arr.call(app, "GET", "/config/host")
    if (host.get("authenticationMethod") or "none").lower() != "none":
        return "ok", "has its own sign-in"
    if not password:
        raise Wait("needs your password once: Settings → Services → Set up")
    arr.call(app, "PUT", "/config/host", json={
        **host, "authenticationMethod": "forms", "authenticationRequired": "disabledForLocalAddresses",
        "allowedHosts": ",".join(dict.fromkeys(["localhost", app, "parlor", "yeerr"])),  # compat: yeerr is the old service name
        "username": name, "password": password, "passwordConfirmation": password})
    return "done", "no sign-in at home; your Parlor name and password from outside"


def roots(app) -> list:
    return [r["path"].rstrip("/") for r in arr.call(app, "GET", "/rootfolder")]


def arr_root(app):
    root, have = arr.APPS[app]["root"], roots(app)
    if have:  # yours, or ours from before: either way, not a second one
        return "ok", ", ".join(have)
    make_dir(root)
    body = {"path": root}
    if app == "lidarr":
        quality = arr.call(app, "GET", "/qualityprofile")[0]["id"]
        meta = next((p["id"] for p in arr.call(app, "GET", "/metadataprofile") if p["name"] != "None"), 1)
        body.update(name="Music", defaultQualityProfileId=quality, defaultMetadataProfileId=meta,
                    defaultMonitorOption="all", defaultNewItemMonitorOption="all", defaultTags=[])
    arr.call(app, "POST", "/rootfolder", json=body)
    return "done", root


def arr_client(app):
    if any(c.get("implementation") == "QBittorrent" for c in arr.call(app, "GET", "/downloadclient")):
        return "ok", "qBittorrent"
    schema = next(s for s in arr.call(app, "GET", "/downloadclient/schema") if s["implementation"] == "QBittorrent")
    where_, (field, category) = urlsplit(qbit()), arr.APPS[app]["category"]
    arr.fill(schema, host=where_.hostname, port=where_.port or 8080, useSsl=where_.scheme == "https", **{field: category})
    schema.update(name="qBittorrent", enable=True, priority=1)
    arr.call(app, "POST", "/downloadclient?forceSave=true", json=schema)  # saved even if qBittorrent is restarting
    return "done", f"qBittorrent, category {category}"


def prowlarr_apps():
    have = {a.get("implementation") for a in arr.call("prowlarr", "GET", "/applications")}
    schemas, added = None, []
    for app in ("sonarr", "radarr", "lidarr"):
        if not ours(app):
            continue  # Prowlarr would push its indexers into your own one
        label = arr.APPS[app]["label"]
        if label in have or not arr.key(app):
            continue
        schemas = schemas or arr.call("prowlarr", "GET", "/applications/schema")
        s = arr.fill(next(x for x in schemas if x["implementation"] == label),
                     prowlarrUrl=arr.url("prowlarr"), baseUrl=arr.url(app), apiKey=arr.key(app))
        s.update(name=label, syncLevel="fullSync")
        arr.call("prowlarr", "POST", "/applications?forceSave=true", json=s)
        added.append(label)
    return ("done", "now fills " + ", ".join(added)) if added else ("ok", "fills Sonarr, Radarr and Lidarr")


def flaresolverr_tag():
    tag = next((t for t in arr.call("prowlarr", "GET", "/tag") if t["label"] == "flaresolverr"), None)
    return (tag or arr.call("prowlarr", "POST", "/tag", json={"label": "flaresolverr"}))["id"]


def prowlarr_proxy():
    tag = flaresolverr_tag()
    if any(p.get("implementation") == "FlareSolverr" for p in arr.call("prowlarr", "GET", "/indexerProxy")):
        return "ok", "FlareSolverr"
    s = next(x for x in arr.call("prowlarr", "GET", "/indexerProxy/schema") if x["implementation"] == "FlareSolverr")
    arr.fill(s, host=flaresolverr_url() + "/", requestTimeout=60)
    s.update(name="FlareSolverr", tags=[tag])
    arr.call("prowlarr", "POST", "/indexerProxy?forceSave=true", json=s)
    return "done", "FlareSolverr for sites behind Cloudflare"


def prowlarr_indexers():
    have = {(i.get("definitionName") or "").lower() for i in arr.call("prowlarr", "GET", "/indexer")}
    ours = set(json.loads(setting("indexers_added") or "[]"))  # added before: if you deleted one, it stays deleted
    if have and not ours:
        return "ok", f"{len(have)} indexers of your own"
    want = [d for d in INDEXERS if d.lower() not in have | ours]
    if not want:
        return "ok", f"{len(have)} indexers"
    # Cardigann definitions are lower-case ("yts"); Prowlarr's built-in ones aren't ("Knaben")
    schemas = {(s.get("definitionName") or "").lower(): s for s in arr.call("prowlarr", "GET", "/indexer/schema", timeout=60)}
    missing = [d for d in want if d.lower() not in schemas]
    added, broken = _add_working([schemas[d.lower()] for d in want if d.lower() in schemas], ours)
    broken += [f"{d} (Prowlarr has no such indexer)" for d in missing]
    if broken:
        raise Fail(f"added {', '.join(added) or 'none'}; couldn't reach {', '.join(broken)}. The next run tries again.")
    return "done", "added " + ", ".join(added)


def _add_working(defs, ours, room=None):
    """Try each definition (straight, then through FlareSolverr; a slow site can take a minute, so four
    at once) and add the ones that answer, up to `room`. Returns (added, broken)."""
    tag, profile = flaresolverr_tag(), arr.call("prowlarr", "GET", "/appprofile")[0]["id"]

    def tried(s):
        s = {**s, "enable": True, "appProfileId": profile, "priority": 25}
        why = "no answer"
        for tags in ([], [tag]):
            try:
                arr.call("prowlarr", "POST", "/indexer/test", json={**s, "tags": tags}, timeout=90)
                return s, tags, None
            except arr.ArrError as exc:
                why = str(exc).split(": ", 1)[-1]
        return s, None, why

    added, broken = [], []
    with ThreadPoolExecutor(4) as pool:
        for s, tags, why in pool.map(tried, defs):
            if why:
                broken.append(f"{s['name']} ({why})")
                continue
            if room is not None and len(added) >= room:
                break
            arr.call("prowlarr", "POST", "/indexer?forceSave=true", json={**s, "tags": tags}, timeout=90)
            added.append(s["name"] + (" via FlareSolverr" if tags else ""))
            ours.add(s["definitionName"].lower())
            core.set_setting("indexers_added", json.dumps(sorted(ours)))
    return added, broken


REFRESH, MAX_OURS, TRIES, DEAD = 12 * 3600, 15, 8, 3 * 86400
MEDIA = {2000, 3000, 5000}  # Newznab's movies, music and TV
ADULT = re.compile(r"hentai|xxx|porn|adult", re.I)


def _cats(s) -> set:
    return {c["id"] // 1000 * 1000 for c in (s.get("capabilities") or {}).get("categories", []) if isinstance(c.get("id"), int)}


def candidates(schemas) -> list:
    """Public torrent sites in English that carry movies, TV or music, the broadest first."""
    fit = [s for s in schemas if s.get("privacy") == "public" and s.get("protocol") == "torrent"
           and (s.get("language") or "en-US").startswith("en") and _cats(s) & MEDIA
           and not ADULT.search(f"{s.get('definitionName')} {s.get('name')} {s.get('description')}")]
    return sorted(fit, key=lambda s: (-len(_cats(s) & MEDIA), (s.get("definitionName") or "").lower()))


def _when(stamp) -> float:
    """Seconds since 1970 from an *arr's UTC time."""
    try:
        return float(calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return 0.0


def refresh_indexers(now: Optional[float] = None) -> Optional[dict]:
    """Twice a day: public indexers that work are added (up to MAX_OURS of Parlor's, TRIES new ones a run),
    Parlor's that have failed for three days are switched off, and ones it switched off come back once they
    answer again. One you deleted or switched off yourself stays that way; a Prowlarr with only its own
    indexers is left alone. Returns what changed, or None when it wasn't due."""
    now = now or time.time()
    if not INDEXERS or not arr.key("prowlarr") or now - float(setting("indexers_refreshed") or 0) < REFRESH:
        return None
    core.set_setting("indexers_refreshed", str(now))
    have = {(i.get("definitionName") or "").lower(): i for i in arr.call("prowlarr", "GET", "/indexer")}
    ours, off = set(json.loads(setting("indexers_added") or "[]")), set(json.loads(setting("indexers_off") or "[]"))
    report = {"at": now, "added": [], "off": [], "on": [], "note": ""}
    if have and not ours:
        report["note"] = "your own indexers: left alone"
    else:
        failing = {s["indexerId"]: _when(s.get("initialFailure")) for s in arr.call("prowlarr", "GET", "/indexerstatus") if s.get("initialFailure")}
        for d, i in have.items():
            if d not in ours:
                continue
            if i.get("enable") and failing.get(i["id"]) and now - failing[i["id"]] > DEAD:
                arr.call("prowlarr", "PUT", f"/indexer/{i['id']}?forceSave=true", json={**i, "enable": False})
                off.add(d)
                report["off"].append(i["name"])
            elif not i.get("enable") and d in off:
                try:
                    arr.call("prowlarr", "POST", "/indexer/test", json={**i, "enable": True}, timeout=90)
                except arr.ArrError:
                    continue
                arr.call("prowlarr", "PUT", f"/indexer/{i['id']}", json={**i, "enable": True})
                off.discard(d)
                report["on"].append(i["name"])
        core.set_setting("indexers_off", json.dumps(sorted(off)))
        room = MAX_OURS - len(ours & set(have))
        if room > 0:
            fresh = [s for s in candidates(arr.call("prowlarr", "GET", "/indexer/schema", timeout=60))
                     if (s.get("definitionName") or "").lower() not in set(have) | ours][:TRIES]
            report["added"], _ = _add_working(fresh, ours, room)
    core.set_setting("indexers_report", json.dumps(report))
    return report


# --- qBittorrent -----------------------------------------------------------------------------

def qbit_folders():
    base = qbit()
    r = http("GET", f"{base}/api/v2/app/preferences", timeout=15)
    if r.status_code in (401, 403):
        raise Fail("qBittorrent asks for a login. Restart the stack (docker compose up -d) so its setup step "
                   "opens it to the home network")
    prefs, changed = need(r, "qBittorrent").json(), []
    if (prefs.get("save_path") or "").rstrip("/") != "/data/downloads":
        need(http("POST", f"{base}/api/v2/app/setPreferences", data={"json": json.dumps({"save_path": "/data/downloads"})}), "qBittorrent")
        changed.append("saves to /data/downloads")
    cats = need(http("GET", f"{base}/api/v2/torrents/categories"), "qBittorrent").json()
    for app in ("sonarr", "radarr", "lidarr"):
        cat = arr.APPS[app]["category"][1]
        if cat not in cats:
            need(http("POST", f"{base}/api/v2/torrents/createCategory", data={"category": cat, "savePath": f"/data/downloads/{cat}"}), "qBittorrent")
            changed.append(f"category {cat}")
    return ("done", ", ".join(changed)) if changed else ("ok", "/data/downloads")


QBIT_NEW = """[BitTorrent]
Session\\DefaultSavePath=/data/downloads
Session\\TempPathEnabled=false

[LegalNotice]
Accepted=true
"""


def _ensure(lines, section, pairs):
    """Put key=value pairs in an INI section (qBittorrent's own format, kept as text); True if anything changed."""
    if f"[{section}]" not in lines:
        lines += ["", f"[{section}]"]
    start = lines.index(f"[{section}]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    changed, insert = False, []
    for k, v in pairs:
        at = next((i for i in range(start + 1, end) if lines[i].split("=", 1)[0] == k), None)
        if at is None:
            insert.append(f"{k}={v}")
            continue
        old = lines[at].split("=", 1)[1]
        if k.endswith("AuthSubnetWhitelist"):  # keep theirs, add ours
            v = ", ".join(dict.fromkeys([n.strip() for n in old.split(",") if n.strip()] + HOME_NETS))
        if old != v:
            lines[at], changed = f"{k}={v}", True
    lines[start + 1:start + 1] = insert
    return changed or bool(insert)


KINDS = ["movies", "shows", "music", "books", "games", "youtube", "downloads"]  # each is /data/<kind>, and .env can put it anywhere
CONFIG_DIRS = ["parlor", "sonarr", "radarr", "lidarr", "prowlarr", "bazarr", "jellyfin", "seerr", "cleanuparr", "qbittorrent"]


def _own(*paths):
    uid, gid = os.environ.get("PUID"), os.environ.get("PGID")
    if uid and gid and hasattr(os, "geteuid") and os.geteuid() == 0:
        for p in paths:
            os.chown(p, int(uid), int(gid), follow_symlinks=False)


def init_files(root: Optional[Path] = None) -> list:
    """Before the apps start (the compose's one-shot, with the compose folder at /compose):
    every app's ./<app>/config folder exists and belongs to PUID:PGID (Docker would make
    them root's, and Seerr, which doesn't run as root, couldn't write its own), and qBittorrent
    skips its login for home addresses (it reads its config only at start and rewrites it on
    exit, so this can't be done later)."""
    root, done = root or Path(os.environ.get("COMPOSE_DIR", "/compose")), []
    old, new = root / "yeerr" / "config", root / "parlor" / "config"  # Parlor was formerly yeerr: its settings move with the name
    if old.is_dir() and any(old.iterdir()) and not (new.is_dir() and any(new.iterdir())):
        if new.is_dir():
            new.rmdir()  # empty: Docker made it while creating the containers, before this ran
        new.parent.mkdir(exist_ok=True)
        os.replace(old, new)
        done.append("moved ./yeerr/config to ./parlor/config (formerly yeerr)")
    for app in CONFIG_DIRS:
        folder = root / app / "config"
        if not folder.is_dir():
            folder.mkdir(parents=True)
            _own(folder.parent, folder)
            done.append(f"{app}: made its config folder")
    for kind in KINDS:  # Docker makes a missing media folder root's when it mounts it
        if hand_over(DATA / kind):
            done.append(f"{kind}: folder handed to the apps' user")
    conf = root / "qbittorrent" / "config" / "qBittorrent" / "qBittorrent.conf"
    text = conf.read_text() if conf.is_file() else QBIT_NEW
    lines = text.rstrip().splitlines()
    keys = [("AuthSubnetWhitelistEnabled", "true"), ("AuthSubnetWhitelist", ", ".join(HOME_NETS)), ("LocalHostAuth", "false")]
    changed = _ensure(lines, "Preferences", [("WebUI\\" + k, v) for k, v in keys])  # qBittorrent 4
    changed = _ensure(lines, "WebUI", keys) or changed  # qBittorrent 5
    if changed or not conf.is_file():
        conf.parent.mkdir(parents=True, exist_ok=True)
        tmp = conf.with_suffix(".tmp")  # whole or not at all: qBittorrent won't start on half a file
        tmp.write_text("\n".join(lines) + "\n")
        _own(conf.parent, tmp)
        os.replace(tmp, conf)
        done.append("qBittorrent: no login on the home network")
    if install_vpn_script(root):
        done.append("gluetun: front door in place")
    return done


# --- Jellyfin ----------------------------------------------------------------------------------

def jf(method, path, key=None, token=None, **kw):
    # a key or a sign-in token both go in Authorization: Jellyfin 10.11 turned the old X-Emby-Token header off
    secret = key or token
    return http(method, jellyfin_url() + path, headers={"Authorization": JF_AUTH + (f', Token="{secret}"' if secret else "")}, **kw)


def jellyfin_wizard(name, password):
    if need(jf("GET", "/System/Info/Public"), "Jellyfin").json().get("StartupWizardCompleted"):
        return "ok", "set up"
    if not password:
        raise Wait("needs your password once: it becomes Jellyfin's admin login")
    for method, path, body in (
            ("POST", "/Startup/Configuration", {"UICulture": "en-US", "MetadataCountryCode": "US", "PreferredMetadataLanguage": "en"}),
            ("GET", "/Startup/User", None),  # Jellyfin wants this read before it's written
            ("POST", "/Startup/User", {"Name": name, "Password": password}),
            ("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False}),
            ("POST", "/Startup/Complete", None)):
        need(jf(method, path, json=body), "Jellyfin")
    return "done", f"admin {name}, with your Parlor password"


def jellyfin_key(name, password):
    key = setting("jellyfin_key")
    if key and jf("GET", "/System/Info", key=key).ok:
        return "ok", "Parlor has an API key"
    if not password:
        raise Wait("needs your password once to make an API key")
    r = jf("POST", "/Users/AuthenticateByName", json={"Username": name, "Pw": password})
    if r.status_code == 401:
        raise Fail(f"Jellyfin doesn't take {name} with your Parlor password; add a Jellyfin API key in Settings instead")
    token = need(r, "Jellyfin").json()["AccessToken"]
    mine = lambda: next((k for k in need(jf("GET", "/Auth/Keys", token=token), "Jellyfin").json()["Items"] if k.get("AppName") in ("Parlor", "yeerr")), None)  # compat: keys made before the rename  # noqa: E731
    found = mine()
    if not found:
        need(jf("POST", "/Auth/Keys", token=token, params={"app": "Parlor"}), "Jellyfin")
        found = mine()
    core.set_setting("jellyfin_key", found["AccessToken"])
    if not setting("jellyfin_url"):
        core.set_setting("jellyfin_url", jellyfin_url())
    return "done", "API key made and saved"


def jellyfin_libraries():
    key = setting("jellyfin_key")
    if not key:
        raise Wait("needs Jellyfin's API key first")
    folders = need(jf("GET", "/Library/VirtualFolders", key=key), "Jellyfin").json()
    have = {loc.rstrip("/") for f in folders for loc in f.get("Locations") or []}
    added = []
    for name, kind, path in LIBRARIES:
        if path in have:
            continue
        make_dir(path)
        need(jf("POST", "/Library/VirtualFolders", key=key, params={"name": name, "collectionType": kind, "paths": path, "refreshLibrary": "false"},
                json={"LibraryOptions": {"PathInfos": [{"Path": path}]}}), "Jellyfin")
        added.append(name)
    if not added:
        return "ok", f"{len(folders)} libraries"
    jf("POST", "/Library/Refresh", key=key)
    return "done", "added " + ", ".join(added)


# --- Seerr -------------------------------------------------------------------------------------

def seerr(method, path, **kw):
    kw.setdefault("headers", {})["X-Api-Key"] = setting("seerr_key")
    return need(http(method, f"{seerr_url()}/api/v1{path}", **kw), "Seerr")


def seerr_key_on_disk():
    try:
        return json.loads((STACK / "seerr" / "settings.json").read_text())["main"]["apiKey"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def seerr_setup(name, password):
    base = seerr_url()
    if need(http("GET", f"{base}/api/v1/settings/public"), "Seerr").json().get("initialized"):
        if setting("seerr_key"):
            return "ok", "set up"
        key = seerr_key_on_disk()
        if not key:
            raise Wait("Seerr was set up before; paste its API key in Settings")
        core.set_setting("seerr_url", base)
        core.set_setting("seerr_key", key)
        return "done", "API key read from Seerr's settings"
    if not setting("seerr_key"):  # a run that stopped half-way already has the key: carry on from there
        if not password:
            raise Wait("needs your password once: Seerr's admin signs in with your Jellyfin account")
        where_ = urlsplit(jellyfin_url())
        login = {"username": name, "password": password}
        r = http("POST", f"{base}/api/v1/auth/jellyfin", json={
            **login, "hostname": where_.hostname, "port": where_.port or 8096, "useSsl": where_.scheme == "https", "urlBase": where_.path,
            "email": f"{re.sub(r'[^a-z0-9]+', '', name.lower()) or 'admin'}@home.invalid", "serverType": 2})  # 2: Jellyfin
        if r.status_code >= 500 and "already configured" in r.text:  # Seerr knows Jellyfin already: just sign in
            r = http("POST", f"{base}/api/v1/auth/jellyfin", json=login)
        need(r, "Seerr's Jellyfin sign-in")
        main = need(http("GET", f"{base}/api/v1/settings/main", cookies=getattr(r, "cookies", None)), "Seerr").json()
        core.set_setting("seerr_url", base)
        core.set_setting("seerr_key", main["apiKey"])
    try:
        for lib in seerr("POST", "/settings/jellyfin/library/sync").json():
            if not lib.get("enabled"):
                seerr("PUT", f"/settings/jellyfin/library/{lib['id']}", json={"enabled": True})
    except Fail as exc:  # Jellyfin with no libraries yet: Seerr works without, and a later run syncs them
        if "NO_LIBRARIES" not in str(exc):
            raise
    seerr_apps()
    seerr("POST", "/settings/initialize")
    # you are Seerr's first user (its admin) and Jellyfin's admin: link your Parlor account to both
    core.db().execute("UPDATE users SET seerr_id = COALESCE(seerr_id, 1), jf_user = COALESCE(jf_user, ?) WHERE name = ?", (name, name))
    return "done", f"signed in as {name} through Jellyfin; libraries, Radarr and Sonarr connected"


def seerr_apps():
    """Radarr and Sonarr in Seerr, so requests reach them."""
    if not setting("seerr_key"):
        raise Wait("needs Seerr's API key first")
    added = []
    for app in ("radarr", "sonarr"):
        if not ours(app) or seerr("GET", f"/settings/{app}").json() or not arr.key(app):
            continue
        where_ = urlsplit(arr.url(app))
        conn = {"hostname": where_.hostname, "port": where_.port, "apiKey": arr.key(app), "useSsl": False, "baseUrl": ""}
        test = seerr("POST", f"/settings/{app}/test", json=conn).json()
        profile = next((p for p in test["profiles"] if p["name"] == "HD-1080p"), test["profiles"][0])
        body = {**conn, "name": arr.APPS[app]["label"], "activeProfileId": profile["id"], "activeProfileName": profile["name"],
                "activeDirectory": (roots(app) or [arr.APPS[app]["root"]])[0], "is4k": False, "isDefault": True, "syncEnabled": True, "externalUrl": ""}
        body.update({"minimumAvailability": "released"} if app == "radarr" else {"enableSeasonFolders": True, "seriesType": "standard"})
        seerr("POST", f"/settings/{app}", json=body)
        added.append(arr.APPS[app]["label"])
    return ("done", "connected " + " and ".join(added)) if added else ("ok", "Radarr and Sonarr connected")


# --- the best quality there is, and better copies as they appear -------------------------------------

BEST = "Best available"
# disc images, raw captures, cinema recordings and uncompressed audio: big, unplayable or worse than they sound
JUNK = {"BR-DISK", "Raw-HD", "WORKPRINT", "CAM", "TELESYNC", "TELECINE", "REGIONAL", "DVDSCR", "WAV"}
RECHECK = {"sonarr": "CutoffUnmetEpisodeSearch", "radarr": "CutoffUnmetMoviesSearch", "lidarr": "CutoffUnmetAlbumSearch"}
WEEK = 7 * 86400


def best_on() -> bool:
    return (setting("quality") or "best") == "best"


def best_profile(app, make=True) -> Optional[int]:
    """The app's 'Best available' profile: every quality but the junk, upgrades on, cutoff at the top."""
    found = next((p["id"] for p in arr.call(app, "GET", "/qualityprofile") if p["name"] == BEST), None)
    if found or not make:
        return found
    s = arr.call(app, "GET", "/qualityprofile/schema")
    for it in s["items"]:
        for q in it.get("items") or [it]:
            q["allowed"] = q["quality"]["name"] not in JUNK
        if it.get("items"):
            it["allowed"] = any(q["allowed"] for q in it["items"])
    top = [it for it in s["items"] if it["allowed"]][-1]  # the *arrs list qualities from worst to best
    s.update(name=BEST, upgradeAllowed=True, cutoff=top["id"] if top.get("items") else top["quality"]["id"])
    return arr.call(app, "POST", "/qualityprofile", json=s)["id"]


def _targets():
    """What decides the quality of new downloads: Seerr's Radarr and Sonarr, and Lidarr's root folder
    (Parlor's music requests use its default). Each: (key, app, label, current id, set(id))."""
    for app in ("radarr", "sonarr"):
        if setting("seerr_key") and ours("seerr") and ours(app):
            for s in seerr("GET", f"/settings/{app}").json():
                if s.get("hostname") != urlsplit(arr.url(app)).hostname:
                    continue  # an entry for a Radarr or Sonarr of yours elsewhere: its profiles aren't these
                put = lambda pid, s=s, app=app: seerr("PUT", f"/settings/{app}/{s['id']}", json={  # noqa: E731
                    **{k: v for k, v in s.items() if k != "id"},  # Seerr refuses its own id in the body
                    "activeProfileId": pid, "activeProfileName": BEST if pid == best_profile(app, make=False) else s.get("activeProfileName")})
                yield f"seerr_{app}_{s['id']}", app, f"Seerr's {arr.APPS[app]['label']}", s.get("activeProfileId"), put
    if arr.key("lidarr") and ours("lidarr"):
        for r in arr.call("lidarr", "GET", "/rootfolder")[:1]:
            put = lambda pid, r=r: arr.call("lidarr", "PUT", f"/rootfolder/{r['id']}", json={**r, "defaultQualityProfileId": pid})  # noqa: E731
            yield "lidarr_root", "lidarr", "music requests", r.get("defaultQualityProfileId"), put


def quality():
    """Best available for new downloads (or back to what each app had). Switched once and remembered:
    if you pick another profile in Seerr or Lidarr afterwards, yours stays."""
    before, changed, best = json.loads(setting("quality_before") or "{}"), [], {}
    for key, app, label, current, put in list(_targets()):
        if best_on():
            if app not in best:
                best[app] = best_profile(app)
            if key not in before:
                before[key] = current
                if current != best[app]:
                    put(best[app])
                    changed.append(label)
        elif key in before:
            if current is not None and current == best_profile(app, make=False):
                put(before[key])
                changed.append(label)
            del before[key]
    core.set_setting("quality_before", json.dumps(before))
    if not best_on():
        return ("done", "back to each app's own profile: " + ", ".join(changed)) if changed else ("ok", "each app's own profile")
    return ("done", "best available for " + ", ".join(changed)) if changed else ("ok", "best available, upgrading as better copies appear")


def recheck(now: Optional[float] = None) -> list:
    """Once a week each *arr searches again for what's below its profile's cutoff: better copies of what
    you have. (Their RSS sync already grabs upgrades that show up new.)"""
    now = now or time.time()
    if not best_on() or now - float(setting("quality_recheck") or 0) < WEEK:
        return []
    sent = []
    for app, command in RECHECK.items():
        if arr.key(app) and ours(app):
            try:
                arr.call(app, "POST", "/command", json={"name": command})
                sent.append(app)
            except arr.ArrError as exc:
                log.warning("upgrade search: %s", exc)
    if sent:
        core.set_setting("quality_recheck", str(now))
    return sent


def rechecker():
    """Started with Parlor in the stack: looks every hour whether the weekly upgrade search is due."""
    def loop():
        while True:
            time.sleep(3600)
            for chore in (recheck, refresh_indexers):
                try:
                    chore()
                except Exception:  # never let one bad hour stop the next
                    log.exception("stack chore %s", chore.__name__)
    threading.Thread(target=loop, daemon=True, name="stack-recheck").start()


# --- Bazarr and Cleanuparr ---------------------------------------------------------------------

def bazarr_key():
    try:
        text = (STACK / "bazarr" / "config" / "config.yaml").read_text()
    except OSError:
        return ""
    found = re.search(r"^auth:\n(?:[ \t]+.*\n)*?[ \t]+apikey:[ \t]*['\"]?([0-9a-fA-F]{16,64})", text, re.M)
    return found[1] if found else ""


def bazarr():
    key = bazarr_key()
    if not key:
        raise Wait("Bazarr hasn't started yet (no API key in its config)")
    url, headers = bazarr_url() + "/api/system/settings", {"X-API-KEY": key}
    now, form = need(http("GET", url, headers=headers), "Bazarr").json(), {}
    for app in ("sonarr", "radarr"):
        if not ours(app) or not arr.key(app) or (now.get("general", {}).get(f"use_{app}") and (now.get(app) or {}).get("apikey")):
            continue
        where_ = urlsplit(arr.url(app))
        form.update({f"settings-general-use_{app}": "true", f"settings-{app}-ip": where_.hostname, f"settings-{app}-port": str(where_.port),
                     f"settings-{app}-apikey": arr.key(app), f"settings-{app}-base_url": "/"})
    if not form:
        return "ok", "reads Sonarr and Radarr"
    need(http("POST", url, headers=headers, data=form), "Bazarr")
    return "done", "subtitles for Sonarr and Radarr"


def cleanuparr(name, password):
    base = cleanuparr_url()
    headers = {"X-Api-Key": setting("cleanuparr_key")} if setting("cleanuparr_key") else None
    if not headers:
        if not password:
            raise Wait("needs your password once: it becomes Cleanuparr's login")
        if not need(http("GET", f"{base}/api/auth/status"), "Cleanuparr").json().get("setupCompleted"):
            need(http("POST", f"{base}/api/auth/setup/account", json={"username": name, "password": password}), "Cleanuparr")
            need(http("POST", f"{base}/api/auth/setup/complete", json={}), "Cleanuparr")
        r = http("POST", f"{base}/api/auth/login", json={"username": name, "password": password})
        if r.status_code == 401:
            raise Fail(f"Cleanuparr has its own login and doesn't take {name} with your Parlor password")
        token = need(r, "Cleanuparr").json()["tokens"]["accessToken"]
        key = need(http("GET", f"{base}/api/account/api-key", headers={"Authorization": f"Bearer {token}"}), "Cleanuparr").json()["apiKey"]
        core.set_setting("cleanuparr_key", key)
        headers = {"X-Api-Key": key}
    get = lambda path: need(http("GET", base + path, headers=headers), "Cleanuparr").json()  # noqa: E731
    changed = []
    general = get("/api/configuration/general")
    if not general.get("auth", {}).get("disableAuthForLocalAddresses"):
        general["auth"] = {**general.get("auth", {}), "disableAuthForLocalAddresses": True}
        need(http("PUT", f"{base}/api/configuration/general", headers=headers, json=general), "Cleanuparr")
        changed.append("no login at home")
    for app in ("sonarr", "radarr", "lidarr"):
        if not ours(app) or not arr.key(app) or get(f"/api/configuration/{app}").get("instances"):
            continue
        need(http("POST", f"{base}/api/configuration/{app}/instances", headers=headers, json={
            "name": arr.APPS[app]["label"], "url": arr.url(app), "apiKey": arr.key(app), "enabled": True,
            "version": int(arr.call(app, "GET", "/system/status")["version"].split(".")[0])}), "Cleanuparr")
        changed.append(arr.APPS[app]["label"])
    if not any(c.get("typeName") == "qBittorrent" for c in get("/api/configuration/download_client").get("clients", [])):
        need(http("POST", f"{base}/api/configuration/download_client", headers=headers, json={
            "name": "qBittorrent", "typeName": "qBittorrent", "type": "Torrent", "host": qbit(), "enabled": True}), "Cleanuparr")
        changed.append("qBittorrent")
    return ("done", "connected " + ", ".join(changed)) if changed else ("ok", "watches the *arrs and qBittorrent")


BLOCKLIST = "https://cleanuparr.pages.dev/static/blacklist"  # Cleanuparr's own list of junk and malware file types
STALL_RULE = {"name": "Stalled", "enabled": True, "maxStrikes": 3, "privacyType": "Public", "minCompletionPercentage": 0,
              "maxCompletionPercentage": 100, "resetStrikesOnProgress": True, "minimumProgress": None,
              "deletePrivateTorrentsFromClient": False, "changeCategory": False}
SLOW_RULE = {"name": "Too slow", "enabled": True, "maxStrikes": 12, "minSpeed": "20KB", "maxTimeHours": 0, "privacyType": "Public",
             "minCompletionPercentage": 0, "maxCompletionPercentage": 100, "resetStrikesOnProgress": False,
             "ignoreWhileAltSpeedActive": True, "deletePrivateTorrentsFromClient": False, "changeCategory": False}  # an hour under 20 KB/s
SEEDING_RULE = {"name": "Done seeding", "categories": ["tv-sonarr", "radarr", "lidarr"], "trackerPatterns": [], "tagsAny": [], "tagsAll": [],
                "privacyType": "Public", "maxRatio": 2, "minSeedTime": 0, "maxSeedTime": 336, "minSeeders": 0, "maxInactiveDays": -1,
                "action": "Delete", "deleteSourceFiles": True}  # ratio 2 or two weeks; the library keeps its own hard link


def cleanuparr_jobs():
    """Cleanuparr's jobs, each set up once and only where you haven't set it yourself: failed, stalled,
    slow and stuck downloads are removed, blocklisted and searched for again; junk and malware files
    are blocked; finished torrents go after seeding. What you change or delete later stays that way."""
    key = setting("cleanuparr_key")
    if not key:
        raise Wait("needs Cleanuparr connected first")
    base, headers = cleanuparr_url(), {"X-Api-Key": key}
    call = lambda method, path, body=None: need(http(method, base + path, headers=headers, json=body), "Cleanuparr")  # noqa: E731
    get = lambda path: call("GET", path).json()  # noqa: E731
    added = set(json.loads(setting("cleanuparr_added") or "[]"))
    done = []

    def once(what, fn):
        if what not in added:
            if fn():
                done.append(what)
            added.add(what)
            core.set_setting("cleanuparr_added", json.dumps(sorted(added)))

    def queue_cleaner():
        q = get("/api/configuration/queue_cleaner")
        fi, errored = q.get("failedImport") or {}, q.get("errored") or {}
        if q.get("enabled") or fi.get("maxStrikes") or q.get("downloadingMetadataMaxStrikes") or errored.get("maxStrikes"):
            return False  # yours
        fi.update(maxStrikes=3, ignorePrivate=True, deletePrivate=False)
        if not fi.get("patterns"):
            fi["patternMode"] = "Exclude"  # no patterns: every failed import counts
        q.update(enabled=True, failedImport=fi, downloadingMetadataMaxStrikes=3, errored={**errored, "maxStrikes": 3, "ignorePrivate": True})
        call("PUT", "/api/configuration/queue_cleaner", q)
        return True

    def rule(kind, body):
        if get(f"/api/queue-rules/{kind}"):
            return False
        call("POST", f"/api/queue-rules/{kind}", body)
        return True

    def malware():
        m = get("/api/configuration/malware_blocker")
        if m.get("enabled") or any((m.get(a) or {}).get("enabled") for a in ("sonarr", "radarr", "lidarr")):
            return False
        m.update(enabled=True, ignorePrivate=True, deleteIfAnyFileBlocked=False)  # skip junk files; drop a torrent only if all of it is junk
        for a in ("sonarr", "radarr", "lidarr"):
            m[a] = {**(m.get(a) or {}), "enabled": True, "blocklistType": "Blacklist", "blocklistPath": BLOCKLIST}
        call("PUT", "/api/configuration/malware_blocker", m)
        return True

    def seeding():
        d = get("/api/configuration/download_cleaner")
        client = next((c for c in d.get("clients", []) if c.get("downloadClientTypeName") == "qBittorrent"), None)
        if not client:
            raise Wait("needs qBittorrent in Cleanuparr first")
        if d.get("enabled") or client.get("seedingRules"):
            return False
        call("POST", f"/api/seeding-rules/{client['downloadClientId']}", SEEDING_RULE)
        call("PUT", "/api/configuration/download_cleaner", {k: v for k, v in {**d, "enabled": True}.items() if k != "clients"})
        return True

    once("removes failed and stuck downloads", queue_cleaner)
    once("stalled ones", lambda: rule("stall", STALL_RULE))
    once("slow ones", lambda: rule("slow", SLOW_RULE))
    once("blocks malware", malware)
    once("clears torrents after seeding", seeding)
    if done:
        return "done", ", ".join(done) + "; each is searched for again"
    return "ok", "cleans failed, stalled and slow downloads, blocks malware, clears seeded torrents"


# --- running it ----------------------------------------------------------------------------------

def steps(name, password):
    for app in ("sonarr", "radarr", "lidarr", "prowlarr"):
        yield app, "login", lambda app=app: arr_login(app, name, password)
    for app in ("sonarr", "radarr", "lidarr"):
        yield app, "root folder", lambda app=app: arr_root(app)
        yield app, "download client", lambda app=app: arr_client(app)
    yield "qbittorrent", "folders", qbit_folders
    yield "prowlarr", "apps", prowlarr_apps
    yield "prowlarr", "flaresolverr", prowlarr_proxy
    yield "jellyfin", "first run", lambda: jellyfin_wizard(name, password)
    yield "jellyfin", "api key", lambda: jellyfin_key(name, password)
    yield "jellyfin", "libraries", jellyfin_libraries
    yield "seerr", "setup", lambda: seerr_setup(name, password)
    yield "seerr", "radarr and sonarr", seerr_apps
    yield "quality", "best available", quality
    yield "bazarr", "sonarr and radarr", bazarr
    yield "cleanuparr", "connections", lambda: cleanuparr(name, password)
    yield "cleanuparr", "cleanup jobs", cleanuparr_jobs
    yield "prowlarr", "indexers", prowlarr_indexers  # last: trying each site can take a minute


def run(name=None, password=None, into=None) -> list:
    """Every step in order; results go in `into` as they finish, so a page can show progress."""
    results = [] if into is None else into
    for app, step, fn in steps(name, password):
        started = time.time()
        mine = app not in SERVICES or ours(app)
        if not mine:
            fn = lambda app=app: ("ok", f"your own {arr.APPS[app]['label'] if app in arr.APPS else LABELS[app]} "  # noqa: E731
                                        f"at {urlsplit(address(app)).hostname}: left alone")
        try:
            status, detail = fn()
        except Wait as exc:
            status, detail = "skipped", str(exc)
        except (arr.ArrError, Fail) as exc:
            status, detail = "failed", str(exc)
        except requests.RequestException as exc:
            status, detail = "failed", f"didn't answer ({type(exc).__name__})"
        except Exception:  # one app's surprise mustn't stop the rest
            log.exception("stack step %s/%s", app, step)
            status, detail = "failed", "unexpected error; Parlor's log has the details"
        results.append({"app": app, "step": step, "status": status, "detail": detail, "seconds": round(time.time() - started, 1)})
        if status in ("done", "failed"):
            log.info("stack %s %s: %s (%s)", app, step, status, detail)
    return results


# failures that mean "not ready yet" (an app still starting, or one it talks to), worth another go
TRANSIENT = re.compile(r"didn.t answer|unable to connect|cannot connect|connection refused|said 5\d\d|timed out|starting|loading", re.I)
state = {"running": False, "results": [], "started": None, "finished": None}
_lock = threading.Lock()
_next: list = []  # a run asked for while one was going: it starts when that one ends


def start(name=None, password=None, retries=0) -> bool:
    """Run in the background; a run asked for meanwhile follows it (False: one is already waiting).
    `retries`: at start-up the apps may still be booting, so a run whose steps got no answer
    goes again a minute later."""
    with _lock:
        if state["running"]:
            if _next and not password:
                return False
            _next[:] = [(name, password, retries)]  # one with your password does everything one without would
            return True
        state.update(running=True, results=[], started=time.time(), finished=None)

    def go():
        try:
            for attempt in range(retries + 1):
                state["results"] = []
                run(name, password, state["results"])
                if not any(r["status"] == "failed" and r["step"] != "indexers" and TRANSIENT.search(r["detail"]) for r in state["results"]) or attempt == retries:
                    break
                if _next:  # a run with your password is waiting: it does everything this one would
                    break
                time.sleep(60)
        finally:
            with _lock:
                state.update(running=False, finished=time.time())
                follow = _next.pop() if _next else None
            if follow:
                start(*follow)

    threading.Thread(target=go, daemon=True, name="stack-setup").start()
    return True


# --- the VPN: Gluetun, behind a front door that keeps qBittorrent offline until there's a tunnel --

VPN_SCRIPT = Path(__file__).with_name("gluetun-front.sh")
VPN_WORDS = {
    "waiting": "No VPN file yet: qBittorrent reaches your home network, not the internet",
    "starting": "Starting the VPN: qBittorrent stays offline until the tunnel is up",
    "connecting": "Reconnecting: qBittorrent stays offline until the tunnel is back",
    "restarting": "Gluetun stopped; starting it again (qBittorrent stays offline)",
    "connected": "Connected: qBittorrent's traffic goes through the VPN only",
    "off": "VPN off: qBittorrent's traffic goes out directly",
}
OFF_FLAG = "vpn-off"
QUIET = 120  # the front door touches its state file every 30 s; this long without means it isn't running


def gluetun_dir() -> Path:
    return STACK / "gluetun"


def _key(value: str, what: str):
    try:
        if len(base64.b64decode(value, validate=True)) == 32:
            return
    except ValueError:
        pass
    raise ValueError(f"The {what} isn't a WireGuard key (44 characters of base64)")


def check_wireguard(text: str) -> str:
    """The file as Gluetun will read it, or ValueError in plain words: one [Interface] with a key and
    addresses, one [Peer] with a key and an endpoint. Gluetun needs the endpoint as an IP address, so a
    host name is looked up here."""
    if len(text) > 16_000:
        raise ValueError("That's too big for a WireGuard file")
    found, section, endpoint_at = {}, None, None
    lines = text.replace("\r\n", "\n").split("\n")
    for i, raw in enumerate(lines):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section == "peer" and "peer" in found:
                raise ValueError("Gluetun takes one [Peer]; this file has more")
            found.setdefault(section, {})
            continue
        if section is None or "=" not in line:
            raise ValueError("That isn't a WireGuard file (it should start with [Interface])")
        k, v = (s.strip() for s in line.split("=", 1))
        found[section][k.lower()] = v
        if section == "peer" and k.lower() == "endpoint":
            endpoint_at = i
    for section, keys in (("Interface", ("PrivateKey", "Address")), ("Peer", ("PublicKey", "Endpoint"))):
        for k in keys:
            if not found.get(section.lower(), {}).get(k.lower()):
                raise ValueError(f"The file has no {k} in [{section}]")
    me, peer = found["interface"], found["peer"]
    _key(me["privatekey"], "PrivateKey")
    _key(peer["publickey"], "PublicKey")
    if peer.get("presharedkey"):
        _key(peer["presharedkey"], "PresharedKey")
    try:
        for a in me["address"].split(","):
            ipaddress.ip_interface(a.strip())
    except ValueError as exc:
        raise ValueError(f"The Address in [Interface] should be like 10.64.0.2/32, not {me['address'][:40]}") from exc
    host, _, port = peer["endpoint"].rpartition(":")
    host = host.strip("[]")
    if not host or not (port.isascii() and port.isdecimal() and 0 < int(port) < 65536):
        raise ValueError("The [Peer] Endpoint should look like vpn.example.com:51820")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_DGRAM)[0][4][0])
        except (OSError, IndexError, UnicodeError) as exc:
            raise ValueError(f"Couldn't look up {host[:60]}; put the server's IP address in Endpoint instead") from exc
    ep = f"[{ip}]:{port}" if ip.version == 6 else f"{ip}:{port}"
    lines[endpoint_at] = f"Endpoint = {ep}"
    return "\n".join(lines).strip() + "\n"


def vpn_state(folder: Optional[Path] = None) -> dict:
    """What the front door last said, the server, and the switches; never the file's keys."""
    g = folder or gluetun_dir()
    conf, state = g / "wireguard" / "wg0.conf", g / "vpn.state"
    try:
        raw, age = state.read_text(errors="replace").strip(), time.time() - state.stat().st_mtime
    except OSError:
        raw, age = "", 0
    endpoint = None
    if conf.is_file():
        endpoint = next((ln.split("=", 1)[1].strip() for ln in conf.read_text(errors="replace").splitlines()
                         if "=" in ln and ln.split("=", 1)[0].strip().lower() == "endpoint"), None)
    if not raw:
        words = "Gluetun hasn't started yet (or is from before this Parlor)"
    elif age > QUIET:
        raw, words = "stopped", f"Gluetun isn't running: nothing heard from it for {int(age // 60)} minutes"
    elif raw.startswith("broken"):
        why = raw.split(": ", 1)[-1]
        words = "Can't run the VPN: " + why[:1].lower() + why[1:]
    else:
        words = VPN_WORDS.get(raw, raw)
    return {"available": g.is_dir(), "state": raw or "unknown", "words": words, "file": conf.is_file(),
            "endpoint": endpoint, "off": (g / OFF_FLAG).exists()}


def save_wireguard(text: str, folder: Optional[Path] = None) -> dict:
    """Checked, then swapped in whole (Gluetun's front door sees the change and restarts it);
    a file means you want the VPN, so the off switch goes back on."""
    g = folder or gluetun_dir()
    text = check_wireguard(text)
    (g / "wireguard").mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=g / "wireguard", prefix=".wg0.", suffix=".tmp")  # 0600, its own name
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, g / "wireguard" / "wg0.conf")
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    (g / OFF_FLAG).unlink(missing_ok=True)
    return vpn_state(g)


def vpn_off(off: bool, folder: Optional[Path] = None) -> dict:
    g = folder or gluetun_dir()
    if off:
        (g / OFF_FLAG).write_text("Turned off in Parlor; delete this file (or turn the VPN on in Parlor) to block again\n")
    else:
        (g / OFF_FLAG).unlink(missing_ok=True)
    return vpn_state(g)


def install_vpn_script(root: Path) -> bool:
    """The front door into ./gluetun (the compose starts Gluetun through it); True if it changed.
    A wg0.conf where the old compose kept it moves to where Gluetun reads it now."""
    g = root / "gluetun"
    (g / "wireguard").mkdir(parents=True, exist_ok=True)
    old, new = g / "wg0.conf", g / "wireguard" / "wg0.conf"
    if old.is_file() and not new.exists():
        os.replace(old, new)
    dest = g / "front-door.sh"
    script = VPN_SCRIPT.read_text()
    if dest.is_file() and dest.read_text() == script:
        return False
    dest.write_text(script)
    return True


def _vpn_ready():
    if not on() or not gluetun_dir().is_dir():
        raise HTTPException(409, "The VPN is set here only in Parlor's own compose (./gluetun isn't mounted)")


@router.get("/vpn")
def vpn(_=Depends(admin)):
    return vpn_state()


class WireGuard(BaseModel):
    conf: str


@router.put("/vpn")
def put_vpn(body: WireGuard, _=Depends(admin)):
    _vpn_ready()
    try:
        return save_wireguard(body.conf)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/vpn")
def delete_vpn(_=Depends(admin)):
    _vpn_ready()
    (gluetun_dir() / "wireguard" / "wg0.conf").unlink(missing_ok=True)
    return vpn_state()


class VpnOff(BaseModel):
    off: bool
    confirm: bool = False


@router.put("/vpn/off")
def put_vpn_off(body: VpnOff, _=Depends(admin)):
    _vpn_ready()
    if body.off and not body.confirm:
        raise HTTPException(400, "Turning the VPN off sends qBittorrent's traffic out directly: confirm it")
    return vpn_off(body.off)


# --- the Services page ---------------------------------------------------------------------

def reachable(url, **kw) -> bool:
    try:
        return http("GET", url, timeout=4, **kw).status_code < 500
    except requests.RequestException:
        return False


def status():
    keys = {"jellyfin": setting("jellyfin_key"), "seerr": setting("seerr_key"), "bazarr": bazarr_key(), "cleanuparr": setting("cleanuparr_key")}
    apps = [{"app": app, "label": arr.APPS[app]["label"], "url": arr.url(app), "key": bool(arr.key(app)), "probe": "/ping"} for app in arr.APPS]
    for app, label, url, probe in (("qbittorrent", "qBittorrent", qbit(), "/api/v2/app/version"), ("flaresolverr", "FlareSolverr", flaresolverr_url(), "/"),
                                   ("jellyfin", "Jellyfin", jellyfin_url(), "/System/Info/Public"), ("seerr", "Seerr", seerr_url(), "/api/v1/settings/public"),
                                   ("bazarr", "Bazarr", bazarr_url(), "/"), ("cleanuparr", "Cleanuparr", cleanuparr_url(), "/health")):
        apps.append({"app": app, "label": label, "url": url, "key": bool(keys[app]) if app in keys else None, "probe": probe})
    with ThreadPoolExecutor(len(apps)) as pool:  # all at once: a page shouldn't wait 4 s per app that's down
        for a, up in zip(apps, pool.map(lambda a: reachable(a["url"] + a.pop("probe")), apps)):
            a["up"] = up
    return apps


@router.get("")
def services(_=Depends(admin)):
    return {"on": on(), "apps": status(), "run": state, "quality": "best" if best_on() else "default",
            "indexers": json.loads(setting("indexers_report") or "null")}


class Quality(BaseModel):
    best: bool


@router.put("/quality")
def set_quality(body: Quality, _=Depends(admin)):
    core.set_setting("quality", "best" if body.best else "default")
    try:
        status_, detail = quality()
    except (arr.ArrError, Fail, Wait, requests.RequestException) as exc:
        return {"quality": "best" if body.best else "default", "detail": f"saved; the apps follow on the next setup run ({exc})"}
    return {"quality": "best" if body.best else "default", "detail": detail}


class Setup(BaseModel):
    password: Optional[str] = None


@router.post("/setup")
def setup(body: Setup, user=Depends(admin)):
    import auth  # auth imports this module

    if body.password:
        keys = (f"stack:{user['id']}",)
        if auth._throttled(*keys):
            raise HTTPException(429, "Too many wrong passwords. Wait a few minutes and try again.")
        stored = one("SELECT pw FROM users WHERE id = ?", user["id"])["pw"]
        # a Jellyfin account has no password here: Jellyfin checks it
        if not (core.check_password(body.password, stored) if stored else auth.jellyfin_login(user["name"], body.password) == user["id"]):
            auth._failed(*keys)
            raise HTTPException(403, "That isn't your password")
    return {"started": start(user["name"], body.password or None)}


if __name__ == "__main__" and sys.argv[1:] == ["init"]:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for line in init_files() or ["nothing to change"]:
        log.info("Parlor setup: %s", line)
