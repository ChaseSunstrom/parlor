"""Parlor's API for other programs: keys (made in Settings, shown once, stored hashed, search-only or full, deleted at
once), the guard taking them in place of a session with the person's own section rules, what a key can never do,
one search across every source, one way to get anything, and following it. The sources are fakes."""
import unittest
from unittest import mock

import yeerr_test_env as env
from yeerr_test_env import H, signed_in

import acl
import activity
import api
import core
import jf
import music
import musicreq
import seerr
import yt


def keyed(key):
    c = env.client()
    c.headers.update({"X-Api-Key": key})
    return c


class Keys(unittest.TestCase):
    def setUp(self):
        self.me = signed_in("api-person")
        self.uid = self.me.get("/api/me").json()["id"]
        core.db().execute("UPDATE users SET blocked = '' WHERE id = ?", (self.uid,))

    def make(self, scope="full", name="script"):
        r = self.me.post("/api/me/api-keys", json={"name": name, "scope": scope}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_a_key_is_shown_once_stored_hashed_and_acts_as_its_person(self):
        k = self.make()
        self.assertTrue(k["key"].startswith("pk_") and len(k["key"]) > 40)
        listed = self.me.get("/api/me/api-keys").json()
        self.assertNotIn(k["key"], str(listed), "never shown again")
        self.assertEqual(listed[0]["hint"], k["key"][:7] + "…")
        stored = core.one("SELECT * FROM api_keys WHERE id = ?", k["id"])
        self.assertNotIn(k["key"], str(dict(stored)), "only its hash is kept")
        bot = keyed(k["key"])
        self.assertEqual(bot.get("/api/me").json()["name"], "api-person", "no session, no X-Requested-With: the key is enough")
        self.assertIsNotNone(core.one("SELECT used FROM api_keys WHERE id = ?", k["id"])["used"], "last use is shown")
        bearer = env.client()
        self.assertEqual(bearer.get("/api/me", headers={"Authorization": f"Bearer {k['key']}"}).status_code, 200)
        self.assertEqual(keyed("pk_" + "x" * 43).get("/api/me").status_code, 401)
        self.me.delete(f"/api/me/api-keys/{k['id']}", headers=H)
        self.assertEqual(bot.get("/api/me").status_code, 401, "deleted: stops at once")

    def test_search_only_keys_read_and_nothing_else(self):
        k = self.make("search")
        bot = keyed(k["key"])
        self.assertEqual(bot.get("/api/me").status_code, 200)
        r = bot.post("/api/v1/get", json={"kind": "movie", "id": "550"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("search", r.json()["detail"])

    def test_the_persons_sections_apply(self):
        k = self.make()
        core.db().execute("UPDATE users SET blocked = 'watch' WHERE id = ?", (self.uid,))
        self.assertEqual(keyed(k["key"]).get("/api/yt/search?q=x").status_code, 403)

    def test_what_a_key_can_never_do(self):
        bot = keyed(self.make()["key"])
        for method, path in (("POST", "/api/me/api-keys"), ("GET", "/api/me/api-keys"), ("POST", "/api/me/password"), ("POST", "/api/me/app-passwords"),
                             ("POST", "/api/auth/logout-everywhere"), ("GET", "/api/vault/key"), ("POST", "/api/private/unlock"), ("GET", "/api/private")):
            self.assertEqual(bot.request(method, path, json={}).status_code, 403, path)

    def test_signing_out_everywhere_ends_the_keys_too(self):
        bot = keyed(self.make()["key"])
        self.me.post("/api/auth/logout-everywhere", headers=H)
        self.assertEqual(bot.get("/api/me").status_code, 401)

    def test_another_bearer_token_is_left_alone(self):
        r = self.me.get("/api/me", headers={"Authorization": "Bearer some-proxy-token"})
        self.assertEqual(r.status_code, 200, "not a Parlor key: the session decides, as before")

    def test_an_admins_key_reads_what_the_admin_does_but_cant_change_the_server(self):
        admin = signed_in("admin")
        k = admin.post("/api/me/api-keys", json={"name": "ops", "scope": "full"}, headers=H).json()
        bot = keyed(k["key"])
        self.assertEqual(bot.get("/api/settings").status_code, 200)
        for method, path, body in (("POST", "/api/users", {"name": "sneaky", "password": "a long password 1", "admin": True}),
                                   ("PUT", "/api/settings", {"jellyfin_url": "http://elsewhere.example.test"}),
                                   ("POST", "/api/stack/run", {}), ("PUT", f"/api/users/{self.uid}/access", {"blocked": []})):
            r = bot.request(method, path, json=body)
            self.assertEqual(r.status_code, 403, (path, r.text))
        self.assertIsNone(core.one("SELECT 1 AS x FROM users WHERE name = 'sneaky'"), "a key can't make itself an account")

    def test_the_docs_are_there_for_signed_in_people(self):
        self.assertEqual(env.client().get("/api/docs").status_code, 401)
        self.assertEqual(env.client().get("/music/openapi.json").status_code, 401, "music-fndr's own docs too")
        self.assertEqual(self.me.get("/api/docs").status_code, 200)
        spec = self.me.get("/api/openapi.json").json()
        self.assertIn("/api/v1/search", spec["paths"])
        self.assertIn("/api/v1/get", spec["paths"])


class SearchAndGet(unittest.TestCase):
    def setUp(self):
        self.me = signed_in("api-user")
        self.uid = self.me.get("/api/me").json()["id"]
        core.db().execute("UPDATE users SET blocked = '' WHERE id = ?", (self.uid,))
        self.bot = keyed(self.me.post("/api/me/api-keys", json={"name": "bot", "scope": "full"}, headers=H).json()["key"])
        fakes = {
            (yt, "search"): lambda q, request, user: {"items": [{"type": "video", "id": "dQw4w9WgXcQ", "title": "A video", "channel": "Chan", "thumbnail": "t.jpg", "duration": 60}]},
            (seerr, "search"): lambda q, page=1, _=None: {"items": [{"type": "movie", "id": 550, "title": "A Film", "year": "1999", "poster": "p.jpg", "status": "available"},
                                                                     {"type": "person", "id": 2, "name": "Someone"}]},
            (music, "search"): lambda q: {"tracks": [{"id": "trk1", "name": "A Song", "artists": [{"name": "Band"}], "album": {"name": "Rec"}, "image": "s.jpg"}],
                                          "albums": [{"id": "alb1", "name": "Rec", "artists": [{"name": "Band"}], "year": "2001", "image": "a.jpg"}],
                                          "artists": [], "playlists": []},
            (activity, "music_search"): lambda q, _=None: {"items": [{"type": "album", "id": "b1392450-e666-3926-a536-22c65f834433", "title": "Rec", "artist": "Band", "year": 2001}]},
            (jf, "items"): mock.Mock(side_effect=RuntimeError("Jellyfin is down")),
        }
        for (mod, name), fn in fakes.items():
            p = mock.patch.object(mod, name, fn)
            p.start()
            self.addCleanup(p.stop)

    def test_one_search_across_every_source(self):
        r = self.bot.get("/api/v1/search?q=rec")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        got = {(x["source"], x["kind"], x["id"]) for x in body["items"]}
        self.assertEqual(got, {("youtube", "video", "dQw4w9WgXcQ"), ("seerr", "movie", "550"), ("spotify", "track", "trk1"), ("spotify", "album", "alb1"),
                               ("lidarr", "album", "b1392450-e666-3926-a536-22c65f834433")})
        film = next(x for x in body["items"] if x["kind"] == "movie")
        self.assertEqual((film["title"], film["year"], film["image"], film["get"]), ("A Film", "1999", "p.jpg", [{"kind": "movie", "id": "550"}]))
        self.assertEqual(body["errors"], {"library": "Jellyfin is down"}, "a source that's down is reported, not fatal")
        only = self.bot.get("/api/v1/search?q=rec&in=youtube").json()
        self.assertEqual({x["source"] for x in only["items"]}, {"youtube"})
        self.assertEqual(self.bot.get("/api/v1/search?q=rec&in=nope").status_code, 422)

    def test_sources_you_cant_use_are_left_out(self):
        core.db().execute("UPDATE users SET blocked = 'watch,media' WHERE id = ?", (self.uid,))
        sources = {x["source"] for x in self.bot.get("/api/v1/search?q=rec").json()["items"]}
        self.assertEqual(sources, {"spotify", "lidarr"})

    def test_get_anything_and_follow_it(self):
        with mock.patch.object(yt, "queue_video", lambda body, user: {"job": "yj1", "status": "queued"}), \
                mock.patch.object(yt, "find_job", lambda job_id: {"job": "yj1", "user_id": self.uid, "status": "downloading", "progress": 0.4,
                                                                   "title": "A video", "error": None}):
            r = self.bot.post("/api/v1/get", json={"kind": "youtube_video", "id": "dQw4w9WgXcQ", "quality": "720"})
            self.assertEqual(r.json()["job"], "youtube:yj1")
            j = self.bot.get("/api/v1/jobs/youtube:yj1").json()
            self.assertEqual((j["state"], j["progress"]), ("downloading", 0.4))
        with mock.patch.object(music, "queue_download", lambda kind, id, title=None, subtitle=None, image=None: {"job": "mj1"}), \
                mock.patch.object(music, "jobs", [{"job": "mj1", "status": "done", "title": "Rec", "error": None, "songs": {"a": {"status": "done"}, "b": {"status": "failed"}}}]):
            self.assertEqual(self.bot.post("/api/v1/get", json={"kind": "album", "id": "alb1"}).json()["job"], "music:mj1")
            j = self.bot.get("/api/v1/jobs/music:mj1").json()
            self.assertEqual((j["state"], j["done"], j["total"]), ("done", 1, 2))
        with mock.patch.object(activity, "request_music", lambda kind, mbid: {"album_id": 77, "title": "Rec", "already": False}), \
                mock.patch.object(musicreq, "add", lambda uid, got, now=None: musicreq.track_album(uid, got["album_id"], 1.0, title="Rec")):
            j = self.bot.post("/api/v1/get", json={"kind": "lidarr_album", "id": "b1392450-e666-3926-a536-22c65f834433"}).json()
            self.assertTrue(j["job"].startswith("request:"))
            self.assertEqual(self.bot.get(f"/api/v1/jobs/{j['job']}").json()["state"], "searching")
        with mock.patch.object(seerr, "make_request", lambda body, user: {"ok": True, "status": "pending"}), \
                mock.patch.object(seerr, "title", lambda kind, tmdb, user: {"title": "A Film", "status": "processing"}):
            j = self.bot.post("/api/v1/get", json={"kind": "movie", "id": "550"}).json()
            self.assertEqual(j["job"], "seerr:movie:550")
            self.assertEqual(self.bot.get("/api/v1/jobs/seerr:movie:550").json()["state"], "processing")

    def test_get_checks_the_sections_and_the_input(self):
        core.db().execute("UPDATE users SET blocked = 'downloads' WHERE id = ?", (self.uid,))
        r = self.bot.post("/api/v1/get", json={"kind": "youtube_video", "id": "dQw4w9WgXcQ"})
        self.assertEqual(r.status_code, 403, "downloading needs Downloads")
        core.db().execute("UPDATE users SET blocked = '' WHERE id = ?", (self.uid,))
        self.assertEqual(self.bot.post("/api/v1/get", json={"kind": "nope", "id": "x"}).status_code, 422)
        self.assertEqual(self.bot.get("/api/v1/jobs/youtube:../x").status_code, 404)
        core.db().execute("UPDATE users SET blocked = 'watch,music' WHERE id = ?", (self.uid,))
        with mock.patch.object(yt, "find_job", lambda job_id: {"job": "yj1", "user_id": self.uid, "status": "done"}):
            self.assertEqual(self.bot.get("/api/v1/jobs/youtube:yj1").status_code, 404, "following needs the section it's in")
        self.assertEqual(self.bot.get("/api/v1/jobs/music:mj1").status_code, 404)
        self.assertEqual(self.bot.post("/api/v1/get", json={"kind": "movie", "id": "５５０"}).status_code, 422, "digits only")


if __name__ == "__main__":
    unittest.main()
