"""Whole sets through Seerr (bulk.py), against a Seerr stand-in shaped like the real one: a collection, a studio, a
network and a keyword list with Seerr's status and the filters; a plan names only what's missing, at most 250, and
only for someone with a Seerr user; confirming works once, for the person the plan was made for; the job requests as
that person, looks at each title again first, survives a restart, and stops when the person loses Movies & TV, Seerr's
quota runs out or Seerr stops answering; Retry carries on; an API key with full scope can do it too.

Run from app/: ../.venv/bin/python -m unittest discover -s ../tests -p 'test_collections*.py'"""
import json
import unittest
import unittest.mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import bulk
import core
import seerr
from seerr_standin import Seerr
from yeerr_test_env import H, client, signed_in


class Sets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = signed_in("admin")
        cls.me = signed_in("member")
        cls.other = signed_in("other")
        bulk.PACE = 0
        bulk._pending = type(bulk._pending)()  # a worker another test's app started keeps waiting on the old queue; these tests run jobs themselves

    def setUp(self):
        core.set_setting("seerr_url", "http://seerr.test")
        core.set_setting("seerr_key", "key")
        core.db().execute("UPDATE users SET seerr_id = NULL, blocked = ''")
        core.db().execute("UPDATE users SET seerr_id = 7 WHERE name = 'member'")
        core.db().execute("UPDATE users SET seerr_id = 8 WHERE name = 'other'")
        core.db().execute("DELETE FROM bulk_jobs")
        for c in (bulk._pages, bulk._names, bulk._home, bulk._plans, seerr._details, seerr._seerr_users):
            with c.lock:
                c.data.clear()
        while not bulk._pending.empty():
            bulk._pending.get_nowait()
        self.fake = Seerr()
        patch = self.fake.use()
        patch.start()
        self.addCleanup(patch.stop)

    def plan(self, who=None, **body):
        r = (who or self.me).post("/api/seerr/sets/plan", json=body, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def go(self, plan, who=None):
        """confirm a plan and run its job here, as the worker would"""
        r = (who or self.me).post("/api/seerr/sets/request", json={"plan": plan["plan"]}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        bulk.run(r.json()["id"])
        return next(j for j in (who or self.me).get("/api/seerr/sets/jobs").json()["jobs"] if j["id"] == r.json()["id"])

    def test_a_collection_lists_with_status_and_filters(self):
        self.fake.status[("movie", 1001)] = 5
        self.fake.status[("movie", 1002)] = 2
        got = self.me.get("/api/seerr/sets/collection/100").json()
        self.assertEqual((got["name"], got["total"], got["media"]), ("The Lantern Saga", 4, "movie"))
        self.assertEqual([(i["title"], i["status"]) for i in got["items"]],
                         [("Lantern 1", "available"), ("Lantern 2", "pending"), ("Lantern 3", "unknown"), ("Lantern 4", "unknown")])
        self.assertEqual(self.me.get("/api/seerr/sets/collection/100?released=false").json()["total"], 5, "with what isn't out yet")
        narrowed = self.me.get("/api/seerr/sets/collection/100?year_from=2015&year_to=2018&rating=8").json()
        self.assertEqual([i["title"] for i in narrowed["items"]], ["Lantern 3"])

    def test_a_studio_pages_and_seerr_does_the_filtering(self):
        got = self.me.get("/api/seerr/sets/studio/7").json()
        self.assertEqual((got["name"], got["total"], got["pages"], len(got["items"])), ("Harbour Pictures", 45, 3, 20))
        self.assertEqual(len(self.me.get("/api/seerr/sets/studio/7?page=3").json()["items"]), 5)
        got = self.me.get("/api/seerr/sets/studio/7?year_from=2000&year_to=2009&rating=7.5").json()
        asked = next(c["params"] for c in reversed(self.fake.calls) if c["path"] == "discover/movies")
        self.assertEqual((asked["studio"], asked["primaryReleaseDateGte"], asked["primaryReleaseDateLte"], asked["voteAverageGte"]), (7, "2000-01-01", "2009-12-31", 7.5))
        self.assertEqual([i["year"] for i in got["items"]], [str(y) for y in range(2005, 2010)])

    def test_a_network_is_shows_and_a_keyword_is_either(self):
        got = self.me.get("/api/seerr/sets/network/9").json()
        self.assertEqual((got["media"], got["total"], got["items"][0]["type"]), ("tv", 30, "tv"))
        self.assertEqual([i["title"] for i in self.me.get("/api/seerr/sets/keyword/55").json()["items"]], ["Clockwork film 1", "Clockwork film 2", "Clockwork film 3"])
        self.assertEqual([i["type"] for i in self.me.get("/api/seerr/sets/keyword/55?media=tv").json()["items"]], ["tv", "tv"])
        self.assertEqual(self.me.get("/api/seerr/sets/album/1").status_code, 404)
        self.assertEqual(self.me.get("/api/seerr/sets/keyword/55?media=book").status_code, 422)

    def test_search_finds_studios_and_keywords(self):
        got = self.me.get("/api/seerr/sets/search?q=harbour").json()
        self.assertEqual(got["studios"], [{"kind": "studio", "id": 7, "name": "Harbour Pictures"}])
        self.assertEqual(got["keywords"][0]["id"], 55)

    def test_a_plan_is_only_what_is_missing(self):
        self.fake.status.update({("movie", 1001): 5, ("movie", 1002): 2, ("movie", 1003): 4})
        p = self.plan(kind="collection", id=100)
        self.assertEqual((p["count"], [t["id"] for t in p["titles"]], p["left_out"]), (1, [1004], {"there": 1, "requested": 1, "partly": 1}))
        self.assertFalse(p["more"])
        self.assertEqual([c for c in self.fake.calls if c["method"] == "POST"], [], "a plan requests nothing")

    def test_a_plan_stops_at_the_cap_and_says_there_is_more(self):
        p = self.plan(kind="studio", id=8)
        self.assertEqual((p["count"], p["looked_at"], p["total"], p["more"], p["cap"]), (250, 260, 300, True, 250))

    def test_only_someone_with_a_seerr_user_plans_and_only_with_movies_and_tv(self):
        core.db().execute("UPDATE users SET seerr_id = NULL WHERE name = 'other'")
        self.fake.users = []
        r = self.other.post("/api/seerr/sets/plan", json={"kind": "collection", "id": 100}, headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertIn("isn't linked", r.json()["detail"])
        core.db().execute("UPDATE users SET blocked = 'media' WHERE name = 'member'")
        self.assertEqual(self.me.get("/api/seerr/sets/collection/100").status_code, 403)
        self.assertEqual(self.me.post("/api/seerr/sets/plan", json={"kind": "collection", "id": 100}, headers=H).status_code, 403)
        self.assertEqual(client().get("/api/seerr/sets/collection/100").status_code, 401)

    def test_confirming_requests_the_missing_ones_as_that_person(self):
        self.fake.status[("movie", 1001)] = 5
        p = self.plan(kind="collection", id=100)
        self.fake.status[("movie", 1002)] = 5  # arrived between looking and confirming
        self.fake.requests[("movie", 1003)] = [3]  # an admin declined it before
        job = self.go(p)
        self.assertEqual([(x["mediaId"], x["user"]) for x in self.fake.posted], [(1004, "7")])
        self.assertEqual((job["status"], job["requested"], job["skipped"], job["failed"], job["done"], job["total"]), ("done", 1, 2, 0, 3, 3))
        self.assertEqual({t["id"]: t["why"] for t in job["titles"] if t["state"] == "skipped"},
                         {1002: "already in the library", 1003: "declined before: ask an admin"})

    def test_shows_are_requested_with_every_season(self):
        self.go(self.plan(kind="keyword", id=55, media="tv"))
        self.assertEqual([(x["mediaType"], x["seasons"]) for x in self.fake.posted], [("tv", [1, 2]), ("tv", [1, 2])])

    def test_a_plan_works_once_and_only_for_its_person(self):
        p = self.plan(kind="collection", id=100)
        self.assertEqual(self.other.post("/api/seerr/sets/request", json={"plan": p["plan"]}, headers=H).status_code, 410, "someone else's plan")
        self.assertEqual(self.me.post("/api/seerr/sets/request", json={"plan": "made-up"}, headers=H).status_code, 410)
        first = self.me.post("/api/seerr/sets/request", json={"plan": p["plan"]}, headers=H)
        self.assertEqual((first.status_code, first.json()["status"], first.json()["waiting"]), (200, "queued", 4))
        self.assertEqual(self.me.post("/api/seerr/sets/request", json={"plan": p["plan"]}, headers=H).status_code, 410, "a double press")
        again = self.plan(kind="collection", id=100)
        self.assertEqual(self.me.post("/api/seerr/sets/request", json={"plan": again["plan"]}, headers=H).status_code, 409, "the same set while it's going")
        self.assertEqual(self.other.get("/api/seerr/sets/jobs").json()["jobs"], [], "jobs are each person's own")
        self.assertEqual(self.other.delete(f"/api/seerr/sets/jobs/{first.json()['id']}", headers=H).status_code, 404)
        self.fake.status.update({("movie", i): 5 for i in range(1001, 1005)})
        nothing = self.plan(who=self.other, kind="collection", id=100)
        self.assertEqual(self.other.post("/api/seerr/sets/request", json={"plan": nothing["plan"]}, headers=H).status_code, 422, "nothing is missing")

    def test_a_restart_carries_on_with_the_next_title(self):
        r = self.me.post("/api/seerr/sets/request", json={"plan": self.plan(kind="collection", id=100)["plan"]}, headers=H).json()
        titles = json.loads(core.one("SELECT titles FROM bulk_jobs WHERE id = ?", r["id"])["titles"])
        titles[0].update(state="requested")  # done before the restart
        self.fake.status[("movie", 1001)] = 2
        core.db().execute("UPDATE bulk_jobs SET status = 'running', titles = ? WHERE id = ?", (json.dumps(titles), r["id"]))
        while not bulk._pending.empty():
            bulk._pending.get_nowait()
        with unittest.mock.patch.object(bulk.threading, "Thread"):  # start() queues what was going; the test runs it
            bulk.start()
        self.assertEqual(bulk._pending.get_nowait(), r["id"])
        bulk.run(r["id"])
        self.assertEqual([x["mediaId"] for x in self.fake.posted], [1002, 1003, 1004], "the one already requested isn't asked for again")
        self.assertEqual(self.me.get("/api/seerr/sets/jobs").json()["jobs"][0]["status"], "done")

    def test_the_person_as_they_are_now_decides(self):
        r = self.me.post("/api/seerr/sets/request", json={"plan": self.plan(kind="collection", id=100)["plan"]}, headers=H).json()
        core.db().execute("UPDATE users SET blocked = 'media' WHERE name = 'member'")  # an admin closed Movies & TV to them
        bulk.run(r["id"])
        self.assertEqual(self.fake.posted, [])
        job = core.one("SELECT * FROM bulk_jobs WHERE id = ?", r["id"])
        self.assertEqual((job["status"], job["note"]), ("stopped", "Movies & TV was closed to this account"))
        self.assertEqual({t["state"] for t in json.loads(job["titles"])}, {"left"})
        # an account that's gone takes its jobs with it
        gone = signed_in("leaver")
        core.db().execute("UPDATE users SET seerr_id = 9 WHERE name = 'leaver'")
        j = gone.post("/api/seerr/sets/request", json={"plan": self.plan(who=gone, kind="collection", id=100)["plan"]}, headers=H).json()
        core.db().execute("DELETE FROM users WHERE name = 'leaver'")
        bulk.run(j["id"])
        self.assertEqual(self.fake.posted, [])

    def test_seerrs_quota_stops_the_job_and_retry_carries_on(self):
        self.fake.quota = 2
        job = self.go(self.plan(kind="collection", id=100))
        self.assertEqual((job["status"], job["requested"], job["left"], job["failed"]), ("stopped", 2, 2, 0))
        self.assertIn("limit", job["note"])
        self.fake.quota = None
        r = self.me.post(f"/api/seerr/sets/jobs/{job['id']}/retry", headers=H)
        self.assertEqual((r.status_code, r.json()["status"], r.json()["waiting"]), (200, "queued", 2))
        bulk.run(job["id"])
        job = self.me.get("/api/seerr/sets/jobs").json()["jobs"][0]
        self.assertEqual((job["status"], job["requested"]), ("done", 4))
        self.assertEqual(self.me.post(f"/api/seerr/sets/jobs/{job['id']}/retry", headers=H).status_code, 422, "nothing failed")

    def test_seerr_not_answering_stops_after_three_and_keeps_the_rest(self):
        p = self.plan(kind="collection", id=100)
        self.fake.down = True
        job = self.go(p)
        self.assertEqual((job["status"], job["left"], job["failed"], job["requested"]), ("stopped", 4, 0, 0))
        self.assertIn("isn't answering", job["note"])

    def test_one_refusal_is_that_titles_alone(self):
        p = self.plan(kind="collection", id=100)
        self.fake.status[("movie", 1002)] = 1
        real = self.fake._title
        self.fake._title = lambda kind, tid: None if tid == 1002 else real(kind, tid)  # Seerr no longer knows it
        job = self.go(p)
        self.assertEqual((job["status"], job["requested"], job["failed"]), ("done", 3, 1))
        self.assertTrue(next(t for t in job["titles"] if t["id"] == 1002)["why"].startswith("Seerr:"))

    def test_a_refusal_that_isnt_one_titles_stops_the_job(self):
        p = self.plan(kind="collection", id=100)
        with unittest.mock.patch.object(bulk.seerr, "ask", side_effect=bulk.HTTPException(403, "Seerr: You do not have permission to make requests.")):
            job = self.go(p)
        self.assertEqual((job["status"], job["left"], job["failed"], job["note"]), ("stopped", 4, 0, "Seerr: You do not have permission to make requests."))

    def test_an_answer_seerr_never_gives_fails_that_title_alone(self):
        p = self.plan(kind="collection", id=100)
        real = bulk.seerr.ask
        with unittest.mock.patch.object(bulk.seerr, "ask", side_effect=lambda u, k, i: (_ for _ in ()).throw(ValueError("not JSON")) if i == 1002 else real(u, k, i)):
            job = self.go(p)
        self.assertEqual((job["status"], job["requested"], job["failed"]), ("done", 3, 1))

    def test_stop_then_carry_on_while_a_title_is_in_flight_loses_nothing(self):
        r = self.me.post("/api/seerr/sets/request", json={"plan": self.plan(kind="collection", id=100)["plan"]}, headers=H).json()
        titles = json.loads(core.one("SELECT titles FROM bulk_jobs WHERE id = ?", r["id"])["titles"])
        titles[0].update(state="failed", why="Seerr: earlier trouble")
        core.db().execute("UPDATE bulk_jobs SET titles = ? WHERE id = ?", (json.dumps(titles), r["id"]))
        real, pressed = bulk.seerr.ask, []

        def ask(user, kind, tid):
            if not pressed:  # while the second film is being requested, the person presses Stop, then Carry on
                pressed.append(self.me.delete(f"/api/seerr/sets/jobs/{r['id']}", headers=H).json()["stopped"])
                self.assertEqual(self.me.post(f"/api/seerr/sets/jobs/{r['id']}/retry", headers=H).status_code, 200)
            return real(user, kind, tid)

        with unittest.mock.patch.object(bulk.seerr, "ask", side_effect=ask):
            bulk.run(r["id"])
        job = self.me.get("/api/seerr/sets/jobs").json()["jobs"][0]
        self.assertEqual((job["status"], job["requested"], job["failed"]), ("done", 4, 0), "the film Carry on put back was asked for")
        self.assertEqual(sorted(x["mediaId"] for x in self.fake.posted), [1001, 1002, 1003, 1004])

    def test_the_jobs_list_is_small_and_a_repeated_title_counts_once(self):
        self.fake.keyword_films.append(dict(self.fake.keyword_films[0]))  # TMDB repeats a title
        p = self.plan(kind="keyword", id=55)
        self.assertEqual(p["count"], 3)
        self.fake.status[("movie", 4002)] = 5
        job = self.go(p)
        self.assertEqual([(t["id"], set(t)) for t in job["titles"]], [(4002, {"type", "id", "title", "state", "why"})], "only titles with something to say, without pictures")
        self.assertEqual((job["total"], job["requested"], job["skipped"]), (3, 2, 1))

    def test_stop_and_clear(self):
        r = self.me.post("/api/seerr/sets/request", json={"plan": self.plan(kind="collection", id=100)["plan"]}, headers=H).json()
        self.assertTrue(self.me.delete(f"/api/seerr/sets/jobs/{r['id']}", headers=H).json()["stopped"])
        bulk.run(r["id"])
        self.assertEqual(self.fake.posted, [], "a stopped job requests nothing more")
        self.assertEqual(self.me.get("/api/seerr/sets/jobs").json()["jobs"][0]["status"], "stopped")
        self.me.delete(f"/api/seerr/sets/jobs/{r['id']}", headers=H)
        self.assertEqual(self.me.get("/api/seerr/sets/jobs").json()["jobs"], [])

    def test_an_api_key_with_full_scope_can_request_a_set(self):
        made = {s: self.me.post("/api/me/api-keys", json={"name": f"sets-{s}", "scope": s}, headers=H).json() for s in ("full", "search")}
        try:
            full, search = client(), client()
            full.headers.update({"X-Api-Key": made["full"]["key"]})
            search.headers.update({"X-Api-Key": made["search"]["key"]})
            self.assertEqual(search.get("/api/seerr/sets/collection/100").json()["total"], 4)
            self.assertEqual(search.post("/api/seerr/sets/plan", json={"kind": "collection", "id": 100}).status_code, 403, "a search key only reads")
            p = full.post("/api/seerr/sets/plan", json={"kind": "collection", "id": 100})
            self.assertEqual(p.status_code, 200, p.text)
            j = full.post("/api/seerr/sets/request", json={"plan": p.json()["plan"]})
            self.assertEqual(j.status_code, 200, j.text)
            bulk.run(j.json()["id"])
            self.assertEqual(len(self.fake.posted), 4)
        finally:
            for k in made.values():
                self.me.delete(f"/api/me/api-keys/{k['id']}", headers=H)

    def test_a_films_page_names_its_sets(self):
        got = self.me.get("/api/seerr/movie/1001").json()
        self.assertEqual(got["collection"], {"id": 100, "name": "The Lantern Saga"})
        self.assertEqual((got["studios"], got["keywords"]), ([{"id": 7, "name": "Harbour Pictures"}], [{"id": 55, "name": "clockwork (universe)"}]))
        self.assertIsNone(self.me.get("/api/seerr/movie/2001").json()["collection"])

    def test_the_start_page_puts_your_own_collections_first(self):
        self.fake.status[("movie", 1001)] = 5
        self.fake.status[("movie", 1002)] = 5
        got = self.me.get("/api/seerr/sets").json()
        self.assertEqual([(f["id"], f["name"], f["have"]) for f in got["franchises"]], [(100, "The Lantern Saga", 2)],
                         "the well-known ones this stand-in doesn't have are left out")


if __name__ == "__main__":
    unittest.main()
