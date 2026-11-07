"""Setting up the whole stack: every step checks first, changes only what's missing, and says
what it did. The apps are one fake (an in-memory copy of each API's behaviour seen on real ones)."""
import json
import os
import re
import threading
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from yeerr_test_env import H, TMP, signed_in

import arr
import core
import stack

KEYS = {"sonarr": "s" * 32, "radarr": "r" * 32, "lidarr": "l" * 32, "prowlarr": "p" * 32}


class Resp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else ("" if body is None else json.dumps(body))
        self.content = self.text.encode()
        self.ok = status < 400
        self.headers = {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeStack:
    """Sonarr, Radarr, Lidarr, Prowlarr, qBittorrent, Jellyfin, Seerr, Bazarr and Cleanuparr, enough to set up."""

    def __init__(self, broken_indexers=(), needs_flaresolverr=(), qbit_open=True, roots=None, indexers=()):
        self.calls = []
        self.host = {a: {"id": 1, "authenticationMethod": "none", "authenticationRequired": "enabled", "username": "", "allowedHosts": ""}
                     for a in KEYS}
        self.roots = {a: list((roots or {}).get(a, [])) for a in ("sonarr", "radarr", "lidarr")}
        self.clients = {a: [] for a in ("sonarr", "radarr", "lidarr")}
        self.apps, self.proxies, self.tags, self.indexers = [], [], [], list(indexers)
        self.profiles = {a: [{"id": 1, "name": "Any"}] for a in KEYS}
        self.status = []  # Prowlarr's /indexerstatus: indexers that are failing
        self.commands = []
        self.broken, self.needs_fs = set(broken_indexers), set(needs_flaresolverr)
        self.qbit = {"open": qbit_open, "save_path": "/downloads/", "categories": {}}
        self.jf = {"wizard": False, "user": None, "keys": [], "libs": []}
        self.seerr = {"initialized": False, "key": "SEERRKEY", "libs": [{"id": "m1", "name": "Movies", "enabled": False}], "radarr": [], "sonarr": []}
        self.bazarr = {"use_sonarr": False, "use_radarr": False, "sonarr_key": "", "radarr_key": ""}
        self.cleanup = {"setup": False, "instances": {"sonarr": [], "radarr": [], "lidarr": []}, "clients": [], "local": False,
                        # Cleanuparr's jobs as a fresh one has them (shapes copied from Cleanuparr 2.x)
                        "queue_cleaner": {"enabled": False, "cronExpression": "0 0/5 * * * ?", "failedImport": {"maxStrikes": 0, "ignorePrivate": False,
                                          "deletePrivate": False, "patterns": [], "patternMode": "Include"}, "downloadingMetadataMaxStrikes": 0,
                                          "errored": {"maxStrikes": 0, "ignorePrivate": False, "deletePrivate": False}},
                        "malware_blocker": {"enabled": False, "ignorePrivate": False, "deleteIfAnyFileBlocked": False,
                                            **{a: {"enabled": False, "blocklistType": "Blacklist", "blocklistPath": None} for a in ("sonarr", "radarr", "lidarr", "readarr")}},
                        "download_cleaner": {"enabled": False, "cronExpression": "0 0 * * * ?"},
                        "stall": [], "slow": [], "seeding": {}}

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PUT", "DELETE")]

    def __call__(self, method, url, headers=None, params=None, json=None, data=None, files=None, timeout=None, **kw):
        u = urlsplit(url)
        host, path = u.hostname, u.path
        q = {**{k: v[0] for k, v in parse_qs(u.query).items()}, **(params or {})}
        self.calls.append((method, host, path))
        body = json
        app = host if host in KEYS else None
        if app:
            if (headers or {}).get("X-Api-Key") != KEYS[app]:
                return Resp(401, text="Unauthorized")
            return self.arr(app, method, re.sub(r"^/api/v\d", "", path), body)
        return getattr(self, "on_" + host)(method, path, q, body, headers or {}, data or files)

    # --- the *arrs
    def arr(self, app, method, path, body):
        if path == "/config/host":
            if method == "PUT":
                if body.get("authenticationRequired") != "enabled" and not body.get("allowedHosts"):
                    return Resp(400, [{"errorMessage": "Allowed Hosts is required"}])
                self.host[app].update(body)
            return Resp(200, dict(self.host[app]))
        if path == "/rootfolder":
            if method == "POST":
                if app == "lidarr" and not body.get("defaultMetadataProfileId"):
                    return Resp(400, [{"errorMessage": "Metadata profile required"}])
                self.roots[app].append({"id": len(self.roots[app]) + 1, **body})
                return Resp(201, body)
            return Resp(200, self.roots[app])
        if path == "/system/status":
            return Resp(200, {"version": {"sonarr": "4.0.15.2941", "radarr": "5.26.2.10099", "lidarr": "2.13.3.4711", "prowlarr": "2.0.5.5160"}[app]})
        if path == "/qualityprofile/schema":  # worst to best, as the real ones list them
            q = lambda i, n: {"quality": {"id": i, "name": n}, "allowed": False}  # noqa: E731
            return Resp(200, {"name": "", "upgradeAllowed": False, "cutoff": 0, "items": [
                q(0, "Unknown"), q(25, "CAM"), {"id": 1002, "name": "WEB 1080p", "allowed": False, "items": [q(3, "WEBDL-1080p"), q(15, "WEBRip-1080p")]},
                q(7, "Bluray-1080p"), q(31, "Remux-2160p"), q(22, "BR-DISK"), q(10, "Raw-HD")]})
        if path == "/qualityprofile":
            if method == "POST":
                self.profiles[app].append({**body, "id": len(self.profiles[app]) + 1})
                return Resp(201, self.profiles[app][-1])
            return Resp(200, self.profiles[app])
        if path == "/metadataprofile":
            return Resp(200, [{"id": 1, "name": "Any"}, {"id": 2, "name": "None"}])
        m = re.fullmatch(r"/rootfolder/(\d+)", path)
        if m and method == "PUT":
            next(r for r in self.roots[app] if r["id"] == int(m[1])).update(body)
            return Resp(202, body)
        if path == "/command" and method == "POST":
            self.commands.append((app, body["name"]))
            return Resp(201, {"name": body["name"], "status": "queued"})
        if path == "/downloadclient/schema":
            cat = arr.APPS[app]["category"][0]
            return Resp(200, [{"implementation": "Transmission", "fields": []},
                              {"implementation": "QBittorrent", "fields": [{"name": n, "value": None} for n in ("host", "port", "useSsl", cat)]}])
        if path == "/downloadclient":
            if method == "POST":
                self.clients[app].append(body)
                return Resp(201, body)
            return Resp(200, self.clients[app])
        if app == "prowlarr":
            return self.prowlarr(method, path, body)
        return Resp(404, text="no such route")

    def prowlarr(self, method, path, body):
        field = lambda o, n: next((f.get("value") for f in o.get("fields", []) if f["name"] == n), None)  # noqa: E731
        if path == "/applications/schema":
            return Resp(200, [{"implementation": n, "fields": [{"name": k, "value": None} for k in ("prowlarrUrl", "baseUrl", "apiKey")]}
                              for n in ("Sonarr", "Radarr", "Lidarr", "Readarr")])
        if path == "/applications":
            if method == "POST":
                self.apps.append(body)
                return Resp(201, body)
            return Resp(200, self.apps)
        if path == "/tag":
            if method == "POST":
                self.tags.append({"id": len(self.tags) + 1, **body})
                return Resp(201, self.tags[-1])
            return Resp(200, self.tags)
        if path == "/indexerProxy/schema":
            return Resp(200, [{"implementation": "FlareSolverr", "fields": [{"name": "host"}, {"name": "requestTimeout"}]}])
        if path == "/indexerProxy":
            if method == "POST":
                self.proxies.append(body)
                return Resp(201, body)
            return Resp(200, self.proxies)
        if path == "/indexer/schema":  # Prowlarr knows hundreds; a few of each kind
            d = lambda name, privacy="public", lang="en-US", cats=(2000, 5000), desc="": {  # noqa: E731
                "definitionName": name, "name": name.upper(), "fields": [{"name": "baseUrl"}], "privacy": privacy, "protocol": "torrent",
                "language": lang, "description": desc, "capabilities": {"categories": [{"id": c} for c in cats]}}
            return Resp(200, [d("yts", cats=(2000,)), d("eztv", cats=(5000,)), d("1337x", cats=(2000, 3000, 5000, 6000)), d("Nyaasi", cats=(5000,)),
                              d("kickass", cats=(2000, 3000, 5000)), d("musicsite", cats=(3000,)), d("bookbay", cats=(7000,)),
                              d("rutracker", privacy="private"), d("zhsite", lang="zh-CN"), d("pictures", desc="hentai and more"),
                              *[d(f"extra{i}") for i in range(20)]])
        if path == "/indexerstatus":
            return Resp(200, self.status)
        m = re.fullmatch(r"/indexer/(\d+)", path)
        if m and method == "PUT":
            next(i for i in self.indexers if i["id"] == int(m[1])).update(body)
            return Resp(202, body)
        if path == "/appprofile":
            return Resp(200, [{"id": 1, "name": "Standard"}])
        if path == "/indexer" and method == "GET":
            return Resp(200, self.indexers)
        if path in ("/indexer/test", "/indexer"):
            d = body["definitionName"].lower()
            if d in self.broken or (d in self.needs_fs and not body.get("tags")):
                return Resp(400, [{"errorMessage": "Unable to connect to indexer"}])
            if path == "/indexer":
                self.indexers.append({**body, "id": len(self.indexers) + 1})
            return Resp(201 if path == "/indexer" else 200, body)
        return Resp(404, text="no such route")

    # --- qBittorrent (behind gluetun)
    def on_gluetun(self, method, path, q, body, headers, form):
        if not self.qbit["open"]:
            return Resp(403, text="Forbidden")
        if path == "/api/v2/app/preferences":
            return Resp(200, {"save_path": self.qbit["save_path"]})
        if path == "/api/v2/app/setPreferences":
            self.qbit.update(json.loads(form["json"]))
            return Resp(200, text="")
        if path == "/api/v2/torrents/categories":
            return Resp(200, self.qbit["categories"])
        if path == "/api/v2/torrents/createCategory":
            self.qbit["categories"][form["category"]] = {"savePath": form["savePath"]}
            return Resp(200, text="")
        return Resp(404, text="")

    # --- Jellyfin
    def on_jellyfin(self, method, path, q, body, headers, form):
        token = re.search(r'Token="([^"]*)"', headers.get("Authorization", ""))  # 10.11 ignores X-Emby-Token
        authed = bool(token) and token[1] in ["TOKEN"] + [k["AccessToken"] for k in self.jf["keys"]]
        if path == "/System/Info/Public":
            return Resp(200, {"StartupWizardCompleted": self.jf["wizard"], "Version": "10.11.0"})
        if path.startswith("/Startup/"):
            if self.jf["wizard"]:
                return Resp(401, text="")
            if path == "/Startup/User" and method == "POST":
                self.jf["user"] = (body["Name"], body["Password"])
            if path == "/Startup/Complete":
                self.jf["wizard"] = True
            return Resp(204 if method == "POST" else 200, None if method == "POST" else {"Name": "abc"}, text="" if method == "POST" else None)
        if path == "/Users/AuthenticateByName":
            ok = self.jf["user"] == (body["Username"], body["Pw"])
            return Resp(200, {"AccessToken": "TOKEN", "User": {"Id": "u1"}}) if ok else Resp(401, text="")
        if not authed:
            return Resp(401, text="")
        if path == "/System/Info":
            return Resp(200, {"Version": "10.11.0"})
        if path == "/Auth/Keys":
            if method == "POST":
                self.jf["keys"].append({"AppName": q["app"], "AccessToken": "J" * 32})
                return Resp(204, text="")
            return Resp(200, {"Items": self.jf["keys"]})
        if path == "/Library/VirtualFolders":
            if method == "POST":
                self.jf["libs"].append({"Name": q["name"], "CollectionType": q["collectionType"], "Locations": [q["paths"]]})
                return Resp(204, text="")
            return Resp(200, self.jf["libs"])
        if path == "/Library/Refresh":
            return Resp(204, text="")
        return Resp(404, text="")

    # --- Seerr
    def on_seerr(self, method, path, q, body, headers, form):
        p = path.replace("/api/v1", "")
        if p == "/settings/public":
            return Resp(200, {"initialized": self.seerr["initialized"]})
        if p == "/auth/jellyfin":
            if "hostname" in body and self.seerr.get("jf_set"):
                return Resp(500, {"message": "Jellyfin hostname already configured"})
            if self.jf["user"] != (body["username"], body["password"]):
                return Resp(401, {"message": "Unauthorized"})
            self.seerr["jf_set"] = self.seerr.get("jf_set") or "hostname" in body
            return Resp(200, {"id": 1})
        if headers.get("X-Api-Key") not in (None, self.seerr["key"]) or (headers.get("X-Api-Key") is None and not self.cookie_ok):
            return Resp(401, {"message": "no"})
        if p == "/settings/main":
            return Resp(200, {"apiKey": self.seerr["key"]})
        if p == "/settings/jellyfin/library/sync":
            return Resp(200, self.seerr["libs"])
        m = re.fullmatch(r"/settings/jellyfin/library/(\w+)", p)
        if m:
            lib = next(x for x in self.seerr["libs"] if x["id"] == m[1])
            lib.update(body)
            return Resp(200, lib)
        m = re.fullmatch(r"/settings/(radarr|sonarr)(/test)?", p)
        if m:
            if m[2]:
                return Resp(200, {"profiles": [{"id": 1, "name": "Any"}, {"id": 4, "name": "HD-1080p"}], "rootFolders": [{"path": arr.APPS[m[1]]["root"]}]})
            if method == "POST":
                self.seerr[m[1]].append({**body, "id": len(self.seerr[m[1]])})
                return Resp(201, body)
            return Resp(200, self.seerr[m[1]])
        m = re.fullmatch(r"/settings/(radarr|sonarr)/(\d+)", p)
        if m and method == "PUT":
            if "id" in body:  # as the real one says
                return Resp(400, {"message": "request/body/id is read-only"})
            self.seerr[m[1]][int(m[2])].update(body)
            return Resp(200, self.seerr[m[1]][int(m[2])])
        if p == "/settings/initialize":
            self.seerr["initialized"] = True
            return Resp(200, {"initialized": True})
        return Resp(404, {"message": "?"})

    cookie_ok = True  # the session the Jellyfin sign-in starts

    # --- Bazarr
    def on_bazarr(self, method, path, q, body, headers, form):
        if headers.get("X-API-KEY") != "B" * 32:
            return Resp(401, text="")
        if method == "GET":
            return Resp(200, {"general": {"use_sonarr": self.bazarr["use_sonarr"], "use_radarr": self.bazarr["use_radarr"]},
                              "sonarr": {"apikey": self.bazarr["sonarr_key"]}, "radarr": {"apikey": self.bazarr["radarr_key"]}})
        for k, v in form.items():
            if k.startswith("settings-general-use_"):
                self.bazarr[k.split("-")[-1]] = v == "true"
            if k.endswith("-apikey"):
                self.bazarr[k.split("-")[1] + "_key"] = v
        return Resp(204, text="")

    # --- Cleanuparr
    def on_cleanuparr(self, method, path, q, body, headers, form):
        if path == "/api/auth/status":
            return Resp(200, {"setupCompleted": self.cleanup["setup"]})
        if path == "/api/auth/setup/account":
            self.cleanup["user"] = (body["username"], body["password"])
            return Resp(201, {})
        if path == "/api/auth/setup/complete":
            self.cleanup["setup"] = True
            return Resp(200, {})
        if path == "/api/auth/login":
            ok = self.cleanup.get("user") == (body["username"], body["password"])
            return Resp(200, {"tokens": {"accessToken": "CT"}}) if ok else Resp(401, {})
        if path == "/api/account/api-key" and headers.get("Authorization") == "Bearer CT":
            return Resp(200, {"apiKey": "CK"})
        if headers.get("Authorization") != "Bearer CT" and headers.get("X-Api-Key") != "CK":
            return Resp(401, text="")
        if path == "/api/configuration/general":
            if method == "PUT":
                self.cleanup["local"] = body["auth"]["disableAuthForLocalAddresses"]
            return Resp(200, {"auth": {"disableAuthForLocalAddresses": self.cleanup["local"], "trustedNetworks": []}, "dryRun": False})
        m = re.fullmatch(r"/api/configuration/(sonarr|radarr|lidarr)(/instances)?", path)
        if m:
            if method == "POST":
                self.cleanup["instances"][m[1]].append(body)
                return Resp(201, body)
            return Resp(200, {"instances": self.cleanup["instances"][m[1]]})
        if path in ("/api/configuration/queue_cleaner", "/api/configuration/malware_blocker", "/api/configuration/download_cleaner"):
            job = path.rsplit("/", 1)[1]
            if method == "PUT":
                fi = body.get("failedImport") or {}
                if body["enabled"] and fi.get("maxStrikes") and fi.get("patternMode") == "Include" and not fi.get("patterns"):
                    return Resp(400, {"detail": "At least one pattern must be specified when using the Include pattern mode"})
                self.cleanup[job] = body
                return Resp(200, {"message": "updated"})
            out = dict(self.cleanup[job])
            if job == "download_cleaner":
                out["clients"] = [{"downloadClientId": f"dc{i}", "downloadClientTypeName": c["typeName"], "seedingRules": self.cleanup["seeding"].get(f"dc{i}", [])}
                                  for i, c in enumerate(self.cleanup["clients"])]
            return Resp(200, out)
        m = re.fullmatch(r"/api/queue-rules/(stall|slow)", path)
        if m:
            if method == "POST":
                self.cleanup[m[1]].append({**body, "id": f"r{len(self.cleanup[m[1]])}"})
                return Resp(201, self.cleanup[m[1]][-1])
            return Resp(200, self.cleanup[m[1]])
        m = re.fullmatch(r"/api/seeding-rules/(dc\d+)", path)
        if m and method == "POST":
            self.cleanup["seeding"].setdefault(m[1], []).append(body)
            return Resp(201, body)
        if path == "/api/configuration/download_client":
            if method == "POST":
                self.cleanup["clients"].append(body)
                return Resp(201, body)
            return Resp(200, {"clients": self.cleanup["clients"]})
        return Resp(404, text="")

    def on_flaresolverr(self, *a):
        return Resp(200, {"msg": "FlareSolverr is ready!"})


def stack_dir():
    """The apps' config folders as the compose mounts them (read-only) for yeerr."""
    root = TMP / "stack"
    for app, k in KEYS.items():
        (root / app).mkdir(parents=True, exist_ok=True)
        (root / app / "config.xml").write_text(f"<Config>\n  <Port>1</Port>\n  <ApiKey>{k}</ApiKey>\n</Config>\n")
    (root / "bazarr" / "config").mkdir(parents=True, exist_ok=True)
    (root / "bazarr" / "config" / "config.yaml").write_text("analytics:\n  enabled: false\nauth:\n  apikey: " + "B" * 32 + "\n  type: null\n")
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeStack(**getattr(self, "fake_args", {}))
        for k in ("jellyfin_url", "jellyfin_key", "seerr_url", "seerr_key", "cleanuparr_key", "indexers_added", "cleanuparr_added", "quality", "quality_before", "quality_recheck", "indexers_refreshed", "indexers_off", "indexers_report"):
            core.db().execute("DELETE FROM settings WHERE key = ?", (k,))
        patches = [mock.patch.object(stack.requests, "request", self.fake), mock.patch.object(arr.requests, "request", self.fake),
                   mock.patch.object(arr, "STACK", stack_dir()), mock.patch.object(stack, "STACK", stack_dir()),
                   mock.patch.object(stack, "DATA", TMP / "media"), mock.patch.dict("os.environ", {"STACK": "1"}),
                   mock.patch.object(stack, "INDEXERS", ["yts", "eztv", "1337x", "nyaasi"])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        arr._keys.clear()

    def run_all(self, password="correct horse"):
        return {(r["app"], r["step"]): r for r in stack.run("admin", password)}


class Everything(Base):
    def test_a_fresh_stack_ends_fully_wired_and_a_second_run_changes_nothing(self):
        first = self.run_all()
        failed = {k: r["detail"] for k, r in first.items() if r["status"] == "failed"}
        self.assertEqual(failed, {})
        f = self.fake
        # no logins at home, with yeerr's admin name and password for outside
        for app in KEYS:
            self.assertEqual((f.host[app]["authenticationMethod"], f.host[app]["authenticationRequired"], f.host[app]["username"]),
                             ("forms", "disabledForLocalAddresses", "admin"), app)
        self.assertEqual([r["path"] for r in f.roots["sonarr"]], ["/data/shows"])
        self.assertEqual(f.roots["lidarr"][0]["defaultMetadataProfileId"], 1, "Lidarr gets a real metadata profile, not 'None'")
        self.assertTrue((TMP / "media" / "movies").is_dir(), "the root folders exist on disk")
        client = f.clients["radarr"][0]
        self.assertEqual({x["name"]: x["value"] for x in client["fields"]}, {"host": "gluetun", "port": 8080, "useSsl": False, "movieCategory": "radarr"})
        self.assertEqual(f.qbit["save_path"], "/data/downloads")
        self.assertEqual(set(f.qbit["categories"]), {"tv-sonarr", "radarr", "lidarr"})
        self.assertEqual({a["implementation"]: next(x["value"] for x in a["fields"] if x["name"] == "baseUrl") for a in f.apps},
                         {"Sonarr": "http://sonarr:8989", "Radarr": "http://radarr:7878", "Lidarr": "http://lidarr:8686"})
        self.assertEqual(f.proxies[0]["tags"], [f.tags[0]["id"]])
        self.assertEqual({i["definitionName"] for i in f.indexers}, {"yts", "eztv", "1337x", "Nyaasi"}, "built-in indexers have capitals")
        self.assertEqual(f.jf["user"], ("admin", "correct horse"), "Jellyfin's admin is you, with your password")
        self.assertEqual({l["Locations"][0] for l in f.jf["libs"]}, {"/data/movies", "/data/shows", "/data/music", "/data/youtube", "/data/books"})
        self.assertEqual(core.setting("jellyfin_key"), "J" * 32)
        self.assertTrue(f.seerr["initialized"])
        self.assertEqual(core.setting("seerr_key"), "SEERRKEY")
        me = core.one("SELECT seerr_id, jf_user FROM users WHERE name = 'admin'")
        self.assertEqual((me["seerr_id"], me["jf_user"]), (1, "admin"), "your yeerr account is linked to the Seerr and Jellyfin admin it made")
        self.assertTrue(f.seerr["libs"][0]["enabled"])
        self.assertEqual((f.seerr["radarr"][0]["activeProfileName"], f.seerr["radarr"][0]["activeDirectory"]), (stack.BEST, "/data/movies"), "Seerr asks for the best there is")
        self.assertEqual((f.bazarr["use_sonarr"], f.bazarr["sonarr_key"], f.bazarr["radarr_key"]), (True, KEYS["sonarr"], KEYS["radarr"]))
        self.assertTrue(f.cleanup["local"])
        self.assertEqual({k: len(v) for k, v in f.cleanup["instances"].items()}, {"sonarr": 1, "radarr": 1, "lidarr": 1})
        self.assertEqual(len(f.cleanup["clients"]), 1)
        # Cleanuparr's jobs: failed, stalled and slow downloads go and are searched again; malware blocked; seeding cleared
        q = f.cleanup["queue_cleaner"]
        self.assertTrue(q["enabled"])
        self.assertEqual((q["failedImport"]["maxStrikes"], q["failedImport"]["patternMode"], q["downloadingMetadataMaxStrikes"], q["errored"]["maxStrikes"]), (3, "Exclude", 3, 3))
        self.assertEqual([(r["name"], r["maxStrikes"]) for r in f.cleanup["stall"]], [("Stalled", 3)])
        self.assertEqual(f.cleanup["slow"][0]["minSpeed"], "20KB")
        mb = f.cleanup["malware_blocker"]
        self.assertTrue(mb["enabled"] and not mb["deleteIfAnyFileBlocked"], "junk files are skipped; a torrent goes only when everything in it is blocked")
        self.assertEqual({a for a in ("sonarr", "radarr", "lidarr", "readarr") if mb[a]["enabled"]}, {"sonarr", "radarr", "lidarr"})
        self.assertEqual(mb["radarr"]["blocklistPath"], stack.BLOCKLIST)
        rule = f.cleanup["seeding"]["dc0"][0]
        self.assertEqual((rule["categories"], rule["maxRatio"], rule["action"]), (["tv-sonarr", "radarr", "lidarr"], 2, "Delete"))
        self.assertTrue(f.cleanup["download_cleaner"]["enabled"])
        # best quality: a profile in each *arr, what Seerr and music requests ask for
        for app in ("sonarr", "radarr", "lidarr"):
            best = next(p for p in f.profiles[app] if p["name"] == stack.BEST)
            allowed = [q["quality"]["name"] for it in best["items"] for q in (it.get("items") or [it]) if q["allowed"]]
            self.assertEqual(allowed, ["Unknown", "WEBDL-1080p", "WEBRip-1080p", "Bluray-1080p", "Remux-2160p"], "no cinema recordings, disc images or raw captures")
            self.assertEqual((best["cutoff"], best["upgradeAllowed"]), (31, True), "keeps upgrading up to the best there is")
        self.assertEqual({(s["activeProfileId"], s["activeProfileName"]) for s in f.seerr["radarr"] + f.seerr["sonarr"]}, {(2, stack.BEST)})
        self.assertEqual(f.roots["lidarr"][0]["defaultQualityProfileId"], 2)

        before = len(f.writes())
        again = self.run_all()
        self.assertEqual(f.writes()[before:], [], "a second run changes nothing")
        self.assertTrue(all(r["status"] in ("ok", "skipped") for r in again.values()), {k: r for k, r in again.items() if r["status"] not in ("ok", "skipped")})

    def test_a_removed_piece_is_put_back(self):
        self.run_all()
        self.fake.clients["sonarr"].clear()
        self.fake.apps[:] = [a for a in self.fake.apps if a["implementation"] != "Lidarr"]
        again = self.run_all()
        self.assertEqual(again[("sonarr", "download client")]["status"], "done")
        self.assertEqual(again[("prowlarr", "apps")]["status"], "done")
        self.assertEqual(len(self.fake.clients["sonarr"]), 1)

    def test_without_your_password_only_the_login_steps_wait(self):
        r = {(x["app"], x["step"]): x for x in stack.run(None, None)}
        self.assertEqual(r[("sonarr", "login")]["status"], "skipped")
        self.assertEqual(r[("jellyfin", "first run")]["status"], "skipped")
        self.assertEqual(r[("sonarr", "root folder")]["status"], "done")
        self.assertEqual(r[("prowlarr", "indexers")]["status"], "done")
        self.assertEqual(self.fake.host["sonarr"]["authenticationMethod"], "none")

    def test_an_app_with_its_own_login_keeps_it(self):
        self.fake.host["radarr"].update(authenticationMethod="forms", authenticationRequired="enabled", username="theirs")
        r = self.run_all()
        self.assertEqual(r[("radarr", "login")]["status"], "ok")
        self.assertEqual(self.fake.host["radarr"]["username"], "theirs")


class Quality(Base):
    def test_your_change_stays_and_switching_off_puts_back_what_was_there(self):
        self.run_all()
        f = self.fake
        f.seerr["sonarr"][0].update(activeProfileId=1, activeProfileName="Any")  # you chose Any for shows
        self.run_all()
        self.assertEqual(f.seerr["sonarr"][0]["activeProfileId"], 1, "your choice stays")
        admin = signed_in("admin")
        r = admin.put("/api/stack/quality", json={"best": False}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(f.seerr["radarr"][0]["activeProfileId"], 4, "back to what Seerr had (HD-1080p)")
        self.assertEqual(f.seerr["sonarr"][0]["activeProfileId"], 1, "not touched: it wasn't ours any more")
        self.assertEqual(f.roots["lidarr"][0]["defaultQualityProfileId"], 1)
        self.assertEqual(admin.get("/api/stack").json()["quality"], "default")
        self.assertEqual(stack.recheck(), [], "no upgrade searches when it's off")
        admin.put("/api/stack/quality", json={"best": True}, headers=H)
        self.assertEqual(f.seerr["radarr"][0]["activeProfileId"], 2)

    def test_upgrades_are_searched_for_weekly(self):
        self.run_all()
        core.set_setting("quality_recheck", "")
        self.assertEqual(stack.recheck(now=1_000_000), ["sonarr", "radarr", "lidarr"])
        self.assertEqual(self.fake.commands, [("sonarr", "CutoffUnmetEpisodeSearch"), ("radarr", "CutoffUnmetMoviesSearch"), ("lidarr", "CutoffUnmetAlbumSearch")])
        self.assertEqual(stack.recheck(now=1_000_000 + 6 * 86400), [], "not again within the week")
        self.assertEqual(len(stack.recheck(now=1_000_000 + 8 * 86400)), 3)


class Yours(Base):
    """Settings pointing Parlor at your own Seerr, Jellyfin or Sonarr elsewhere: setup leaves those alone."""
    OUTSIDE = {"seerr_url": "https://seerr.example.test", "jellyfin_url": "http://media.example.test:8096", "sonarr_url": "http://nas.example.test:8989"}

    def setUp(self):
        super().setUp()
        for k, v in self.OUTSIDE.items():
            core.set_setting(k, v)
        self.addCleanup(lambda: [core.db().execute("DELETE FROM settings WHERE key = ?", (k,)) for k in self.OUTSIDE])

    def test_your_own_servers_are_left_alone(self):
        r = self.run_all()
        hosts = {c[1] for c in self.fake.calls}
        self.assertFalse(hosts & {"seerr.example.test", "media.example.test", "nas.example.test"}, "nothing sent to them at all")
        for key in (("seerr", "setup"), ("jellyfin", "libraries"), ("sonarr", "login"), ("sonarr", "root folder")):
            self.assertEqual(r[key]["status"], "ok", key)
            self.assertIn("left alone", r[key]["detail"])
        self.assertNotIn("Sonarr", {a["implementation"] for a in self.fake.apps}, "Prowlarr isn't told to fill your own Sonarr")
        self.assertEqual(set(self.fake.cleanup["instances"]["sonarr"]), set(), "nor is Cleanuparr told to clean it")
        self.assertEqual(r[("radarr", "root folder")]["status"], "done", "the stack's own apps are still set up")


class SeerrOfYourOwnArrs(Base):
    def test_a_seerr_entry_for_another_radarr_keeps_its_profile(self):
        self.run_all()
        self.fake.seerr["radarr"].append({"id": 1, "hostname": "nas.example.test", "port": 7878, "activeProfileId": 7, "activeProfileName": "Mine"})
        core.db().execute("DELETE FROM settings WHERE key = 'quality_before'")
        self.run_all()
        self.assertEqual(self.fake.seerr["radarr"][1]["activeProfileId"], 7)


class CleanuparrYourWay(Base):
    def test_your_own_cleanup_settings_and_deletions_stay(self):
        self.run_all()
        f = self.fake
        f.cleanup["queue_cleaner"] = {**f.cleanup["queue_cleaner"], "enabled": False}  # you turned it off
        f.cleanup["stall"].clear()  # and deleted the stall rule
        before = len(f.writes())
        r = self.run_all()[("cleanuparr", "cleanup jobs")]
        self.assertEqual(r["status"], "ok", r["detail"])
        self.assertEqual(f.writes()[before:], [], "nothing put back that you took away")


class HalfDone(Base):
    def test_a_run_that_stopped_half_way_is_finished_next_time(self):
        self.fake.seerr["jf_set"] = True  # an earlier run got as far as Seerr's Jellyfin sign-in
        r = self.run_all()
        self.assertEqual(r[("seerr", "setup")]["status"], "done", r[("seerr", "setup")]["detail"])
        self.assertTrue(self.fake.seerr["initialized"])


class Indexers(Base):
    fake_args = {"broken_indexers": {"1337x"}, "needs_flaresolverr": {"nyaasi"}}

    def test_working_ones_kept_flaresolverr_when_needed_broken_reported(self):
        r = self.run_all()[("prowlarr", "indexers")]
        self.assertEqual(r["status"], "failed")
        self.assertIn("1337X", r["detail"])
        added = {i["definitionName"].lower(): i["tags"] for i in self.fake.indexers}
        self.assertEqual(set(added), {"yts", "eztv", "nyaasi"})
        self.assertEqual(added["nyaasi"], [self.fake.tags[0]["id"]], "only the one that needed it goes through FlareSolverr")
        self.assertEqual(added["yts"], [])


class FreshIndexers(Base):
    def test_new_working_ones_added_dead_ones_rest_and_come_back(self):
        f = self.fake
        self.run_all()
        start = len(f.indexers)
        report = stack.refresh_indexers(now=1_000_000)
        names = [i["definitionName"] for i in f.indexers[start:]]
        self.assertEqual(names[:2], ["kickass", "extra0"], "the broadest first")
        self.assertEqual(len(names), stack.TRIES, "a few new tries per run")
        stack.refresh_indexers(now=1_000_000 + 13 * 3600)
        self.assertEqual(len(f.indexers), stack.MAX_OURS, "and no more than this many of yeerr's")
        self.assertNotIn("musicsite", names, "music-only ones come after the broad ones")
        for skipped in ("rutracker", "zhsite", "pictures", "bookbay"):
            self.assertNotIn(skipped, {i["definitionName"] for i in f.indexers}, "private, other languages, adult and books stay out")
        self.assertEqual(report["added"][0], "KICKASS")
        self.assertIsNone(stack.refresh_indexers(now=1_000_000 + 14 * 3600), "twice a day at most")

        yts = next(i for i in f.indexers if i["definitionName"] == "yts")
        f.status = [{"indexerId": yts["id"], "initialFailure": "1970-01-08T00:00:00Z", "mostRecentFailure": "1970-01-12T00:00:00Z"}]
        f.broken.add("yts")
        report = stack.refresh_indexers(now=1_000_000 + 26 * 3600)
        self.assertFalse(yts["enable"], "failing for more than three days: switched off")
        self.assertEqual(report["off"], ["YTS"])
        f.broken.discard("yts")
        f.status = []
        report = stack.refresh_indexers(now=1_000_000 + 39 * 3600)
        self.assertTrue(yts["enable"])
        self.assertEqual(report["on"], ["YTS"])

    def test_one_you_switched_off_or_deleted_stays_that_way(self):
        f = self.fake
        self.run_all()
        stack.refresh_indexers(now=1_000_000)
        mine = f.indexers[0]
        mine["enable"] = False  # you switched it off
        f.indexers[:] = [i for i in f.indexers if i["definitionName"] != "kickass"]  # and deleted one
        stack.refresh_indexers(now=1_000_000 + 13 * 3600)
        self.assertFalse(mine["enable"])
        self.assertNotIn("kickass", {i["definitionName"] for i in f.indexers})

    def test_shown_in_services(self):
        self.run_all()
        stack.refresh_indexers(now=1_000_000)
        r = signed_in("admin").get("/api/stack").json()["indexers"]
        self.assertEqual((r["at"], r["added"][0]), (1_000_000, "KICKASS"))


class QbitClosed(Base):
    fake_args = {"qbit_open": False}

    def test_a_login_wall_is_explained(self):
        r = self.run_all()[("qbittorrent", "folders")]
        self.assertEqual(r["status"], "failed")
        self.assertIn("login", r["detail"])


class Retries(unittest.TestCase):
    def test_not_ready_yet_is_tried_again_but_a_dead_indexer_isnt(self):
        answers = [[{"app": "sonarr", "step": "download client", "status": "failed", "detail": "Sonarr said 400: Unable to connect to qBittorrent"}],
                   [{"app": "prowlarr", "step": "indexers", "status": "failed", "detail": "couldn't reach X (timed out)"}]]
        calls = []

        def fake_run(name, password, into):
            calls.append(name)
            into.extend(answers[len(calls) - 1])

        with mock.patch.object(stack, "run", side_effect=fake_run), mock.patch.object(stack.time, "sleep"):
            self.assertTrue(stack.start("admin", "pw", retries=5))
            for _ in range(200):
                if not stack.state["running"]:
                    break
                threading.Event().wait(0.01)  # time.sleep is patched here
        self.assertEqual(len(calls), 2, "once more after 'unable to connect', then it stops: indexers that don't answer aren't retried in a loop")


class PasswordRuns(unittest.TestCase):
    def tearDown(self):
        stack.state["running"] = False
        stack._next.clear()

    def test_a_run_with_your_password_replaces_one_waiting_without(self):
        stack.state["running"] = True
        self.assertTrue(stack.start())
        self.assertFalse(stack.start(), "one without a password is already waiting")
        self.assertTrue(stack.start("admin", "pw", retries=3), "yours is never dropped")
        self.assertEqual(stack._next, [("admin", "pw", 3)])


class Existing(Base):
    fake_args = {"roots": {"sonarr": [{"id": 1, "path": "/data/tv"}]}}

    def test_your_own_root_folder_is_used_not_a_second_one(self):
        r = self.run_all()
        self.assertEqual(r[("sonarr", "root folder")]["status"], "ok")
        self.assertEqual([x["path"] for x in self.fake.roots["sonarr"]], ["/data/tv"])
        self.assertEqual(self.fake.seerr["sonarr"][0]["activeDirectory"], "/data/tv", "Seerr sends shows where Sonarr keeps them")
        self.assertEqual({i["version"] for i in self.fake.cleanup["instances"]["radarr"]}, {5}, "the API generation each app says it speaks")

    def test_an_indexer_you_deleted_stays_deleted(self):
        self.run_all()
        self.fake.indexers = [i for i in self.fake.indexers if i["definitionName"] != "eztv"]
        self.assertEqual(self.run_all()[("prowlarr", "indexers")]["status"], "ok")
        self.assertNotIn("eztv", {i["definitionName"] for i in self.fake.indexers})


class YourOwnIndexers(Base):
    fake_args = {"indexers": [{"definitionName": "myprivatetracker"}]}

    def test_a_prowlarr_with_its_own_indexers_gets_no_public_ones(self):
        r = self.run_all()[("prowlarr", "indexers")]
        self.assertEqual(r["status"], "ok", r["detail"])
        self.assertEqual(len(self.fake.indexers), 1)


class InitFiles(unittest.TestCase):
    def test_empty_media_folders_docker_made_go_to_the_apps_user_and_full_ones_are_left_alone(self):
        media = TMP / "kinds"
        for kind in ("movies", "shows"):
            (media / kind).mkdir(parents=True, exist_ok=True)
        (media / "shows" / "Show").mkdir(exist_ok=True)
        owned = []
        other = str(os.getuid() + 1)  # the apps' user is not who owns the folders
        with mock.patch.object(stack, "DATA", media), mock.patch.object(stack, "_own", lambda *p: owned.extend(p)), \
                mock.patch.dict(os.environ, {"PUID": other, "PGID": other}):
            done = stack.init_files(TMP / "init-kinds")
        self.assertEqual(owned.count(media / "movies"), 1, "empty and not theirs: handed over")
        self.assertNotIn(media / "shows", owned, "an existing library is never touched")
        self.assertNotIn(media / "music", owned, "a folder that isn't mounted isn't made")
        self.assertIn("movies: folder handed to the apps' user", done)
        # setting up a root folder over an existing library never re-owns it; links aren't followed
        owned.clear()
        (media / "films").symlink_to(media / "movies")
        with mock.patch.object(stack, "DATA", media), mock.patch.object(stack, "_own", lambda *p: owned.extend(p)), \
                mock.patch.dict(os.environ, {"PUID": other, "PGID": other}):
            stack.make_dir("/data/shows")
            stack.make_dir("/data/films")
            stack.make_dir("/data/books")
            with mock.patch.object(stack.os, "geteuid", lambda: os.getuid() + 2):  # someone else's empty folder
                stack.make_dir("/data/games")
        self.assertEqual(owned, [media / "books"], "only the folder it just made")

    def test_qbittorrent_opens_to_the_home_network_once_and_keeps_your_password(self):
        root = TMP / "init"
        conf = root / "qbittorrent" / "config" / "qBittorrent" / "qBittorrent.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text("[Preferences]\nWebUI\\Password_PBKDF2=\"@ByteArray(x:y)\"\n\n[WebUI]\nAddress=*\nAuthSubnetWhitelist=10.9.0.0/16\n")
        done = stack.init_files(root)
        self.assertIn("qBittorrent: no login on the home network", done)
        self.assertIn("seerr: made its config folder", done, "Seerr doesn't run as root: its folder must exist first")
        self.assertTrue((root / "sonarr" / "config").is_dir())
        text = conf.read_text()
        self.assertIn("[WebUI]\nAuthSubnetWhitelistEnabled=true\nLocalHostAuth=false\nAddress=*\nAuthSubnetWhitelist=10.9.0.0/16, 192.168.0.0/16, 172.16.0.0/12\n", text,
                      "their own subnet is kept, ours added")
        self.assertIn("WebUI\\AuthSubnetWhitelistEnabled=true", text, "qBittorrent 4 reads it under [Preferences]")
        self.assertIn('WebUI\\Password_PBKDF2="@ByteArray(x:y)"', text)  # pragma: allowlist secret
        self.assertEqual(stack.init_files(root), [], "a second run changes nothing")
        self.assertEqual(conf.read_text(), text)

    def test_windows_line_endings_and_your_home_network(self):
        root = TMP / "init3"
        conf = root / "qbittorrent" / "config" / "qBittorrent" / "qBittorrent.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_bytes(b"[WebUI]\r\nAddress=*\r\n")
        with mock.patch.object(stack, "HOME_NETS", ["10.1.2.0/24", "172.16.0.0/12"]):
            stack.init_files(root)
        text = conf.read_text()
        self.assertEqual(text.count("[WebUI]"), 1, "the section is found, not added twice")
        self.assertIn("AuthSubnetWhitelist=10.1.2.0/24, 172.16.0.0/12\n", text, "only your network and the containers, not every private range")
        self.assertEqual(list(conf.parent.glob("*.tmp")), [], "written whole, then swapped in")

    def test_settings_from_before_the_rename_move_with_it(self):
        root = TMP / "rename"
        (root / "yeerr" / "config").mkdir(parents=True, exist_ok=True)
        (root / "yeerr" / "config" / "yeerr.db").write_text("accounts")
        (root / "parlor" / "config").mkdir(parents=True, exist_ok=True)  # empty: Docker made it first
        self.assertIn("moved ./yeerr/config to ./parlor/config (formerly yeerr)", stack.init_files(root))
        self.assertEqual((root / "parlor" / "config" / "yeerr.db").read_text(), "accounts")
        (root / "yeerr" / "config").mkdir()
        (root / "yeerr" / "config" / "other.db").write_text("x")
        stack.init_files(root)
        self.assertEqual((root / "parlor" / "config" / "yeerr.db").read_text(), "accounts", "both full: nothing is moved over the new one")

    def test_a_first_start_gets_a_whole_file(self):
        root = TMP / "init2"
        stack.init_files(root)
        text = (root / "qbittorrent" / "config" / "qBittorrent" / "qBittorrent.conf").read_text()
        self.assertIn("AuthSubnetWhitelistEnabled=true", text)
        self.assertIn("Session\\DefaultSavePath=/data/downloads", text)


class Api(Base):
    def test_admins_only_and_progress(self):
        member = signed_in("member-stack")
        self.assertEqual(member.get("/api/stack").status_code, 403)
        admin = signed_in("admin")
        with mock.patch.object(stack, "start") as start:
            r = admin.post("/api/stack/setup", json={"password": "wrong"}, headers=H)
            self.assertEqual(r.status_code, 403, "your password, checked, before it goes anywhere")
            r = admin.post("/api/stack/setup", json={"password": "correct horse"}, headers=H)
            self.assertEqual(r.status_code, 200)
            start.assert_called_once_with("admin", "correct horse")
        stack.run("admin", "correct horse")
        apps = {a["app"]: a for a in admin.get("/api/stack").json()["apps"]}
        core.db().execute("UPDATE users SET pw = NULL, jellyfin_id = 'jf-admin' WHERE name = 'admin'")
        self.addCleanup(core.db().execute, "UPDATE users SET jellyfin_id = NULL WHERE name = 'admin'")
        self.addCleanup(lambda: core.db().execute("UPDATE users SET pw = ? WHERE name = 'admin'", (core.hash_password("correct horse"),)))
        me = core.one("SELECT id FROM users WHERE name = 'admin'")["id"]
        with mock.patch.object(stack, "start") as start, mock.patch("auth.jellyfin_login", side_effect=lambda n, p: me if p == "jf pass" else None):
            guess = {"password": "anything"}  # pragma: allowlist secret
            self.assertEqual(admin.post("/api/stack/setup", json=guess, headers=H).status_code, 403,
                             "a Jellyfin account's password is checked with Jellyfin, not waved through")
            self.assertEqual(admin.post("/api/stack/setup", json={"password": "jf pass"}, headers=H).status_code, 200)
            start.assert_called_once_with("admin", "jf pass")
        self.assertTrue(apps["sonarr"]["key"])
        self.assertNotIn(KEYS["sonarr"], admin.get("/api/stack").text, "keys never leave the server")


if __name__ == "__main__":
    unittest.main()
