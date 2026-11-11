"""Games you ask for, inside Parlor (app/gamereq.py): rows to browse and a title search answered from a stand-in
Steam; a request made in a stand-in Questarr by the game's Steam id; a member's request waits for an admin's yes; the
worker follows it; a release is taken unseen only when every check passes, and otherwise an admin picks from the
list, whose links (they carry Prowlarr's key) never reach a browser."""
import json
import time
import unittest
from email.utils import formatdate
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from yeerr_test_env import H, signed_in

import core
import gamelookup
import gamereq
import stack
from test_gamelookup import NOW, Steam

LINK = "http://prowlarr:9696/3/download?apikey=PROWLARR-KEY&file="  # pragma: allowlist secret (a made-up key)


def release(title, seeders=20, score=120, match="exact", kind="full_game", part="main", known="scene_release", accepted=True, **more):
    """One search result, in Questarr 1.6.0's shape."""
    lines = [{"ruleId": "title_exact", "points": 100}] + ([{"ruleId": known, "points": 20}] if known else [])
    return {"title": title, "link": LINK + title.replace(" ", "."), "size": 7_000_000_000, "seeders": seeders, "leechers": 2, "indexerName": "Harbour Index",
            "downloadType": "torrent", "group": None, "pubDate": "Tue, 19 Apr 2011 20:04:15 +0000", "aiReleaseType": kind,
            "evaluation": {"accepted": accepted, "score": score, "lines": lines, "rejections": [], "titleMatch": match, "category": part},
            "trust": {"level": "unverified", "reasons": []}, **more}


class Questarr:
    """Questarr's request API, release search and download route, as far as Parlor uses them."""

    def __init__(self):
        self.password, self.token, self.logins = "Pq7-its-own", "jwt-1", 0
        self.ledger, self.releases, self.grabs, self.searches, self.down = {}, [], [], [], False
        self.slow, self.during_search = False, None  # slow: the request is taken, its game not made yet

    def answer(self, status, body):
        return mock.Mock(status_code=status, ok=status < 400, text=json.dumps(body), json=lambda: body)

    def describe(self, ext):
        r = self.ledger[ext]
        return {"externalRequestId": ext, "status": "accepted" if self.slow else r["status"], "deliverable": False, "error": r.get("error"), "title": r["title"],
                "game": None if self.slow else {"id": r["game"], "title": r["title"], "status": "wanted"}, "actions": {"retry": r["status"] == "failed", "cancel": r["status"] not in ("available", "cancelled")}}

    def __call__(self, method, url, **kw):
        if self.down:
            raise gamereq.requests.ConnectionError("no route to Questarr")
        path, body = urlsplit(url).path, kw.get("json") or {}
        assert url.startswith("http://questarr:5000/"), url
        if path == "/api/auth/login":
            self.logins += 1
            good = (body.get("username"), body.get("password")) == ("parlor", self.password)
            return self.answer(200, {"token": self.token}) if good else self.answer(401, {"error": "Invalid credentials"})
        if (kw.get("headers") or {}).get("Authorization") != f"Bearer {self.token}":
            return self.answer(401, {"error": "Invalid token"})
        if path == gamereq.API and method == "POST":
            ext = body["externalRequestId"]
            assert body["variant"] == {"operatingSystem": "windows", "architecture": "x64"} and isinstance(body["igdbId"], int) and body["title"]
            if ext in self.ledger:
                return self.answer(200, self.describe(ext))
            if any(r["app"] == body["igdbId"] and r["status"] != "cancelled" for r in self.ledger.values()):
                return self.answer(409, {"error": "Game is already linked to another request."})
            self.ledger[ext] = {"app": body["igdbId"], "title": body["title"], "status": "searching", "game": f"game-{body['igdbId']}"}
            return self.answer(201, self.describe(ext))
        if path.startswith(gamereq.API + "/"):
            ext, _, action = path[len(gamereq.API) + 1:].partition("/")
            if ext not in self.ledger:
                return self.answer(404, {"error": "Request not found."})
            if action == "cancel":
                self.ledger[ext]["status"] = "cancelled"
            return self.answer(200, self.describe(ext))
        if path == "/api/search":
            q = parse_qs(urlsplit(url).query) or {k: [str(v)] for k, v in (kw.get("params") or {}).items()}
            self.searches.append((q["query"][0], q["gameId"][0]))
            if self.during_search:
                self.during_search()
            return self.answer(200, {"items": self.releases, "total": len(self.releases), "offset": 0})
        if path == "/api/downloads" and method == "POST":
            self.grabs.append(body)
            for r in self.ledger.values():
                if r["game"] == body.get("gameId") and r["status"] != "cancelled":
                    r["status"] = "downloading"
            return self.answer(200, {"success": True, "id": "hash", "downloaderId": "d1"})
        raise AssertionError(f"Parlor asked Questarr for something this test doesn't know: {method} {path}")


