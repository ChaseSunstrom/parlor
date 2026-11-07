"""qBittorrent for admins: the torrents list (incremental, as qBittorrent's own page gets it), actions
across qBittorrent 4 and 5, speed limits, adding a magnet, and qBittorrent's own page through /qbit/.
qBittorrent is a fake that checks what the real one checks (the Referer for CSRF, a login when set)."""
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from yeerr_test_env import H, client, signed_in

import qbit

HASH, HASH2 = "a" * 40, "b" * 40


class Resp:
    def __init__(self, status=200, body=b"", ctype="application/json", headers=None):
        self.status_code, self.content = status, body if isinstance(body, bytes) else body.encode()
        self.headers = {"Content-Type": ctype, **(headers or {})}
        self.ok = status < 400
        self.text = self.content.decode()

    def json(self):
        import json
        return json.loads(self.content)

    def iter_content(self, n):
        yield self.content

    def close(self):
        pass


class FakeQbit:
    """qBittorrent 5 by default; v4=True speaks the old pause/resume names."""

    def __init__(self, v4=False, login=None):
        self.v4, self.login, self.calls, self.sid = v4, login, [], None
        self.torrents = {HASH: {"name": "A.Film.2160p", "state": "downloading", "progress": 0.5, "dlspeed": 1000, "category": "radarr",
                                "size": 4_000_000, "eta": 2000, "ratio": 0.1, "secret_field": "x"},
                         HASH2: {"name": "Show.S01", "state": "stoppedUP", "progress": 1.0, "dlspeed": 0, "category": "tv-sonarr", "size": 9}}
        self.alt, self.limits = False, {"dl": 0, "up": 0}

    def __call__(self, method, url, params=None, data=None, headers=None, cookies=None, allow_redirects=True, stream=False, timeout=None, **kw):
        u = urlsplit(url)
        path = u.path
        q = {**{k: v[0] for k, v in parse_qs(u.query).items()}, **(params or {})}
        form = data if isinstance(data, dict) else {k: v[0] for k, v in parse_qs((data or b"").decode() if isinstance(data, bytes) else (data or "")).items()}
        self.calls.append((method, path, q, form, dict(headers or {}), dict(cookies or {})))
        assert u.netloc == "gluetun:8080", u.netloc
        if method == "POST" and urlsplit((headers or {}).get("Referer", "")).netloc != "gluetun:8080":
            return Resp(401, "Unauthorized", "text/plain")  # qBittorrent's CSRF check
        name = "SID" if self.v4 else "QBT_SID_8080"  # qBittorrent 5 names its cookie after its port (seen live)
        if path == "/api/v2/auth/login":
            if self.login and (form.get("username"), form.get("password")) == self.login:
                self.sid = "SID123"
                return Resp(200 if self.v4 else 204, "Ok." if self.v4 else "", "text/plain", {"Set-Cookie": f"{name}=SID123; HttpOnly; SameSite=Lax; path=/"})
            return Resp(200, "Fails.", "text/plain")
        if self.login and (cookies or {}).get(name) != "SID123":
            return Resp(403, "Forbidden", "text/plain")
        if path == "/api/v2/sync/maindata":
            if str(q.get("rid")) == "2":
                return Resp(200, '{"rid": 3, "categories": {"music": {"name": "music"}}, "categories_removed": ["radarr"]}')
            if str(q.get("rid")) == "1":
                return Resp(200, '{"rid": 2, "torrents": {"%s": {"progress": 0.6}}, "server_state": {"dl_info_speed": 5}}' % HASH)
            import json
            return Resp(200, json.dumps({"rid": 1, "full_update": True, "torrents": self.torrents, "categories": {"radarr": {"name": "radarr"}},
                                         "server_state": {"dl_info_speed": 1000, "up_info_speed": 10, "use_alt_speed_limits": self.alt,
                                                          "dl_rate_limit": self.limits["dl"], "up_rate_limit": self.limits["up"], "free_space_on_disk": 10**12,
                                                          "connection_status": "connected", "secret": "x"}}))
        old = {"/api/v2/torrents/stop": "/api/v2/torrents/pause", "/api/v2/torrents/start": "/api/v2/torrents/resume"}
        if (self.v4 and path in old) or (not self.v4 and path in old.values()):
            return Resp(404, "Not Found", "text/plain")
        if path == "/api/v2/torrents/add" and "not-a-torrent" in form.get("urls", ""):  # what the real ones say for a link they won't take
            return Resp(200, "Fails.", "text/plain") if self.v4 else Resp(200, '{"added_torrent_ids":[],"failure_count":1,"pending_count":0,"success_count":0}')
        if path in ("/api/v2/torrents/stop", "/api/v2/torrents/pause", "/api/v2/torrents/start", "/api/v2/torrents/resume",
                    "/api/v2/torrents/recheck", "/api/v2/torrents/reannounce", "/api/v2/torrents/setCategory", "/api/v2/torrents/add"):
            return Resp(200, "", "text/plain")
        if path == "/api/v2/torrents/delete":
            for h in form["hashes"].split("|"):
                self.torrents.pop(h, None)
            return Resp(200, "", "text/plain")
        if path == "/api/v2/transfer/toggleSpeedLimitsMode":
            self.alt = not self.alt
            return Resp(200, "", "text/plain")
        if path in ("/api/v2/transfer/setDownloadLimit", "/api/v2/transfer/setUploadLimit"):
            self.limits["dl" if "Download" in path else "up"] = int(form["limit"])
            return Resp(200, "", "text/plain")
        if path == "/api/v2/transfer/speedLimitsMode":
            return Resp(200, "1" if self.alt else "0", "text/plain")
        if path == "/":
            return Resp(200, '<html><link href="css/style.css"></html>', "text/html", {"Set-Cookie": "SID=leak; path=/", "X-Frame-Options": "SAMEORIGIN"})
        if path == "/css/style.css":
            return Resp(200, "body{}", "text/css")
        if path == "/old":
            return Resp(302, "", "text/plain", {"Location": "/new/place"})
        return Resp(404, "Not Found", "text/plain")


