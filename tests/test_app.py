"""Accounts, the guard, settings, the music mount and Seerr (with Seerr faked)."""
import json
import unittest
from unittest import mock

import yeerr_test_env as env
from yeerr_test_env import H, client, signed_in

import auth
import core
import music
import seerr
import yt


class Accounts(unittest.TestCase):
    def test_1_first_run_setup_then_sign_in(self):
        core.db().execute("DELETE FROM users")  # a brand-new install, whichever test file ran first
        c = client()
        self.assertTrue(c.get("/api/auth/state").json()["setup"])
        self.assertEqual(c.post("/api/auth/login", json={"name": "anyone", "password": "anything!"}, headers=H).status_code, 409,  # pragma: allowlist secret
                         "nobody signs in (not even with Jellyfin) before the admin account exists")
        self.assertEqual(c.get("/api/me").status_code, 401)
        self.assertEqual(c.post("/api/auth/setup", json={"name": "admin", "password": "correct horse"}).status_code, 403, "needs the header")
        self.assertEqual(c.post("/api/auth/setup", json={"name": "admin", "password": "short"}, headers=H).status_code, 422)
        self.assertEqual(c.post("/api/auth/setup", json={"name": "admin", "password": "correct horse"}, headers=H).status_code, 200)
        me = c.get("/api/me").json()
        self.assertEqual((me["name"], me["admin"]), ("admin", True))
        # setup is once only
        self.assertEqual(client().post("/api/auth/setup", json={"name": "evil", "password": "correct horse"}, headers=H).status_code, 409)
        c.post("/api/auth/logout", headers=H)
        self.assertEqual(c.get("/api/me").status_code, 401)
        self.assertEqual(c.post("/api/auth/login", json={"name": "admin", "password": "wrong one!"}, headers=H).status_code, 401)
        self.assertEqual(c.post("/api/auth/login", json={"name": "ADMIN", "password": "correct horse"}, headers=H).status_code, 200, "names ignore case")

    def test_2_changes_need_the_header_and_sessions_are_hashed(self):
        c = signed_in("admin")
        self.assertEqual(c.post("/api/me/later", json={"id": "dQw4w9WgXcQ"}).status_code, 403)
        self.assertEqual(c.post("/api/me/later", json={"id": "dQw4w9WgXcQ"}, headers=H).status_code, 200)
        token = c.cookies.get(core.COOKIE)
        stored = [r["token"] for r in core.rows("SELECT token FROM sessions")]
        self.assertNotIn(token, stored)
        self.assertEqual(core.COOKIE, "parlor_session")

    def test_2b_an_install_from_before_the_rename_keeps_working(self):
        c = signed_in("admin")
        token = c.cookies.get(core.COOKIE)
        old = client()
        old.cookies.set("yeerr_session", token)  # a browser still holding yeerr's cookie (compat)
        self.assertEqual(old.get("/api/me").status_code, 200, "the old session cookie still signs you in")
        self.assertEqual(old.post("/api/me/later", json={"id": "dQw4w9WgXcQ"}, headers={"X-Requested-With": "yeerr"}).status_code, 200,
                         "a page loaded before the update still sends the old header (compat)")
        self.assertEqual(c.post("/api/me/later", json={"id": "dQw4w9WgXcQ"}, headers={"X-Requested-With": "parlor"}).status_code, 200)
        self.assertEqual(old.post("/api/auth/logout", headers=H).status_code, 200)
        self.assertEqual(old.get("/api/me").status_code, 401, "signing out ends the old cookie's session too")

    def test_3_guessing_is_throttled(self):
        c = client()
        auth._failures.clear()
        codes = [c.post("/api/auth/login", json={"name": "nobody", "password": f"guess {i}xx"}, headers=H).status_code for i in range(10)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8:], [429, 429])
        auth._failures.clear()

    def test_4_members_cant_do_admin_things(self):
        member = signed_in("member")
        for method, path in [("get", "/api/users"), ("get", "/api/settings"), ("put", "/api/settings"), ("get", "/api/yt/status")]:
            kwargs = {"headers": H, "json": {}} if method == "put" else {}
            self.assertEqual(getattr(member, method)(path, **kwargs).status_code, 403, path)
        self.assertFalse(member.get("/api/me").json()["admin"])

    def test_5_there_is_always_an_admin(self):
        admin = signed_in("admin")
        me = admin.get("/api/me").json()
        self.assertEqual(admin.patch(f"/api/users/{me['id']}", json={"admin": False}, headers=H).status_code, 409)
        self.assertEqual(admin.delete(f"/api/users/{me['id']}", headers=H).status_code, 409)

    def test_6_changing_a_password_signs_out_other_devices(self):
        auth.create_user("pat", "first password", False)
        laptop, phone = signed_in("pat", "first password"), signed_in("pat", "first password")
        self.assertEqual(laptop.post("/api/me/password", json={"old": "nope nope", "new": "second password"}, headers=H).status_code, 403)
        self.assertEqual(laptop.post("/api/me/password", json={"old": "first password", "new": "second password"}, headers=H).status_code, 200)
        self.assertEqual(laptop.get("/api/me").status_code, 200)
        self.assertEqual(phone.get("/api/me").status_code, 401)

    def test_7_settings_round_trip_hide_the_key(self):
        admin = signed_in("admin")
        r = admin.put("/api/settings", json={"seerr_url": "http://seerr.test:5055/", "seerr_key": "abcdefghijkl1234"}, headers=H).json()
        self.assertEqual(r["seerr_url"], "http://seerr.test:5055")
        self.assertTrue(r["seerr_key_set"])
        self.assertNotIn("abcdefghijkl", json.dumps(r))
        self.assertEqual(admin.put("/api/settings", json={"seerr_url": "javascript:alert(1)"}, headers=H).status_code, 422)
        admin.put("/api/settings", json={"seerr_url": "", "seerr_key": ""}, headers=H)

    def test_7b_deleting_an_account_deletes_its_cookies_and_ids_are_not_reused(self):
        import yt

        admin = signed_in("admin")
        auth.create_user("temp", "temp password", False)
        temp = signed_in("temp", "temp password")
        good = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tsecret\n"
        temp.put("/api/me/youtube-cookies", json={"text": good}, headers=H)
        tid = temp.get("/api/me").json()["id"]
        self.assertTrue(yt.cookie_path(tid).exists())
        self.assertEqual(admin.delete(f"/api/users/{tid}", headers=H).status_code, 200)
        self.assertFalse(yt.cookie_path(tid).exists())
        self.assertGreater(auth.create_user("next one", "next password", False), tid)

    def test_8_jellyfin_sign_in(self):
        core.set_setting("jellyfin_url", "http://jellyfin.test")
        auth._failures.clear()

        def jellyfin(url, json=None, headers=None, timeout=None):
            if url.endswith("/Sessions/Logout"):
                return mock.Mock(ok=True, status_code=204)
            ok = json["Pw"] == "jf password"
            name = {"m.alias": "member"}.get(json["Username"], json["Username"])  # Jellyfin may spell names its own way
            body = {"User": {"Id": "jf-" + name, "Name": name, "Policy": {"IsAdministrator": False}}, "AccessToken": "t"}
            return mock.Mock(ok=ok, status_code=200 if ok else 401, json=lambda: body)

        try:
            with mock.patch.object(auth.requests, "post", side_effect=jellyfin):
                c = client()
                self.assertEqual(c.post("/api/auth/login", json={"name": "robin", "password": "bad"}, headers=H).status_code, 401)
                self.assertEqual(c.post("/api/auth/login", json={"name": "robin", "password": "jf password"}, headers=H).status_code, 200)
                me = c.get("/api/me").json()
                self.assertTrue(me["jellyfin"] and not me["admin"] and not me["local"])
                # a Jellyfin user named like an existing local account doesn't get that account
                self.assertEqual(client().post("/api/auth/login", json={"name": "member", "password": "jf password"}, headers=H).status_code, 401)
                signed_in("member")
                self.assertEqual(client().post("/api/auth/login", json={"name": "m.alias", "password": "jf password"}, headers=H).status_code, 409)
        finally:
            core.set_setting("jellyfin_url", "")
            auth._failures.clear()