class Base(unittest.TestCase):
    def setUp(self):
        self.steam, self.q = Steam(), Questarr()
        for obj, name, value in ((gamelookup, "steam", self.steam), (gamelookup, "_now", lambda: NOW), (gamereq.requests, "request", self.q),
                                 (stack, "_questarr_own", lambda: self.q.password), (stack, "questarr_url", lambda: "http://questarr:5000")):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.admin, self.member = signed_in("admin"), signed_in("gamer")
        core.db().execute("DELETE FROM game_requests")
        core.db().execute("DELETE FROM settings WHERE key = 'games_auto_pick'")
        core.db().execute("UPDATE users SET blocked = '' WHERE name = 'gamer'")
        gamereq._token["value"] = ""

    def ask(self, who, app=10, status=200):
        r = who.post(f"/api/games/requests/game/{app}", headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def listed(self, who):
        return who.get("/api/games/requests").json()["items"]

    def state(self, who=None):
        return [(x["title"], x["state"]) for x in self.listed(who or self.admin)]


class Browse(Base):
    def test_browse_rows_come_from_steam_with_no_questarr_needed(self):
        with mock.patch.object(stack, "_questarr_own", lambda: ""):
            d = self.member.get("/api/games/requests/discover").json()
            self.assertFalse(self.member.get("/api/games/requests/status").json()["ready"])
        titles = {row: [g["title"] for g in d[row]] for row in ("popular", "new", "upcoming")}
        self.assertIn("Doom", titles["popular"])
        self.assertEqual((titles["new"], titles["upcoming"]), (["Fresh Quest"], ["Soon Quest"]))
        self.assertNotIn("After Dark", titles["popular"], "adult-only games stay out of the rows")
        self.assertNotIn("Console of Steam", titles["popular"])
        doom = next(g for g in d["popular"] if g["title"] == "Doom")
        self.assertEqual((doom["id"], doom["year"], doom["rating"], doom["status"]), (10, 2025, 97, None))
        self.assertTrue(doom["poster"].startswith("https://shared.akamai.steamstatic.com/"))
        self.assertEqual(self.q.logins, 0)

    def test_browse_search_by_title_and_a_games_page(self):
        found = self.member.get("/api/games/requests/search", params={"q": "doom"}).json()["items"]
        self.assertEqual([g["title"] for g in found], ["Doom", "Doom Eternal"], "games only: no add-ons")
        g = self.member.get("/api/games/requests/game/20").json()
        self.assertEqual((g["title"], g["summary"], g["developers"], g["request"]), ("Doom Eternal", "About Doom Eternal.", ["Makers"], None))
        self.assertIn("Horror", g["genres"])
        self.assertTrue(g["screenshots"] and all(s.startswith("https://") for s in g["screenshots"]))
        self.assertTrue(g["ready"])
        for app in (70, 60, 80, 99, 123456):  # an add-on, hardware, an adult-only game, a hidden entry, nothing at all
            self.assertEqual(self.member.get(f"/api/games/requests/game/{app}").status_code, 404, app)
            self.ask(self.admin, app, status=404)
        self.assertEqual(self.member.get("/api/games/requests/search", params={"q": ""}).json()["items"], [])

    def test_browse_and_asking_are_closed_with_the_games_section(self):
        core.db().execute("UPDATE users SET blocked = 'games' WHERE name = 'gamer'")
        for r in (self.member.get("/api/games/requests/discover"), self.member.post("/api/games/requests/game/10", headers=H), self.member.get("/api/games/requests")):
            self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(signed_in("nobody-yet").get("/api/games/requests").status_code, 200)
        from yeerr_test_env import client
        self.assertEqual(client().get("/api/games/requests/discover").status_code, 401)


class Asking(Base):
    def test_an_admins_request_goes_to_questarr_by_steam_id_and_is_followed(self):
        got = self.ask(self.admin)
        self.assertEqual((got["state"], got["label"], got["title"], got["by"], got["mine"]), ("searching", "Searching", "Doom", "admin", True))
        self.assertEqual(self.q.ledger, {}, "the page doesn't wait for Questarr: the worker sends it")
        self.q.releases = [release("Doom-HARBOUR", seeders=9), release("Doom Repack", seeders=40, score=108, kind="repack", known="repack"),
                           release("Doom Update 3-HARBOUR", seeders=90, kind="update", part="update")]
        gamereq.tick(NOW)
        (ext, sent), = self.q.ledger.items()
        self.assertEqual((sent["app"], sent["title"]), (10, "Doom"))
        self.assertRegex(ext, r"^parlor-\d+-\d+$")
        self.assertEqual(self.state(), [("Doom", "searching")])
        gamereq.tick(NOW + 1)  # now Questarr knows the game: Parlor searches its indexers and takes the best sure release
        self.assertEqual(self.q.searches, [("Doom", "game-10")])
        self.assertEqual([(g["title"], g["gameId"], g["downloadType"]) for g in self.q.grabs], [("Doom-HARBOUR", "game-10", "torrent")])
        self.assertTrue(self.q.grabs[0]["url"].startswith(LINK))
        row = self.listed(self.admin)[0]
        self.assertEqual((row["state"], row["release"], row["picks"]), ("downloading", "Doom-HARBOUR", []))
        self.q.ledger[ext]["status"] = "available"
        gamereq.tick(NOW + 400)
        self.assertEqual(self.state(), [("Doom", "available")])
        self.assertEqual(self.member.get("/api/games/requests/game/10").json()["status"], "available", "everyone sees it's there")
        self.assertEqual(self.q.logins, 1, "one sign-in for all of it")
        gamereq.tick(NOW + 9000)
        self.assertEqual(len(self.q.searches), 1, "a finished request is left alone")

    def test_a_members_request_waits_for_an_admins_yes(self):
        got = self.ask(self.member)
        self.assertEqual((got["state"], got["label"], got["by"]), ("pending", "Waiting for an admin", "gamer"))
        gamereq.tick(NOW)
        self.assertEqual(self.q.ledger, {}, "nothing reaches Questarr before the yes")
        rid = got["id"]
        self.assertEqual(self.member.post(f"/api/games/requests/{rid}/approve", headers=H).status_code, 403)
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/approve", headers=H).json()["state"], "searching")
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/approve", headers=H).status_code, 409, "only once")
        gamereq.tick(NOW)
        self.assertEqual([r["app"] for r in self.q.ledger.values()], [10])
        self.assertEqual(self.listed(self.member)[0]["mine"], True)
        # a no: nothing is sent, and the person sees it
        other = self.ask(self.member, 20)
        self.assertEqual(self.admin.post(f"/api/games/requests/{other['id']}/decline", headers=H).json()["state"], "declined")
        gamereq.tick(NOW + 1)
        self.assertEqual(len(self.q.ledger), 1)
        self.assertIn(("Doom Eternal", "declined"), self.state(self.member))

    def test_asking_twice_is_one_request_and_others_see_it_asked_for(self):
        first = self.ask(self.admin)
        again = self.ask(self.member)
        self.assertEqual((again["id"], again["state"], again["by"], again["mine"]), (first["id"], "searching", None, False))
        core.db().execute("UPDATE game_requests SET why = 'between the admins', release = 'Doom-HARBOUR'")
        seen = self.member.get("/api/games/requests/game/10").json()["request"]
        self.assertEqual((seen["state"], seen["why"], seen["release"], seen["by"]), ("searching", "", None, None), "that it's asked for, and no more")
        self.assertEqual(len(self.listed(self.admin)), 1)
        self.assertEqual(self.listed(self.member), [], "your list is what you asked for")
        cards = self.member.get("/api/games/requests/search", params={"q": "doom"}).json()["items"]
        self.assertEqual([c["status"] for c in cards], ["searching", None])

    def test_without_parlors_login_in_questarr_asking_says_what_to_do(self):
        with mock.patch.object(stack, "_questarr_own", lambda: ""):
            r = self.admin.post("/api/games/requests/game/10", headers=H)
            self.assertEqual(r.status_code, 409)
            self.assertIn("Set up again", r.json()["detail"])
            gamereq.tick(NOW)
        self.assertEqual((self.listed(self.admin), self.q.logins), ([], 0))


class Picking(Base):
    def chosen(self, releases):
        self.q.releases = releases
        rid = self.ask(self.admin)["id"]
        gamereq.tick(NOW)
        gamereq.tick(NOW + 1)
        return rid

    def test_sure_is_every_check_at_once(self):
        ok = release("Doom-HARBOUR")
        self.assertTrue(gamereq.sure(ok))
        self.assertTrue(gamereq.sure(release("Doom Repack", kind="repack", known="repack")))
        self.assertTrue(gamereq.sure({**release("Doom", known=None), "trust": {"level": "trusted"}}), "listed on xREL is known enough")
        for why, bad in {
            "an update, not the game": release("Doom Update 1-HARBOUR", kind="update", part="update"),
            "an add-on": release("Doom DLC-HARBOUR", part="dlc"),
            "a title that only contains the name": release("Doom Eternal-HARBOUR", match="contains"),
            "nothing says what kind it is": {**ok, "aiReleaseType": None},
            "too few seeders": release("Doom-HARBOUR", seeders=4),
            "from nobody known": release("Doom Full Game", known=None),
            "Questarr turned it down": release("Doom-HARBOUR", accepted=False),
            "marked risky": {**ok, "trust": {"level": "risky"}},
            "on the blacklist": {**ok, "blacklisted": True},
            "not a torrent": {**ok, "downloadType": "usenet"},
            "no link Parlor could hand on": {**ok, "link": "ftp://somewhere/doom"},
            "no seeders known": {**ok, "seeders": None},
            "posted yesterday: fakes are new": {**ok, "pubDate": formatdate(time.time() - 86400)},
            "no date at all": {**ok, "pubDate": None},
            "a date that isn't one": {**ok, "pubDate": "soon"},
            "not even a result": {},
        }.items():
            self.assertFalse(gamereq.sure(bad), why)

    def test_when_none_is_sure_an_admin_picks_and_links_never_leave_the_server(self):
        rid = self.chosen([release("Doom Full Game", seeders=30, known=None, score=100), release("Doom Old Rip", seeders=2, score=100, known=None),
                           release("Doom PS3", accepted=False), release("Doom-USENET", downloadType="usenet"), {**release("Doom-RISKY"), "trust": {"level": "risky"}},
                           release("Doom Update 4-HARBOUR", seeders=90, kind="update", part="update"), release("Doom Crack Only", seeders=90, kind="crack_only")])
        self.assertEqual(self.q.grabs, [])
        row = self.listed(self.admin)[0]
        self.assertEqual((row["state"], row["label"]), ("choose", "Pick a release"))
        self.assertEqual([(p["title"], p["seeders"], p["indexer"], p["sure"]) for p in row["picks"]],
                         [("Doom Full Game", 30, "Harbour Index", False), ("Doom Old Rip", 2, "Harbour Index", False)], "what Questarr accepts that is the game itself (no update, add-on or crack alone), best first")
        core.db().execute("UPDATE game_requests SET user_id = (SELECT id FROM users WHERE name = 'gamer')")
        mine = self.listed(self.member)[0]
        self.assertEqual((mine["state"], mine["picks"]), ("choose", []), "the list is the admins'")
        for who in (self.admin, self.member):
            for r in (who.get("/api/games/requests"), who.get("/api/games/requests/game/10")):
                self.assertNotIn("apikey", r.text)
                self.assertNotIn("prowlarr", r.text.lower())
        self.assertEqual(self.member.post(f"/api/games/requests/{rid}/pick", json={"pick": 0, "title": "Doom Full Game"}, headers=H).status_code, 403)
        for bad in (2, -1, 99):
            self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/pick", json={"pick": bad, "title": "Doom Old Rip"}, headers=H).status_code, 409, bad)
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/pick", json={"pick": "http://evil.example/x.torrent", "title": "x"}, headers=H).status_code, 422)
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/pick", json={"pick": 1, "title": "Doom Full Game"}, headers=H).status_code, 409,
                         "the list changed under the page: the release named isn't the one at that place")
        self.assertEqual(self.q.grabs, [])
        got = self.admin.post(f"/api/games/requests/{rid}/pick", json={"pick": 1, "title": "Doom Old Rip"}, headers=H).json()
        self.assertEqual((got["state"], got["release"]), ("downloading", "Doom Old Rip"))
        self.assertEqual([g["url"] for g in self.q.grabs], [LINK + "Doom.Old.Rip"])
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/pick", json={"pick": 0, "title": "Doom Full Game"}, headers=H).status_code, 409, "one release per request")

    def test_with_taking_unseen_switched_off_every_request_waits_for_a_pick(self):
        self.assertEqual(self.member.put("/api/games/requests/auto", json={"on": False}, headers=H).status_code, 403)
        self.assertEqual(self.admin.put("/api/games/requests/auto", json={"on": False}, headers=H).json(), {"auto": False})
        self.assertEqual(self.admin.get("/api/games/requests/status").json(), {"ready": True, "auto": False})
        self.chosen([release("Doom-HARBOUR")])
        self.assertEqual((self.q.grabs, self.state()), ([], [("Doom", "choose")]))
        self.assertTrue(self.listed(self.admin)[0]["picks"][0]["sure"], "the one it would have taken is marked")

    def test_nothing_found_keeps_looking_later(self):
        self.chosen([release("Doom PS3", accepted=False)])
        row = self.listed(self.admin)[0]
        self.assertEqual(row["state"], "searching")
        self.assertIn("looking again", row["why"])
        gamereq.tick(NOW + 60)
        self.assertEqual(len(self.q.searches), 1, "not before its time")
        self.q.releases = [release("Doom-HARBOUR")]
        gamereq.tick(NOW + gamereq.LOOK_AGAIN + 2)
        self.assertEqual(self.state(), [("Doom", "downloading")])

    def test_search_again_cancel_and_ask_anew(self):
        rid = self.chosen([release("Doom Full Game", known=None)])
        self.q.releases = [release("Doom-HARBOUR")]
        self.assertEqual(self.member.post(f"/api/games/requests/{rid}/retry", headers=H).status_code, 404, "not theirs")
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/retry", headers=H).json()["state"], "searching")
        gamereq.tick(NOW + 5)
        self.assertEqual(self.state(), [("Doom", "downloading")])
        first, = self.q.ledger
        self.assertEqual(self.member.delete(f"/api/games/requests/{rid}", headers=H).status_code, 404)
        self.assertEqual(self.admin.delete(f"/api/games/requests/{rid}", headers=H).status_code, 200)
        self.assertEqual((self.listed(self.admin), self.q.ledger[first]["status"]), ([], "cancelled"))
        self.ask(self.admin)
        gamereq.tick(NOW + 10)
        self.assertEqual(sorted(r["status"] for r in self.q.ledger.values()), ["cancelled", "searching"], "a new request in Questarr, not the cancelled one again")

    def test_a_failed_download_says_why_and_can_be_tried_again(self):
        rid = self.chosen([release("Doom-HARBOUR")])
        ext, = self.q.ledger
        self.q.ledger[ext].update(status="failed", error=f"Couldn't fetch {LINK}Doom-HARBOUR (or magnet:?xt=urn:btih:abc): it was removed.")
        gamereq.tick(NOW + 400)
        row = self.listed(self.admin)[0]
        self.assertEqual((row["state"], row["why"]), ("failed", "Couldn't fetch (a link) (or (a link) it was removed."), "Questarr's words, without the links in them")
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/retry", headers=H).json()["state"], "searching")
        gamereq.tick(NOW + 500)
        gamereq.tick(NOW + 501)
        self.assertEqual((self.state(), len(self.q.grabs), self.q.ledger[ext]["status"]), ([("Doom", "downloading")], 2, "cancelled"))


