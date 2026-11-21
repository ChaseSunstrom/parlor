"""Access control: an admin picks, per person, the sections of Parlor they can use and the Jellyfin libraries
they see. One guard enforces sections on every route; Jellyfin enforces libraries through the person's own
Jellyfin user, which Parlor makes and keeps in sync."""
import base64
import unittest
from unittest import mock

import yeerr_test_env as env
from yeerr_test_env import H, signed_in

import acl
import app
import core
import jf
from fastapi.routing import APIRoute
from starlette.routing import Mount


def access(admin, member_id, **body):
    return admin.put(f"/api/users/{member_id}/access", json=body, headers=H)


class Routes(unittest.TestCase):
    def test_every_route_has_a_section_or_says_who_it_is_for(self):
        """A route added later with no rule fails here, so nothing ships open by mistake."""
        unmapped, not_admin = [], []
        for r in app.app.routes:
            if isinstance(r, Mount):
                if r.path == "/music":  # music-fndr, mounted whole
                    self.assertEqual(acl.rule("/music/api/search"), ("music",))
                continue
            rule = acl.rule(r.path)
            if rule is None:
                unmapped.append(r.path)
            elif rule == acl.ADMIN and isinstance(r, APIRoute):
                deps = {d.call for d in r.dependant.dependencies}
                if core.admin not in deps and not acl.checks_admin_itself(r.path):
                    not_admin.append(r.path)
        self.assertEqual(unmapped, [], "routes with no section: add them to acl.RULES")
        self.assertEqual(not_admin, [], "routes marked admin that don't depend on core.admin")

    def test_rules(self):
        cases = {"/sw.js": acl.OPEN, "/api/yt/search": ("watch",), "/api/yt/downloads": ("watch", "downloads"), "/api/me/later": ("watch",),
                 "/api/seerr/discover": ("media",), "/api/arr/queue": ("media",), "/api/arr/music/search": ("music",),
                 "/api/arr/apps": acl.EVERYONE,  # which apps are connected: Music asks it too, with Films & shows closed
                 "/api/jf/items": ("library",), "/api/jf/music/a.mp3": ("music",), "/rest/ping.view": ("music",),
                 "/music/api/downloads": ("music", "downloads"), "/music/api/import": ("music", "downloads"),
                 "/music/api/playlists/abc/sync": ("music", "downloads"), "/music/api/playlists/abc": ("music",),
                 "/music/api/playlists": ("music",), "/music/api/organize": ("music", "downloads"),
                 "/api/me": acl.EVERYONE, "/api/users/3": acl.ADMIN,
                 "/api/qbit/torrents": acl.ADMIN, "/qbit/": acl.ADMIN, "/api/nope": None,
                 "/tv": acl.OPEN, "/api/tv/code": acl.OPEN, "/api/tv/claim": acl.OPEN, "/api/tv/me": acl.OPEN,
                 "/api/tv/pair": acl.EVERYONE, "/api/tv/3/do": acl.EVERYONE, "/api/tv/events": acl.EVERYONE,
                 "/api/tv/sets": acl.ADMIN, "/api/tv/sets/3/test": acl.ADMIN, "/api/tv/hw/3/press": acl.EVERYONE,
                 "/api/games/steam": ("games",), "/api/games/play/nes/x.nes": ("games",), "/api/games/settings": acl.ADMIN,
                 "/games/stream/api/pair": ("games",), "/games/stream": ("games",),
                 "/api/live/tw/follows": ("watch",), "/api/live/seg": ("watch",)}
        for path, want in cases.items():
            self.assertEqual(acl.rule(path), want, path)

    def test_open_api_paths_are_the_ones_the_guard_lets_in(self):
        opened = {r.path for r in app.app.routes if getattr(r, "path", "").startswith("/api/") and acl.rule(r.path) == acl.OPEN}
        self.assertTrue(opened, "the TV's pairing calls at least")
        self.assertEqual(opened - app.PUBLIC, set(), "open in acl, but the guard still asks for a sign-in")


