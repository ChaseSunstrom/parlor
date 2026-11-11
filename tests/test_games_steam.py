"""Games → Steam: an admin sets a Steam Web API key (never sent to a browser), each person links their Steam profile
(a profile address, a custom name or the 17-digit id), and Games shows their owned games with art, playtime and
recently played. A private profile, an unknown name and Steam being down are told apart; answers are kept an hour;
art comes through Parlor from Steam's image server only. With no key at all, a person signs in by scanning a QR code
with Steam's phone app: Parlor keeps that sign-in in its database and never sends it to a browser or a log.
Against a stand-in for Steam's API, its sign-in service and its image server."""
import base64
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
T0 = 1_800_000_000  # the tests' clock starts here


def jwt(**claims):
    """a token shaped like Steam's: three parts, the middle one its claims"""
    return "eyJhbGciOiJFZERTQSJ9." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".c2lnbmF0dXJl"


# the stand-in's sign-in service: whose phone scans, what the next poll answers, which sign-ins Steam has ended
SIGNIN = {"who": PUBLIC, "next": "waiting", "ended": set(), "n": 0}
REQUEST_ID = "cmVxdWVzdC1pZC0xMjM0NQ=="
LOGIN_NAME = "gabe_login_name"


def refresh_of(sid):
    return jwt(sub=sid, aud=["web", "renew", "derive", "mobile"], exp=T0 + 200 * 86400)


