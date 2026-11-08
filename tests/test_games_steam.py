"""Games → Steam: an admin sets a Steam Web API key (never sent to a browser), each person links their Steam profile
(a profile address, a custom name or the 17-digit id), and Games shows their owned games with art, playtime and
recently played. A private profile, an unknown name and Steam being down are told apart; answers are kept an hour;
art comes through Parlor from Steam's image server only. Against a stand-in for Steam's API and image server."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import core
import games
from yeerr_test_env import H, client, signed_in

KEY = "0123456789ABCDEF0123456789ABCDEF"
PUBLIC, PRIVATE, EMPTY = "76561197960287930", "76561197960287931", "76561197960287932"
CALLS: list = []
DOWN = threading.Event()
JPEG = b"\xff\xd8\xff\xe0" + b"art" * 50


class Steam(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        CALLS.append((u.path, q))
        if DOWN.is_set():
            return self.send(503, b"down", "text/plain")
        if u.path.startswith("/img/"):  # the image server: /img/<appid>/<kind>.jpg
            appid = u.path.split("/")[2]
            return self.send(200, JPEG, "image/jpeg") if appid == "620" else self.send(404, b"no", "text/plain")
        if q.get("key") != KEY:
            return self.send(403, b"<html>Forbidden</html>", "text/html")
        if u.path == "/ISteamUser/ResolveVanityURL/v1/":
            found = {"gaben": PUBLIC, "hidden": PRIVATE}.get(q.get("vanityurl"))
            return self.send(200, {"response": {"success": 1, "steamid": found} if found else {"success": 42, "message": "No match"}})
        if u.path == "/ISteamUser/GetPlayerSummaries/v2/":
            sid = q.get("steamids")
            if sid not in (PUBLIC, PRIVATE, EMPTY):
                return self.send(200, {"response": {"players": []}})
            return self.send(200, {"response": {"players": [{"steamid": sid, "personaname": "Gabe" if sid == PUBLIC else "Shy",
                                                             "avatarfull": "https://avatars.example/x.jpg",
                                                             "communityvisibilitystate": 3 if sid == PUBLIC else 1}]}})
        if u.path == "/IPlayerService/GetOwnedGames/v1/":
            if q.get("steamid") == PRIVATE:
                return self.send(200, {"response": {}})
            if q.get("steamid") == EMPTY:
                return self.send(200, {"response": {"game_count": 0}})
            return self.send(200, {"response": {"game_count": 2, "games": [
                {"appid": 620, "name": "Portal 2", "playtime_forever": 1500, "rtime_last_played": 1700000000, "img_icon_url": "abc"},
                {"appid": 440, "name": "Team Fortress 2", "playtime_forever": 30, "rtime_last_played": 1600000000}]}})
        if u.path == "/IPlayerService/GetRecentlyPlayedGames/v1/":
            if q.get("steamid") == PRIVATE:
                return self.send(200, {"response": {}})
            return self.send(200, {"response": {"total_count": 1, "games": [{"appid": 620, "name": "Portal 2", "playtime_2weeks": 90, "playtime_forever": 1500}]}})
        self.send(404, b"no", "text/plain")


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Steam)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SERVER.server_port}"


class SteamLibrary(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(games, "STEAM_API", BASE), mock.patch.object(games, "STEAM_ART", BASE + "/img/{appid}/{kind}.jpg")]
        for p in self.patches:
            p.start()
        games._cache.clear()
        CALLS.clear()
        DOWN.clear()
        self.admin = signed_in("admin")
        self.assertEqual(self.admin.put("/api/games/settings", json={"steam_key": KEY}, headers=H).status_code, 200)
        self.me = signed_in("steamer")
        core.db().execute("UPDATE users SET steam_id = NULL")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def link(self, profile, who=None):
        return (who or self.me).put("/api/games/steam", json={"profile": profile}, headers=H)

    def test_the_key_is_an_admins_and_never_reaches_a_browser(self):
        self.assertEqual(self.me.put("/api/games/settings", json={"steam_key": "x"}, headers=H).status_code, 403)
        got = self.admin.get("/api/games/settings").json()
        self.assertEqual(got, {"steam_key_set": True})
        self.link(PUBLIC)
        for r in (self.me.get("/api/games/steam"), self.admin.get("/api/games/settings")):
            self.assertNotIn(KEY, r.text)
        self.assertEqual(self.admin.put("/api/games/settings", json={"steam_key": "not a key!"}, headers=H).status_code, 422)
        r = self.admin.put("/api/games/settings", json={"steam_key": "F" * 32}, headers=H)
        self.assertEqual(r.status_code, 422, "a key Steam refuses is said at once")
        self.assertIn("refused", r.json()["detail"])
        self.assertTrue(self.admin.get("/api/games/settings").json()["steam_key_set"], "and the good one stays")

    def test_a_profile_links_by_address_custom_name_or_id(self):
        for profile, want in ((f"https://steamcommunity.com/profiles/{PUBLIC}/", PUBLIC), ("https://steamcommunity.com/id/gaben", PUBLIC),
                              ("gaben", PUBLIC), (PUBLIC, PUBLIC), ("  hidden ", PRIVATE)):
            r = self.link(profile)
            self.assertEqual(r.status_code, 200, (profile, r.text))
            self.assertEqual(r.json()["steam_id"], want, profile)
        r = self.link("nobody-by-that-name")
        self.assertEqual(r.status_code, 404)
        self.assertIn("No Steam profile", r.json()["detail"])
        self.assertEqual(self.link("https://evil.example/id/gaben").status_code, 422, "only Steam's own addresses")
        self.assertEqual(self.link("x" * 300).status_code, 422)

    def test_owned_and_recent_games_with_playtime_and_art(self):
        self.link(PUBLIC)
        got = self.me.get("/api/games/steam").json()
        self.assertEqual(got["profile"]["name"], "Gabe")
        self.assertFalse(got["private"])
        self.assertEqual([g["name"] for g in got["games"]], ["Portal 2", "Team Fortress 2"], "most played first")
        portal = got["games"][0]
        self.assertEqual((portal["appid"], portal["minutes"], portal["last"]), (620, 1500, 1700000000))
        self.assertEqual(portal["art"], "/api/games/steam/art/620/header")
        self.assertEqual([(g["name"], g["minutes_2weeks"]) for g in got["recent"]], [("Portal 2", 90)])
        art = self.me.get(portal["art"])
        self.assertEqual((art.status_code, art.headers["content-type"], art.content), (200, "image/jpeg", JPEG))
        self.assertEqual(self.me.get("/api/games/steam/art/440/header").status_code, 404)
        self.assertEqual(self.me.get("/api/games/steam/art/620/../../etc").status_code, 404)
        self.assertEqual(self.me.get("/api/games/steam/art/620/anything").status_code, 422, "only Steam's art kinds")

    def test_answers_are_kept_an_hour(self):
        self.link(PUBLIC)
        self.me.get("/api/games/steam")
        n = len(CALLS)
        self.me.get("/api/games/steam")
        self.assertEqual(len(CALLS), n, "the second look is from the cache")
        with mock.patch.object(games, "_now", lambda: games.time.time() + 3601):
            self.me.get("/api/games/steam")
        self.assertGreater(len(CALLS), n, "after an hour, Steam is asked again")
        self.me.get(f"/api/games/steam/art/620/header")
        n = len(CALLS)
        self.me.get(f"/api/games/steam/art/620/header")
        self.assertEqual(len(CALLS), n, "art is kept on disk")

    def test_private_unknown_and_down_are_told_apart(self):
        self.link("hidden")
        got = self.me.get("/api/games/steam").json()
        self.assertTrue(got["private"])
        self.assertEqual(got["games"], [])
        self.assertIn("Game details", got["note"], "it says which Steam privacy setting to change")
        self.link(EMPTY)
        empty = self.me.get("/api/games/steam").json()
        self.assertEqual((empty["private"], empty["games"]), (False, []), "a new account with no games isn't private")
        games._cache.clear()
        DOWN.set()
        r = self.me.get("/api/games/steam")
        self.assertEqual(r.status_code, 502)
        self.assertIn("Steam isn't answering", r.json()["detail"])
        DOWN.clear()
        core.set_setting("steam_key", "F" * 32)  # a key Steam has since revoked
        games._cache.clear()
        r = self.me.get("/api/games/steam")
        self.assertEqual(r.status_code, 502)
        self.assertIn("refused", r.json()["detail"])

    def test_no_key_or_no_profile_say_what_to_do(self):
        self.assertEqual(self.me.get("/api/games/steam").json()["linked"], False)
        self.admin.put("/api/games/settings", json={"steam_key": ""}, headers=H)
        r = self.link("gaben")  # a custom name needs Steam to look it up
        self.assertEqual(r.status_code, 409)
        self.assertIn("Games page", r.json()["detail"])
        self.admin.put("/api/games/settings", json={"steam_key": KEY}, headers=H)
        self.link(PUBLIC)
        self.assertEqual(self.me.delete("/api/games/steam", headers=H).status_code, 200)
        self.assertFalse(self.me.get("/api/games/steam").json()["linked"])

    def test_someone_without_games_is_refused(self):
        uid = core.one("SELECT id FROM users WHERE name = 'steamer'")["id"]
        core.db().execute("UPDATE users SET blocked = 'games' WHERE id = ?", (uid,))
        try:
            self.assertEqual(self.me.get("/api/games/steam").status_code, 403)
        finally:
            core.db().execute("UPDATE users SET blocked = '' WHERE id = ?", (uid,))
        self.assertEqual(client().get("/api/games/steam").status_code, 401)


if __name__ == "__main__":
    unittest.main()