class Trouble(Base):
    def test_questarr_down_is_tried_again_and_a_stale_session_signs_in_again(self):
        self.ask(self.admin)
        self.q.down = True
        gamereq.tick(NOW)
        row = self.listed(self.admin)[0]
        self.assertEqual(row["state"], "searching")
        self.assertIn("trying again", row["why"])
        self.q.down = False
        gamereq.tick(NOW + 1)
        self.assertEqual(self.q.ledger, {}, "after a wait, not at once")
        gamereq.tick(NOW + 3 * gamereq.EVERY)
        self.assertEqual(len(self.q.ledger), 1)
        self.q.token = "jwt-2"  # Questarr restarted, or the week was up
        self.q.releases = [release("Doom-HARBOUR")]
        gamereq.tick(NOW + 4 * gamereq.EVERY)
        self.assertEqual((self.state(), self.q.logins), ([("Doom", "downloading")], 2))

    def test_a_game_questarr_already_has_a_request_for_says_so(self):
        self.q.ledger["someone-elses"] = {"app": 10, "title": "Doom", "status": "searching", "game": "game-10"}
        self.ask(self.admin)
        gamereq.tick(NOW)
        row = self.listed(self.admin)[0]
        self.assertEqual(row["state"], "failed")
        self.assertIn("already linked", row["why"])

    def test_a_request_removed_in_questarr_is_stuck_not_lost(self):
        self.ask(self.admin)
        gamereq.tick(NOW)
        self.q.ledger.clear()
        gamereq.tick(NOW + 1)
        self.assertEqual(self.state(), [("Doom", "failed")])