class Sections(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        self.member = signed_in("limited-member")
        self.mid = self.member.get("/api/me").json()["id"]
        self.addCleanup(lambda: access(self.admin, self.mid, blocked=[], libraries=None))

    def test_a_blocked_section_is_refused_by_the_server(self):
        self.assertEqual(self.member.get("/api/me/later").status_code, 200)
        self.assertEqual(access(self.admin, self.mid, blocked=["watch"]).status_code, 200)
        r = self.member.get("/api/me/later")
        self.assertEqual(r.status_code, 403)
        self.assertIn("Watch", r.json()["detail"])
        self.assertEqual(self.member.get("/api/seerr/status").status_code, 200, "the rest still works")
        me = self.member.get("/api/me").json()
        self.assertEqual((me["blocked"], me["sections"]["watch"]), (["watch"], False))
        self.assertEqual(self.admin.get("/api/me/later").status_code, 200)

    def test_downloads_are_their_own_switch(self):
        access(self.admin, self.mid, blocked=["downloads"])
        self.assertEqual(self.member.post("/api/yt/downloads", json={"id": "kkkkkkkkkkk"}, headers=H).status_code, 403)
        self.assertEqual(self.member.get("/music/api/downloads").status_code, 403)
        self.assertEqual(self.member.get("/api/me/later").status_code, 200, "watching still works")

    def test_music_apps_too(self):
        r = self.member.post("/api/me/app-passwords", json={"name": "phone"}, headers=H).json()
        auth = {"u": "limited-member", "p": "enc:" + r["secret"].encode().hex(), "c": "test", "v": "1.16.1", "f": "json"}
        ok = env.client().get("/rest/ping.view", params=auth).json()["subsonic-response"]
        self.assertEqual(ok["status"], "ok")
        access(self.admin, self.mid, blocked=["music"])
        refused = env.client().get("/rest/ping.view", params=auth).json()["subsonic-response"]
        self.assertEqual((refused["status"], refused["error"]["code"]), ("failed", 50))

    def test_only_admins_set_access_and_only_known_sections(self):
        self.assertEqual(access(self.member, self.mid, blocked=[]).status_code, 403)
        self.assertEqual(access(self.admin, self.mid, blocked=["everything"]).status_code, 422)
        admin_id = self.admin.get("/api/me").json()["id"]
        self.assertEqual(access(self.admin, admin_id, blocked=["watch"]).status_code, 409, "admins always have everything")
        listed = {u["id"]: u for u in self.admin.get("/api/users").json()}
        self.assertEqual(listed[self.mid]["blocked"], [])


class FakeJellyfin:
    """Users, libraries and user policies, as Jellyfin answers them."""

    def __init__(self):
        self.users = [{"Id": "a" * 32, "Name": "default", "Policy": {"EnableAllFolders": True}}]
        self.policies, self.calls, self.made = {}, [], 0

    def __call__(self, method, url, headers=None, params=None, json=None, **kw):
        path = url.split("8096", 1)[1]
        self.calls.append((method, path, json))
        body, code = {}, 200
        if path == "/Users" and method == "GET":
            body = self.users
        elif path == "/Users/New":
            self.made += 1
            new = {"Id": f"{self.made:032x}", "Name": json["Name"], "Policy": {"EnableAllFolders": True}}
            self.users.append(new)
            body = new
        elif path.endswith("/Policy"):
            uid = path.split("/")[2]
            self.policies[uid] = json
            next(u for u in self.users if u["Id"] == uid)["Policy"] = json
            code = 204
        elif method == "DELETE" and path.startswith("/Users/"):
            self.users = [u for u in self.users if u["Id"] != path.split("/")[2]]
            code = 204
        elif path == "/Localization/ParentalRatings":
            body = [{"Name": "Unrated", "Value": None}, {"Name": "Approved", "Value": 0}, {"Name": "G", "Value": 0}, {"Name": "TV-PG", "Value": 10},
                    {"Name": "PG", "Value": 10}, {"Name": "PG-13", "Value": 13}]
        elif path == "/Library/VirtualFolders":
            body = [{"Name": "Films", "ItemId": "f" * 32, "CollectionType": "movies"}, {"Name": "Shows", "ItemId": "e" * 32, "CollectionType": "tvshows"}]
        return mock.Mock(status_code=code, ok=code < 300, json=lambda: body, headers={}, close=lambda: None)


class Libraries(unittest.TestCase):
    def setUp(self):
        core.set_setting("jellyfin_url", "http://jellyfin:8096")
        core.set_setting("jellyfin_key", "k")
        core.set_setting("jellyfin_user", "default")
        self.addCleanup(core.set_setting, "jellyfin_url", "")
        self.fake = FakeJellyfin()
        p = mock.patch.object(jf.requests, "request", self.fake)
        p.start()
        self.addCleanup(p.stop)
        jf._users.drop("all")
        self.admin = signed_in("admin")
        self.member = signed_in("library-member")
        self.mid = self.member.get("/api/me").json()["id"]
        core.db().execute("UPDATE users SET jf_user = NULL, jf_own = 0, libraries = NULL, blocked = '', jellyfin_id = NULL WHERE id = ?",
                          (self.mid,))  # each test starts from a member with no limits

    def test_limiting_libraries_gives_the_person_their_own_jellyfin_user(self):
        self.assertEqual([l["id"] for l in self.admin.get("/api/access/libraries").json()["libraries"]], ["f" * 32, "e" * 32])
        r = access(self.admin, self.mid, libraries=["f" * 32])
        self.assertEqual(r.status_code, 200, r.text)
        made = [c for c in self.fake.calls if c[1] == "/Users/New"]
        self.assertEqual(len(made), 1)
        uid = self.fake.users[-1]["Id"]
        policy = self.fake.policies[uid]
        self.assertEqual((policy["EnableAllFolders"], policy["EnabledFolders"]), (False, ["f" * 32]))
        self.assertEqual(core.one("SELECT jf_user FROM users WHERE id = ?", self.mid)["jf_user"], uid, "they now play as it")
        self.assertTrue(self.fake.users[0]["Policy"]["EnableAllFolders"], "the shared default user isn't touched")
        access(self.admin, self.mid, libraries=["f" * 32, "e" * 32])
        self.assertEqual(len([c for c in self.fake.calls if c[1] == "/Users/New"]), 1, "the same user, re-synced")
        self.assertEqual(self.fake.policies[uid]["EnabledFolders"], ["f" * 32, "e" * 32])
        access(self.admin, self.mid, libraries=None)
        self.assertTrue(self.fake.policies[uid]["EnableAllFolders"], "back to all libraries")

    def test_pictures_from_a_hidden_library_are_refused(self):
        access(self.admin, self.mid, libraries=["f" * 32])
        uid = core.one("SELECT jf_user FROM users WHERE id = ?", self.mid)["jf_user"]
        hidden, shown = "c" * 32, "d" * 32

        def item(method, url, **kw):  # their own Jellyfin user can see `shown` only
            if "/Images/" in url:
                return mock.Mock(status_code=200, headers={"Content-Type": "image/jpeg"}, iter_content=lambda n: iter([b"img"]), close=lambda: None)
            if url.endswith(f"/Items/{shown}") or url.endswith(f"/Items/{hidden}"):
                ok = url.endswith(shown)
                return mock.Mock(status_code=200 if ok else 404, ok=ok, json=lambda: {"Id": shown}, headers={}, close=lambda: None)
            return self.fake(method, url, **kw)
        with mock.patch.object(jf.requests, "request", item):
            self.assertEqual(self.member.get(f"/api/jf/stream/Items/{hidden}/Images/Primary").status_code, 404)
            self.assertEqual(self.member.get(f"/api/jf/stream/Items/{shown}/Images/Primary").status_code, 200)

    def test_making_them_admin_lifts_their_limits(self):
        access(self.admin, self.mid, blocked=["watch"], libraries=["f" * 32])
        uid = core.one("SELECT jf_user FROM users WHERE id = ?", self.mid)["jf_user"]
        self.assertEqual(self.admin.patch(f"/api/users/{self.mid}", json={"admin": True}, headers=H).status_code, 200)
        row = core.one("SELECT blocked, libraries FROM users WHERE id = ?", self.mid)
        self.assertEqual((row["blocked"], row["libraries"]), ("", None))
        self.assertTrue(self.fake.policies[uid]["EnableAllFolders"], "their Jellyfin user sees everything again")
        self.admin.patch(f"/api/users/{self.mid}", json={"admin": False}, headers=H)
        self.assertEqual(self.member.get("/api/me/later").status_code, 200, "no old block comes back on demotion")

    def test_a_deleted_jellyfin_user_is_made_again_and_deleting_the_person_removes_theirs(self):
        access(self.admin, self.mid, libraries=["f" * 32])
        gone = core.one("SELECT jf_user FROM users WHERE id = ?", self.mid)["jf_user"]
        self.fake.users = [u for u in self.fake.users if u["Id"] != gone]  # someone deleted it in Jellyfin
        self.assertEqual(access(self.admin, self.mid, libraries=["e" * 32]).status_code, 200)
        again = core.one("SELECT jf_user FROM users WHERE id = ?", self.mid)["jf_user"]
        self.assertNotEqual(again, gone)
        self.assertEqual(self.fake.policies[again]["EnabledFolders"], ["e" * 32])
        self.assertEqual(self.admin.delete(f"/api/users/{self.mid}", headers=H).status_code, 200)
        self.assertIn(("DELETE", f"/Users/{again}", None), self.fake.calls, "the Jellyfin user Parlor made goes with them")

    def test_a_jellyfin_sign_in_keeps_its_account_visible(self):
        core.db().execute("UPDATE users SET jellyfin_id = ? WHERE id = ?", ("a" * 32, self.mid))
        access(self.admin, self.mid, libraries=["f" * 32])
        self.assertIsNot(self.fake.policies["a" * 32].get("IsHidden"), True, "their own Jellyfin account isn't hidden from Jellyfin's sign-in")
        self.assertEqual(self.fake.policies["a" * 32]["EnabledFolders"], ["f" * 32])

    def test_unknown_library_ids_are_refused(self):
        self.assertEqual(access(self.admin, self.mid, libraries=["0" * 32]).status_code, 422)

    def test_jellyfin_down_fails_closed(self):
        def down(*a, **k):
            raise jf.requests.ConnectionError("down")
        with mock.patch.object(jf.requests, "request", down):
            r = access(self.admin, self.mid, libraries=["f" * 32])
        self.assertEqual(r.status_code, 502)
        row = core.one("SELECT libraries FROM users WHERE id = ?", self.mid)
        self.assertEqual(row["libraries"], "[]", "until Jellyfin takes the limit, they see no library rather than all")


if __name__ == "__main__":
    unittest.main()