def access_of(sid, n=0):
    return jwt(sub=sid, aud=["web", "mobile"], exp=T0 + 86400 * (n + 1), n=n)


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

    def answer(self, eresult, body):
        data = json.dumps({"response": body}).encode()
        self.send_response(200)
        for k, v in (("Content-Type", "application/json"), ("Content-Length", str(len(data))), ("x-eresult", str(eresult))):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # Steam's sign-in service: no key, the fields as one JSON form field
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        q = json.loads(form.get("input_json", ["{}"])[0])
        path = urlparse(self.path).path
        CALLS.append((path, q))
        if DOWN.is_set():
            return self.send(503, b"down", "text/plain")
        if SIGNIN["next"] == "busy":  # Steam's own way of saying not now
            return self.answer(20, {})
        if SIGNIN["next"] == "junk":
            return self.send(200, {"response": "not what was asked for"})
        if path == "/IAuthenticationService/BeginAuthSessionViaQR/v1/":
            SIGNIN["n"] += 1
            SIGNIN["client"] = str(1000 + SIGNIN["n"])
            return self.answer(1, {"client_id": SIGNIN["client"], "challenge_url": f"https://s.team/q/1/{SIGNIN['client']}", "request_id": REQUEST_ID, "interval": 5, "version": 1})
        if path == "/IAuthenticationService/PollAuthSessionStatus/v1/":
            if q.get("client_id") != SIGNIN.get("client") or q.get("request_id") != REQUEST_ID or SIGNIN["next"] == "gone":
                return self.answer(9, {})
            if SIGNIN["next"] == "rotate":
                SIGNIN["client"], SIGNIN["next"] = "2222", "waiting"
                return self.answer(1, {"new_client_id": "2222", "new_challenge_url": "https://s.team/q/1/2222", "had_remote_interaction": False})
            if SIGNIN["next"] == "approved":
                return self.answer(1, {"refresh_token": refresh_of(SIGNIN["who"]), "access_token": "", "account_name": LOGIN_NAME, "had_remote_interaction": True})
            return self.answer(1, {"had_remote_interaction": SIGNIN["next"] == "seen"})
        if path == "/IAuthenticationService/GenerateAccessTokenForApp/v1/":
            sid = q.get("steamid")
            if q.get("refresh_token") != refresh_of(sid) or sid in SIGNIN["ended"]:
                return self.answer(15, {})
            return self.answer(1, {"access_token": access_of(sid, sum(1 for path, _ in CALLS if path.endswith("GenerateAccessTokenForApp/v1/")) - 1)})
        self.send(404, b"no", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        CALLS.append((u.path, q))
        if DOWN.is_set():
            return self.send(503, b"down", "text/plain")
        if u.path.startswith("/profiles/"):  # the public profile, as XML: no key
            sid = u.path.split("/")[2]
            return self.send(200, f"<?xml version='1.0'?><profile><steamID64>{sid}</steamID64><steamID><![CDATA[{'Gabe' if sid == PUBLIC else 'Shy & <b>'}]]></steamID></profile>".encode(), "text/xml")
        token = q.get("access_token", "")
        if token and u.path.startswith("/IPlayerService/"):  # a signed-in person asks about themselves: private or not, Steam answers
            sid = q.get("steamid")
            if "key" in q or not any(token == access_of(sid, n) for n in range(4)) or sid in SIGNIN["ended"]:
                return self.send(401, b"<html>Unauthorized</html>", "text/html")
            if u.path == "/IPlayerService/GetOwnedGames/v1/":
                return self.send(200, {"response": {"game_count": 2, "games": [
                    {"appid": 620, "name": "Portal 2", "playtime_forever": 1500, "rtime_last_played": 1700000000},
                    {"appid": 440, "name": "Team Fortress 2", "playtime_forever": 30, "rtime_last_played": 1600000000}]}})
            return self.send(200, {"response": {"total_count": 1, "games": [{"appid": 620, "name": "Portal 2", "playtime_2weeks": 90, "playtime_forever": 1500}]}})
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

    def test_a_profile_linked_by_key_is_never_answered_from_a_sign_in(self):
        """someone signed in sees the games their private profile hides; another person linking that profile doesn't"""
        core.db().execute("UPDATE users SET steam_id = ?, steam_token = ? WHERE name = 'admin'", (PRIVATE, refresh_of(PRIVATE)))
        with mock.patch.object(games, "_now", lambda: T0):
            own = self.admin.get("/api/games/steam").json()
            other = self.link(PRIVATE) and self.me.get("/api/games/steam").json()
        self.assertEqual([g["name"] for g in own["games"]], ["Portal 2", "Team Fortress 2"])
        self.assertEqual((other["private"], other["games"]), (True, []))
        core.db().execute("UPDATE users SET steam_id = NULL, steam_token = NULL WHERE name = 'admin'")

    def test_someone_without_games_is_refused(self):
        uid = core.one("SELECT id FROM users WHERE name = 'steamer'")["id"]
        core.db().execute("UPDATE users SET blocked = 'games' WHERE id = ?", (uid,))
        try:
            self.assertEqual(self.me.get("/api/games/steam").status_code, 403)
        finally:
            core.db().execute("UPDATE users SET blocked = '' WHERE id = ?", (uid,))
        self.assertEqual(client().get("/api/games/steam").status_code, 401)


class SteamSignIn(unittest.TestCase):
    """no key anywhere: a QR code, scanned with Steam's phone app"""

    def setUp(self):
        self.clock = [float(T0)]
        self.patches = [mock.patch.object(games, "STEAM_API", BASE), mock.patch.object(games, "STEAM_ART", BASE + "/img/{appid}/{kind}.jpg"),
                        mock.patch.object(games, "STEAM_PROFILE", BASE + "/profiles/{sid}/?xml=1"), mock.patch.object(games, "_now", lambda: self.clock[0])]
        for p in self.patches:
            p.start()
        for d in (games._cache, games._qr, games._tokens, games._began, CALLS):
            d.clear()
        DOWN.clear()
        SIGNIN.update(who=PUBLIC, next="waiting", ended=set())
        core.set_setting("steam_key", "")
        self.me = signed_in("scanner")
        core.db().execute("UPDATE users SET steam_id = NULL, steam_token = NULL")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def poll(self, who=None):
        self.clock[0] += 5  # the page asks every five seconds
        return (who or self.me).get("/api/games/steam/qr")

    def scan(self, sid=PUBLIC, who=None):
        """start a sign-in, the phone approves it, the page's next look finds it done"""
        r = (who or self.me).post("/api/games/steam/qr", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        SIGNIN.update(who=sid, next="approved")
        return r, self.poll(who)

    def kept(self, name="scanner"):
        return core.one("SELECT steam_id, steam_token FROM users WHERE name = ?", name)

    def test_a_code_starts_with_no_key_and_a_member_needs_no_admin(self):
        r = self.me.post("/api/games/steam/qr", headers=H)
        got = r.json()
        self.assertEqual((r.status_code, got["state"]), (200, "waiting"))
        self.assertTrue(got["url"].startswith("https://s.team/q/1/"), got["url"])
        self.assertTrue(got["svg"].startswith("<svg") and "<script" not in got["svg"])
        self.assertNotIn(REQUEST_ID, r.text, "the half of the sign-in that proves who asked stays on the server")
        path, sent = CALLS[0]
        self.assertEqual((path, sent["device_friendly_name"]), ("/IAuthenticationService/BeginAuthSessionViaQR/v1/", "Parlor"), "Steam's app shows the person it is Parlor asking")
        self.assertFalse(any("key" in q for _, q in CALLS))
        self.assertEqual(self.me.post("/api/games/steam/qr", headers=H).json()["url"], got["url"], "a second press shows the same code")
        self.assertEqual(len(CALLS), 1)
        waiting = self.poll().json()
        self.assertEqual((waiting["state"], waiting["url"], waiting["seen"]), ("waiting", got["url"], False))
        SIGNIN["next"] = "seen"
        self.assertTrue(self.poll().json()["seen"], "scanned, not yet approved: the page can say so")
        self.assertEqual(self.kept()["steam_token"], None)

    def test_the_code_follows_steam_when_it_changes_it(self):
        first = self.me.post("/api/games/steam/qr", headers=H).json()
        SIGNIN["next"] = "rotate"
        second = self.poll().json()
        self.assertEqual(second["url"], "https://s.team/q/1/2222")
        self.assertNotEqual(second["svg"], first["svg"])
        self.assertEqual(self.poll().json()["url"], second["url"], "and the next look asks about the new one")
        self.assertEqual(CALLS[-1][1]["client_id"], "2222")

    def test_a_page_asking_too_often_isnt_passed_on_to_steam(self):
        self.me.post("/api/games/steam/qr", headers=H)
        self.poll()
        n = len(CALLS)
        for _ in range(5):
            self.assertEqual(self.me.get("/api/games/steam/qr").json()["state"], "waiting")
        self.assertEqual(len(CALLS), n)

    def test_scanning_signs_in_and_the_library_shows_with_no_key(self):
        with self.assertLogs("parlor", "DEBUG") as logs:
            games.log.debug("the log is being read")
            begun, done = self.scan()
            self.assertEqual(done.json(), {"state": "done"})
            lib = self.me.get("/api/games/steam")
        got = lib.json()
        self.assertEqual((got["linked"], got["steam_id"], got["private"], got["profile"]["name"]), (True, PUBLIC, False, "Gabe"))
        self.assertEqual([(g["name"], g["minutes"]) for g in got["games"]], [("Portal 2", 1500), ("Team Fortress 2", 30)], "most played first")
        self.assertEqual([(g["name"], g["minutes_2weeks"]) for g in got["recent"]], [("Portal 2", 90)])
        self.assertFalse(any("key" in q for _, q in CALLS), "no call to Steam carried a key")
        self.assertEqual(self.kept()["steam_token"], refresh_of(PUBLIC), "the sign-in is kept in the (encrypted) database")
        secrets = (refresh_of(PUBLIC), access_of(PUBLIC), LOGIN_NAME, REQUEST_ID)
        for r in (begun, done, lib, self.me.get("/api/games"), self.me.get("/api/games/steam/qr"), self.me.get("/api/me"), self.me.get("/api/auth/users")):
            for secret in secrets:
                self.assertNotIn(secret, r.text, f"{r.url} must not carry the sign-in")
        for secret in secrets:
            self.assertNotIn(secret, "\n".join(logs.output))
        self.assertEqual(self.me.get("/api/games/steam/qr").json(), {"state": "done"}, "a second tab showing the same code hears it too")
        n = len(CALLS)
        self.poll()
        self.assertEqual(len(CALLS), n, "and a finished sign-in leaves nothing to ask Steam about")

    def test_the_owner_sees_games_a_private_profile_hides(self):
        self.scan(PRIVATE)
        got = self.me.get("/api/games/steam").json()
        self.assertEqual((got["private"], got["note"], len(got["games"])), (False, "", 2))
        self.assertEqual(got["profile"]["name"], "Shy & <b>", "the name as Steam has it (the page escapes it)")
        self.assertTrue(got["signed_in"])
        games._cache.clear()
        with mock.patch.object(games, "steam", lambda path, **kw: {}):  # Steam takes the pass and sends no list
            empty = self.me.get("/api/games/steam").json()
        self.assertEqual((empty["private"], empty["games"]), (True, []))
        self.assertIn("sign in again", empty["note"], "said as what it is, not as a private profile")
        self.assertNotIn("Privacy Settings", empty["note"])

    def test_steams_pass_is_asked_for_once_and_again_when_it_runs_out(self):
        self.scan()
        renewals = lambda: sum(1 for path, _ in CALLS if path.endswith("GenerateAccessTokenForApp/v1/"))  # noqa: E731
        for _ in range(3):
            games._cache.clear()
            self.assertEqual(self.me.get("/api/games/steam").status_code, 200)
        self.assertEqual(renewals(), 1)
        self.clock[0] += 86400  # the day-long pass is over
        games._cache.clear()
        self.assertEqual(self.me.get("/api/games/steam").status_code, 200)
        self.assertEqual(renewals(), 2)
        uid = core.one("SELECT id FROM users WHERE name = 'scanner'")["id"]
        games._tokens[uid] = (jwt(sub=PUBLIC, exp=T0 * 2), T0 * 2)  # a pass Steam turns away before its time
        games._cache.clear()
        self.assertEqual(self.me.get("/api/games/steam").status_code, 200, "a new pass is asked for at once: the person sees nothing of it")
        self.assertEqual(renewals(), 3)
        games._cache.clear()
        games._tokens.clear()
        SIGNIN["next"] = "busy"
        r = self.me.get("/api/games/steam")
        self.assertEqual((r.status_code, "busy" in r.json()["detail"]), (502, True), "Steam being busy isn't a sign-in that ended")

    def test_a_sign_in_ended_at_steam_says_so_and_a_new_scan_mends_it(self):
        self.scan()
        self.assertEqual(self.me.get("/api/games/steam").status_code, 200)
        SIGNIN["ended"].add(PUBLIC)
        self.clock[0] += 2 * 86400
        r = self.me.get("/api/games/steam")
        self.assertEqual(r.status_code, 409)
        self.assertIn("sign in again", r.json()["detail"])
        DOWN.set()
        self.assertEqual(self.me.get("/api/games/steam").status_code, 502, "Steam being down is not a sign-in that ended")
        DOWN.clear()
        self.assertEqual(self.kept()["steam_token"], refresh_of(PUBLIC), "nothing is thrown away on Steam's say-so")
        SIGNIN["ended"].clear()
        self.assertEqual(self.scan()[1].json(), {"state": "done"})
        self.assertEqual(self.me.get("/api/games/steam").status_code, 200)

    def test_a_code_that_ran_out_and_steam_down_are_told_apart(self):
        self.assertEqual(self.me.get("/api/games/steam/qr").json(), {"state": "gone"}, "nothing was started")
        self.me.post("/api/games/steam/qr", headers=H)
        for hiccup in ("busy", "junk"):
            SIGNIN["next"] = hiccup
            self.assertEqual(self.poll().status_code, 502, "a hiccup of Steam's doesn't throw the code away")
        SIGNIN["next"] = "waiting"
        self.assertEqual(self.poll().json()["state"], "waiting")
        SIGNIN["next"] = "gone"
        self.assertEqual(self.poll().json(), {"state": "gone"})
        self.assertEqual(self.poll().json(), {"state": "gone"})
        SIGNIN["next"] = "waiting"
        self.clock[0] = T0 + 9
        r = self.me.post("/api/games/steam/qr", headers=H)
        self.assertEqual(r.status_code, 429, "a page asking for code after code isn't passed on to Steam")
        self.clock[0] = T0 + 11
        self.assertEqual(self.me.post("/api/games/steam/qr", headers=H).status_code, 200)
        self.clock[0] += 60
        DOWN.set()
        r = self.me.post("/api/games/steam/qr", headers=H)
        self.assertEqual(r.status_code, 502)
        self.assertIn("Steam isn't answering", r.json()["detail"])

    def test_unlinking_or_linking_a_profile_forgets_the_sign_in(self):
        self.scan()
        self.me.get("/api/games/steam")
        self.assertEqual(self.me.delete("/api/games/steam", headers=H).status_code, 200)
        self.assertEqual(dict(self.kept()), {"steam_id": None, "steam_token": None})
        self.assertEqual(games._tokens, {})
        self.assertEqual(self.me.get("/api/games/steam").json()["linked"], False)
        self.clock[0] += 60
        self.scan()
        self.assertEqual(self.me.put("/api/games/steam", json={"profile": EMPTY}, headers=H).status_code, 200)
        self.assertEqual(dict(self.kept()), {"steam_id": EMPTY, "steam_token": None}, "another profile is never asked about with this one's sign-in")

    def test_one_persons_sign_in_is_theirs_alone(self):
        other = signed_in("onlooker")
        self.me.post("/api/games/steam/qr", headers=H)
        self.assertEqual(other.get("/api/games/steam/qr").json(), {"state": "gone"}, "someone else can't follow or finish it")
        self.scan()
        self.assertEqual(other.get("/api/games/steam").json()["linked"], False)
        self.assertEqual(client().post("/api/games/steam/qr", headers=H).status_code, 401)

    def test_an_answer_that_isnt_steams_is_refused(self):
        with mock.patch.object(games, "steam_auth", lambda call, **f: (1, {"client_id": "1", "request_id": "r", "challenge_url": "javascript:alert(1)"})):
            self.assertEqual(self.me.post("/api/games/steam/qr", headers=H).status_code, 502, "only Steam's own sign-in address is ever shown as a link")
        self.clock[0] += 60
        odd = {"client_id": "1", "request_id": "r", "challenge_url": "https://s.team/q/1/1", "interval": "nan"}
        with mock.patch.object(games, "steam_auth", lambda call, **f: (1, odd)):
            self.assertEqual(self.me.post("/api/games/steam/qr", headers=H).json()["every"], 5, "a wait that isn't a number is five seconds")
        with mock.patch.object(games, "steam_auth", lambda call, **f: (1, {"refresh_token": "not-a-token"})):  # pragma: allowlist secret (made up)
            self.assertEqual(self.poll().status_code, 502)
        self.assertEqual(dict(self.kept()), {"steam_id": None, "steam_token": None})

    def test_unlinking_while_steam_answers_stays_unlinked_and_an_old_sign_in_isnt_a_new_one(self):
        self.me.post("/api/games/steam/qr", headers=H)

        def late(call, **fields):
            self.me.delete("/api/games/steam", headers=H)  # the person unlinks in another tab while Steam is answering
            return 1, {"refresh_token": refresh_of(PUBLIC)}
        with mock.patch.object(games, "steam_auth", late):
            self.assertEqual(self.poll().json(), {"state": "gone"})
        self.assertEqual(dict(self.kept()), {"steam_id": None, "steam_token": None})
        core.db().execute("UPDATE users SET steam_id = ?, steam_token = 'ended-long-ago' WHERE name = 'scanner'", (PUBLIC,))  # pragma: allowlist secret (made up)
        self.assertEqual(self.poll().json(), {"state": "gone"}, "a sign-in kept from before is not this code being approved")


if __name__ == "__main__":
    unittest.main()