class Assembly(unittest.TestCase):
    def test_music_is_mounted_behind_the_login(self):
        self.assertEqual(client().get("/music/api/playlists").status_code, 401)
        c = signed_in("admin")
        r = c.get("/music/api/playlists")
        self.assertEqual(r.status_code, 200)
        self.assertIn("playlists", r.json())
        self.assertEqual(c.post("/music/api/import", json={"text": ""}).status_code, 403, "the guard covers music too")

    def test_ui_is_served(self):
        r = client().get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<title>Parlor</title>", r.text)
        self.assertEqual(client().get("/api/health").json(), {"ok": True})

    def test_startup_starts_one_music_worker(self):
        self.assertEqual(music.app.router.on_startup, [], "music-fndr's own hook would start a second worker")
        with mock.patch.object(music, "start_worker") as music_start, mock.patch.object(yt, "start") as yt_start:
            with client():
                pass
        music_start.assert_called_once()
        yt_start.assert_called_once()
        self.assertIs(music.others_busy, yt.busy)


class FakeSeerr:
    """Answers like Seerr's API and records what it was asked."""

    def __init__(self):
        self.calls = []
        self.users = [{"id": 7, "username": "member", "displayName": "Member", "email": "m@example.test"},
                      {"id": 9, "jellyfinUserId": "jfrobin", "jellyfinUsername": "robin", "displayName": "Robin"}]

    def __call__(self, method, url, headers=None, params=None, json=None, timeout=None):
        path = url.split("/api/v1/", 1)[1]
        self.calls.append({"method": method, "path": path, "user": (headers or {}).get("X-Api-User"), "params": params, "json": json})
        body = {}
        if path.startswith("user"):
            body = {"results": self.users}
        elif path == "tv/42":
            body = {"id": 42, "name": "Show", "seasons": [{"seasonNumber": 0}, {"seasonNumber": 1}, {"seasonNumber": 2}]}
        elif path == "request" and method == "POST":
            body = {"status": 1}
        elif path == "request" and method == "GET":
            body = {"results": [], "pageInfo": {"pages": 0, "results": 0}}
        elif path.startswith("request/5"):
            body = {"id": 5, "requestedBy": {"id": 9}}
        elif path.startswith("search"):
            body = {"results": [{"mediaType": "movie", "id": 1, "title": "Film", "releaseDate": "2001-02-03", "posterPath": "/p.jpg",
                                 "mediaInfo": {"status": 5}}, {"mediaType": "person", "id": 2, "name": "Person"}], "totalPages": 1}
        return mock.Mock(ok=True, status_code=200, content=b"x", json=lambda: body)