if __name__ == "__main__":
    unittest.main()


class Careful(Base):
    """What a review found: the worker and the page stepping on each other, and people leaning on the buttons."""

    def test_a_member_cant_lean_on_search_again(self):
        rid = self.ask(self.member)["id"]
        self.admin.post(f"/api/games/requests/{rid}/approve", headers=H)
        r = self.member.post(f"/api/games/requests/{rid}/retry", headers=H)
        self.assertEqual(r.status_code, 429, "each search asks every indexer: not again within minutes")
        self.assertEqual(self.admin.post(f"/api/games/requests/{rid}/retry", headers=H).status_code, 200, "an admin may")
        core.db().execute("UPDATE game_requests SET updated = updated - ?", (gamereq.EVERY + 1,))
        self.assertEqual(self.member.post(f"/api/games/requests/{rid}/retry", headers=H).status_code, 200)

    def test_a_game_that_isnt_out_is_never_taken_unseen(self):
        self.q.releases = [release("Soon Quest-HARBOUR")]
        self.ask(self.admin, 50)
        gamereq.tick(NOW)
        gamereq.tick(NOW + 1)
        row = self.listed(self.admin)[0]
        self.assertEqual((row["state"], self.q.grabs), ("choose", []), "a release of a game nobody has released is a fake until an admin says otherwise")
        self.assertIn("isn't out yet", row["why"])

    def test_cancelled_while_questarr_was_searching_nothing_is_downloaded(self):
        self.q.releases = [release("Doom-HARBOUR")]
        rid = self.ask(self.admin)["id"]
        gamereq.tick(NOW)
        self.q.during_search = lambda: self.admin.delete(f"/api/games/requests/{rid}", headers=H)
        gamereq.tick(NOW + 1)
        self.assertEqual((self.q.grabs, self.listed(self.admin)), ([], []))

    def test_a_request_questarr_hasnt_made_its_game_for_yet_is_waited_for_not_sent_twice(self):
        self.q.releases, self.q.slow = [release("Doom-HARBOUR")], True
        self.ask(self.admin)
        gamereq.tick(NOW)
        gamereq.tick(NOW + 40)
        self.assertEqual((len(self.q.ledger), self.state(), self.q.searches), (1, [("Doom", "searching")], []))
        self.q.slow = False
        gamereq.tick(NOW + 80)
        self.assertEqual((len(self.q.ledger), self.state()), (1, [("Doom", "downloading")]))

    def test_cancel_with_questarr_down_keeps_the_request(self):
        rid = self.ask(self.admin)["id"]
        gamereq.tick(NOW)
        self.q.down = True
        self.assertEqual(self.admin.delete(f"/api/games/requests/{rid}", headers=H).status_code, 502)
        self.assertEqual(self.state(), [("Doom", "searching")], "still listed: it's still in Questarr")
        self.q.down = False
        self.assertEqual(self.admin.delete(f"/api/games/requests/{rid}", headers=H).status_code, 200)
        self.assertEqual([r["status"] for r in self.q.ledger.values()], ["cancelled"])

    def test_an_error_that_passed_leaves_no_words_behind(self):
        self.q.releases = [release("Doom-HARBOUR")]
        self.ask(self.admin)
        gamereq.tick(NOW)
        gamereq.tick(NOW + 1)
        self.q.down = True
        gamereq.tick(NOW + 400)
        self.assertIn("trying again", self.listed(self.admin)[0]["why"])
        self.q.down = False
        gamereq.tick(NOW + 400 + 3 * gamereq.EVERY)
        row = self.listed(self.admin)[0]
        self.assertEqual((row["state"], row["why"]), ("downloading", ""))