class Base(unittest.TestCase):
    fake_args = {}

    def setUp(self):
        self.fake = FakeQbit(**self.fake_args)
        for p in (mock.patch.object(qbit.requests, "request", self.fake), mock.patch.object(qbit, "base", lambda: "http://gluetun:8080")):
            p.start()
            self.addCleanup(p.stop)
        qbit._sid.clear()
        self.c = signed_in("admin")


class Torrents(Base):
    def test_list_then_only_what_changed(self):
        r = self.c.get("/api/qbit/torrents").json()
        self.assertEqual(r["rid"], 1)
        self.assertEqual(r["torrents"][HASH]["name"], "A.Film.2160p")
        self.assertNotIn("secret_field", r["torrents"][HASH], "only what the page shows")
        self.assertNotIn("secret", r["server_state"])
        r = self.c.get("/api/qbit/torrents", params={"rid": 1}).json()
        self.assertEqual((r["rid"], r["torrents"][HASH]), (2, {"progress": 0.6}))
        self.assertIsNone(r["categories"], "no change, no list")
        r = self.c.get("/api/qbit/torrents", params={"rid": 2}).json()
        self.assertEqual((r["categories"], r["categories_removed"]), (["music"], ["radarr"]), "changes to merge, not a new list")

    def test_actions(self):
        self.assertEqual(self.c.post("/api/qbit/torrents/pause", json={"hashes": [HASH]}, headers=H).status_code, 200)
        self.assertEqual(self.fake.calls[-1][1:4:2], ("/api/v2/torrents/stop", {"hashes": HASH}))
        self.c.post("/api/qbit/torrents/resume", json={"hashes": "all"}, headers=H)
        self.assertEqual(self.fake.calls[-1][3], {"hashes": "all"})
        self.c.post("/api/qbit/torrents/category", json={"hashes": [HASH, HASH2], "category": "radarr"}, headers=H)
        self.assertEqual(self.fake.calls[-1][3], {"hashes": f"{HASH}|{HASH2}", "category": "radarr"})
        self.c.post("/api/qbit/torrents/delete", json={"hashes": [HASH], "delete_files": True}, headers=H)
        self.assertEqual(self.fake.calls[-1][3], {"hashes": HASH, "deleteFiles": "true"})
        self.assertNotIn(HASH, self.fake.torrents)
        for bad in ({"hashes": ["../x"]}, {"hashes": []}, {"hashes": "all", "delete_files": True}):
            self.assertEqual(self.c.post("/api/qbit/torrents/delete", json=bad, headers=H).status_code, 422, bad)
        self.assertEqual(self.c.post("/api/qbit/torrents/explode", json={"hashes": [HASH]}, headers=H).status_code, 404)

    def test_limits_and_adding(self):
        r = self.c.put("/api/qbit/limits", json={"alt": True, "down": 2_000_000, "up": 0}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((self.fake.alt, self.fake.limits), (True, {"dl": 2_000_000, "up": 0}))
        self.c.put("/api/qbit/limits", json={"alt": True}, headers=H)
        self.assertTrue(self.fake.alt, "already on: not toggled off")
        r = self.c.post("/api/qbit/add", json={"magnet": "magnet:?xt=urn:btih:" + HASH, "category": "radarr"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls[-1][3]["urls"], "magnet:?xt=urn:btih:" + HASH)
        self.assertEqual(self.c.post("/api/qbit/add", json={"magnet": "file:///etc/passwd"}, headers=H).status_code, 422)
        r = self.c.post("/api/qbit/add", json={"magnet": "https://example.net/not-a-torrent"}, headers=H)
        self.assertEqual(r.status_code, 409, "qBittorrent's 200 'Fails.' isn't a success")
        before = (self.fake.alt, dict(self.fake.limits))
        self.assertEqual(self.c.put("/api/qbit/limits", json={"alt": False, "down": -5}, headers=H).status_code, 422)
        self.assertEqual((self.fake.alt, self.fake.limits), before, "a bad value changes nothing, slow mode included")

    def test_admins_only(self):
        member = signed_in("member-qbit")
        for r in (member.get("/api/qbit/torrents"), member.get("/qbit/"), client().get("/qbit/")):
            self.assertIn(r.status_code, (401, 403))
        self.assertFalse([c for c in self.fake.calls if c[1] == "/"], "nothing reached qBittorrent")


class OldQbitLogin(Base):
    fake_args = {"v4": True, "login": ("qb", "pw")}

    def test_qbittorrent_4_session_cookie(self):
        with mock.patch.dict("os.environ", {"QBITTORRENT_USER": "qb", "QBITTORRENT_PASSWORD": "pw"}):  # pragma: allowlist secret
            self.assertEqual(self.c.get("/api/qbit/torrents").status_code, 200)


class OldQbit(Base):
    fake_args = {"v4": True}

    def test_qbittorrent_4_refuses_a_link_its_way(self):
        self.assertEqual(self.c.post("/api/qbit/add", json={"magnet": "https://example.net/not-a-torrent"}, headers=H).status_code, 409)

    def test_qbittorrent_4_names(self):
        self.assertEqual(self.c.post("/api/qbit/torrents/pause", json={"hashes": [HASH]}, headers=H).status_code, 200)
        self.assertEqual([c[1] for c in self.fake.calls[-2:]], ["/api/v2/torrents/stop", "/api/v2/torrents/pause"])


class WithLogin(Base):
    fake_args = {"login": ("qb", "pw")}

    def test_signs_in_once_and_keeps_the_session_to_itself(self):
        with mock.patch.dict("os.environ", {"QBITTORRENT_USER": "qb", "QBITTORRENT_PASSWORD": "pw"}):  # pragma: allowlist secret
            self.assertEqual(self.c.get("/api/qbit/torrents").status_code, 200)
            self.assertEqual(self.c.get("/api/qbit/torrents").status_code, 200)
            r = self.c.get("/qbit/")
        self.assertEqual(len([c for c in self.fake.calls if c[1] == "/api/v2/auth/login"]), 1)
        self.assertNotIn("set-cookie", r.headers, "qBittorrent's session stays on the server")

    def test_a_wrong_password_isnt_tried_on_every_poll(self):
        with mock.patch.dict("os.environ", {"QBITTORRENT_USER": "qb", "QBITTORRENT_PASSWORD": "wrong"}):  # pragma: allowlist secret
            for _ in range(5):
                self.assertEqual(self.c.get("/api/qbit/torrents").status_code, 502)
        self.assertEqual(len([c for c in self.fake.calls if c[1] == "/api/v2/auth/login"]), 1, "qBittorrent bans an address after a few failures")

    def test_without_a_login_it_says_so(self):
        r = self.c.get("/api/qbit/torrents")
        self.assertEqual(r.status_code, 502)
        self.assertIn("QBITTORRENT_USER", r.json()["detail"])


class Page(Base):
    def test_qbittorrents_own_page(self):
        r = self.c.get("/qbit", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (307, "/qbit/"))
        r = self.c.get("/qbit/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)
        self.assertIn("css/style.css", r.text)
        self.assertNotIn("set-cookie", r.headers)
        sent = self.fake.calls[-1]
        self.assertNotIn("yeerr", " ".join(sent[4]).lower() + " ".join(sent[5]), "yeerr's own session never goes to qBittorrent")
        self.assertEqual(sent[4].get("Accept-Encoding"), "identity", "a compressed body would make Content-Length lie (seen live)")
        self.assertEqual(self.c.get("/qbit/css/style.css").text, "body{}")
        r = self.c.post("/qbit/api/v2/torrents/delete", data={"hashes": HASH}, headers={"Origin": "http://testserver"})
        self.assertEqual(r.status_code, 200, "qBittorrent's CSRF check passes: yeerr speaks as its own page")
        self.assertEqual(self.c.get("/qbit/old", follow_redirects=False).headers["location"], "/qbit/new/place")
        for sneaky in ("/qbit/%2e%2e/x", "/qbit//evil.example/x", "/qbit/a/%2E%2E/b", "/qbit/%252e%252e/x", "/qbit/a%3Fb"):
            self.assertIn(self.c.get(sneaky, follow_redirects=False).status_code, (400, 404), sneaky)

    def test_another_site_cant_post_through_it(self):
        r = self.c.post("/qbit/api/v2/torrents/delete", data={"hashes": HASH}, headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self.c.post("/qbit/api/v2/torrents/delete", data={"hashes": HASH})
        self.assertEqual(r.status_code, 403, "a change that can't show where it came from is refused too")
        r = self.c.post("/qbit/api/v2/torrents/delete", data={"hashes": HASH}, headers={"Sec-Fetch-Site": "same-site"})
        self.assertEqual(r.status_code, 403, "a sibling subdomain isn't this site")
        r = self.c.get("/qbit/api/v2/torrents/info", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403, "another site's link can't reach qBittorrent's API either")
        self.assertEqual(self.c.get("/qbit/", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 200, "a link to the page itself is fine")
        self.assertIn(HASH, self.fake.torrents)


if __name__ == "__main__":
    unittest.main()