class Seerr(unittest.TestCase):
    def setUp(self):
        core.set_setting("seerr_url", "http://seerr.test")
        core.set_setting("seerr_key", "key")
        seerr._seerr_users.drop("all")
        core.db().execute("UPDATE users SET seerr_id = NULL")
        self.fake = FakeSeerr()
        self.patch = mock.patch.object(seerr.requests, "request", side_effect=self.fake)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        core.set_setting("seerr_url", "")
        core.set_setting("seerr_key", "")

    def link(self, name, seerr_id):
        core.db().execute("UPDATE users SET seerr_id = ? WHERE name = ?", (seerr_id, name))

    def test_requests_are_made_as_the_linked_seerr_user(self):
        member, admin = signed_in("member"), signed_in("admin")
        # a matching name is only a suggestion: anyone could sign up as "member"
        self.assertEqual(member.post("/api/seerr/request", json={"type": "movie", "id": 603}, headers=H).status_code, 403)
        member_id = member.get("/api/me").json()["id"]
        self.assertEqual(admin.get("/api/seerr/users").json()["suggested"][str(member_id)], 7)
        admin.patch(f"/api/users/{member_id}", json={"seerr_id": 7}, headers=H)
        self.assertEqual(member.post("/api/seerr/request", json={"type": "movie", "id": 603}, headers=H).status_code, 200)
        sent = self.fake.calls[-1]
        self.assertEqual((sent["method"], sent["path"], sent["user"]), ("POST", "request", "7"))
        self.assertEqual(sent["json"]["mediaId"], 603)

    def test_jellyfin_accounts_match_by_jellyfin_id(self):
        core.db().execute("UPDATE users SET jellyfin_id = 'jf-robin' WHERE name = 'robin'")
        self.fake.users[1]["jellyfinUserId"] = "jf-robin"
        robin = core.one("SELECT id, name, admin, jellyfin_id, seerr_id FROM users WHERE name = 'robin'")
        if robin:
            self.assertEqual(seerr.seerr_user(robin), 9)

    def test_unlinked_members_cant_request_but_admins_can(self):
        stranger = signed_in("stranger")
        r = stranger.post("/api/seerr/request", json={"type": "movie", "id": 1}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn("isn't linked", r.json()["detail"])
        admin = signed_in("admin")
        self.assertEqual(admin.post("/api/seerr/request", json={"type": "movie", "id": 1}, headers=H).status_code, 200)
        self.assertIsNone(self.fake.calls[-1]["user"], "an unlinked admin requests as the API key's owner")

    def test_tv_all_seasons_skips_specials(self):
        member = signed_in("member")
        self.link("member", 7)
        member.post("/api/seerr/request", json={"type": "tv", "id": 42, "seasons": "all"}, headers=H)
        self.assertEqual(self.fake.calls[-1]["json"]["seasons"], [1, 2])
        self.assertEqual(member.post("/api/seerr/request", json={"type": "tv", "id": 42, "seasons": [0]}, headers=H).status_code, 422)

    def test_search_is_encoded_for_seerr(self):
        r = signed_in("member").get("/api/seerr/search", params={"q": "fast & furious+"}).json()
        self.assertIn("query=fast%20%26%20furious%2B", self.fake.calls[-1]["path"])
        self.assertEqual([x["type"] for x in r["items"]], ["movie", "person"])
        self.assertEqual(r["items"][0]["status"], "available")
        self.assertTrue(r["items"][0]["poster"].startswith("https://image.tmdb.org/"))

    def test_members_only_see_and_cancel_their_own(self):
        member = signed_in("member")
        self.link("member", 7)
        member.get("/api/seerr/requests?scope=all")
        self.assertEqual(self.fake.calls[-1]["params"]["requestedBy"], 7, "scope=all is admins only")
        self.assertEqual(member.delete("/api/seerr/requests/5", headers=H).status_code, 403, "request 5 is someone else's")
        self.assertEqual(member.post("/api/seerr/requests/5/approve", headers=H).status_code, 403)

    def test_not_connected_says_so(self):
        core.set_setting("seerr_key", "")
        c = signed_in("member")
        self.assertFalse(c.get("/api/seerr/status").json()["connected"])
        self.assertEqual(c.get("/api/seerr/discover").status_code, 503)



class SeerrJellyfinLink(unittest.TestCase):
    def test_a_title_jellyfin_has_carries_its_jellyfin_id(self):
        import seerr
        jid = "0123456789abcdef0123456789abcdef"
        self.assertEqual(seerr.status_of({"status": 5, "jellyfinMediaId": jid})["jellyfin"], jid)
        self.assertIsNone(seerr.status_of({"status": 5, "jellyfinMediaId": "../x"})["jellyfin"], "only a real Jellyfin id")
        self.assertIsNone(seerr.status_of(None)["jellyfin"])

if __name__ == "__main__":
    unittest.main()
