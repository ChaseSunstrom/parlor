"""Adding a remote to a TV that is already on: the TV shows a link code (and a square that opens it on a phone); any
signed-in account that enters it gets this one TV: it lists it, drives it, is one of its people in Who's watching
(their PIN asked as for anyone, never acting as an admin), and can take itself off; the owner can take anyone off.
Codes come only from a TV's own session, last five minutes, work once, and wrong ones are throttled like pairing codes."""
import unittest

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import tv
from yeerr_test_env import H, client, signed_in


def paired(owner, name="Den"):
    t = client()
    got = t.post("/api/tv/code", headers=H).json()
    assert owner.post("/api/tv/pair", json={"code": got["code"], "name": name}, headers=H).status_code == 200
    assert t.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H).json()["paired"]
    return t, t.get("/api/tv/me").json()["tv"]["id"]


class Links(unittest.TestCase):
    def setUp(self):
        tv._tries.clear()
        auth._failures.clear()
        for table in ("tvs", "tv_codes", "tv_links", "tv_remotes"):
            core.db().execute(f"DELETE FROM {table}")
        self.owner = signed_in("lowner")
        self.pal = signed_in("lpal")  # not in any household: links by the TV's code
        self.other = signed_in("lother")
        core.db().execute("UPDATE users SET household = 0, pin = NULL, admin = 0 WHERE name IN ('lowner', 'lpal', 'lother')")
        self.uid = {r["name"]: r["id"] for r in core.rows("SELECT id, name FROM users")}
        self.tv, self.tid = paired(self.owner)

    def code(self, t=None):
        r = (t or self.tv).post("/api/tv/remote-code", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def link(self, who, code):
        return who.post("/api/tv/link", json={"code": code}, headers=H)

    def test_a_linked_account_gets_this_tv_and_no_other(self):
        _, other_tid = paired(self.owner, "Bedroom")
        got = self.code()
        self.assertEqual(len(got["code"]), 8)
        self.assertIn("/#/tvs/link/", got["url"])
        self.assertEqual(self.pal.get("/api/tv").json()["tvs"], [], "nothing before")
        r = self.link(self.pal, got["code"].lower().replace("", " ").strip())  # typed loosely: case and spaces don't matter
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tv"], {"id": self.tid, "name": "Den"})
        listed = self.pal.get("/api/tv").json()["tvs"]
        self.assertEqual([(t["id"], t["linked"]) for t in listed], [(self.tid, True)], "this TV, not the owner's other one")
        self.assertIn(self.uid["lpal"], tv.audience(self.tid))
        self.assertNotIn(self.uid["lpal"], tv.audience(other_tid))
        self.assertEqual(self.pal.post(f"/api/tv/{self.tid}/do", json={"cmd": "toggle"}, headers=H).status_code, 409, "allowed: just not listening")
        self.assertEqual(self.pal.post(f"/api/tv/{other_tid}/do", json={"cmd": "toggle"}, headers=H).status_code, 404)
        self.assertEqual(self.other.get(f"/api/tv/{self.tid}/people").status_code, 404, "a third account still can't")
        self.assertFalse(next(t for t in self.owner.get("/api/tv").json()["tvs"] if t["id"] == self.tid)["linked"], "the owner's is paired, not linked")

    def test_a_tv_signed_in_with_a_password_becomes_a_tv(self):
        screen = signed_in("lpal")  # the Android app on a TV, signed in with a password: a plain session
        self.assertIsNone(screen.get("/api/tv/me").json()["tv"])
        core.db().execute("UPDATE users SET admin = 1 WHERE name = 'lpal'")
        self.assertTrue(screen.get("/api/me").json()["admin"])
        r = screen.post("/api/tv/adopt", json={"name": "Lounge"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        mine = r.json()["tv"]
        self.assertEqual((mine["name"], mine["user_id"]), ("Lounge", self.uid["lpal"]))
        self.assertEqual(screen.get("/api/tv/me").json()["tv"]["id"], mine["id"])
        self.assertFalse(screen.get("/api/me").json()["admin"], "now a TV: never an admin")
        self.assertEqual(screen.post("/api/tv/adopt", json={"name": "Again"}, headers=H).json()["tv"]["id"], mine["id"], "once")
        self.assertEqual([t["id"] for t in self.pal.get("/api/tv").json()["tvs"]], [mine["id"]], "the account's phone lists it")
        self.assertEqual(self.tv.post("/api/tv/adopt", json={}, headers=H).json()["tv"]["id"], self.tid, "a paired TV stays itself")
        self.assertEqual(client().post("/api/tv/adopt", json={}, headers=H).status_code, 401)
        core.db().execute("UPDATE users SET admin = 0 WHERE name = 'lpal'")

    def test_a_peek_names_the_tv_and_its_owner_and_keeps_the_code(self):
        got = self.code()  # a phone asks first, so its person can say yes to this TV by name before it may act as them
        r = self.pal.post("/api/tv/link", json={"code": got["code"], "peek": True}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["tv"], {"id": self.tid, "name": "Den", "owner": "lowner"})
        self.assertEqual(self.pal.get("/api/tv").json()["tvs"], [], "a peek adds nothing")
        self.assertEqual(self.link(self.pal, got["code"]).status_code, 200, "and leaves the code to use")
        self.assertEqual(self.pal.post("/api/tv/link", json={"code": "ZZZZZZZZ", "peek": True}, headers=H).status_code, 404)

    def test_a_code_works_once_and_runs_out(self):
        got = self.code()
        self.assertEqual(self.link(self.pal, got["code"]).status_code, 200)
        self.assertEqual(self.link(self.other, got["code"]).status_code, 404, "once only")
        got = self.code()
        self.assertNotEqual(self.code()["code"], got["code"])
        self.assertEqual(self.link(self.other, got["code"]).status_code, 404, "a newer code replaces the TV's last one")
        got = self.code()
        old = tv._now
        try:
            tv._now = lambda: old() + tv.CODE_LIFE + 1
            self.assertEqual(self.link(self.other, got["code"]).status_code, 404, "run out")
        finally:
            tv._now = old

    def test_only_a_tv_makes_a_code_and_a_tv_never_links(self):
        self.assertEqual(self.owner.post("/api/tv/remote-code", headers=H).status_code, 403, "a phone isn't a TV")
        self.assertEqual(client().post("/api/tv/remote-code", headers=H).status_code, 401)
        bedroom, _ = paired(self.owner, "Bedroom")
        r = self.link(bedroom, self.code()["code"])
        self.assertEqual(r.status_code, 403)
        self.assertIn("Not from a TV", r.json()["detail"])
        self.assertEqual(client().post("/api/tv/link", json={"code": "ABCDEFGH"}, headers=H).status_code, 401)

    def test_wrong_codes_are_throttled(self):
        tv._tries.clear()  # pairing in setUp counted as one try
        for _ in range(tv.LIMITS["pair"]):
            self.assertEqual(self.link(self.pal, "ZZZZZZZZ").status_code, 404)
        self.assertEqual(self.link(self.pal, self.code()["code"]).status_code, 429, "even a right one waits")

    def test_a_linked_person_is_one_of_the_tv_s_people(self):
        self.link(self.pal, self.code()["code"])
        names = [p["name"] for p in self.tv.get(f"/api/tv/{self.tid}/people").json()["people"]]
        self.assertEqual(sorted(names), ["lowner", "lpal"])
        r = self.tv.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lpal"]}, headers=H)
        self.assertEqual(r.status_code, 403, "whoever holds the TV can't make it act as a guest: only the guest, or their PIN")
        self.assertIn("own phone", r.json()["detail"])
        self.assertEqual(self.pal.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lpal"]}, headers=H).status_code, 200, "from their own phone")
        self.assertEqual(self.tv.get("/api/me").json()["name"], "lpal", "the TV acts as them")
        self.assertEqual(self.tv.get("/api/tv/me").json()["tv"]["viewer"]["name"], "lpal")
        core.db().execute("UPDATE users SET admin = 1 WHERE name = 'lpal'")
        self.assertFalse(self.tv.get("/api/me").json()["admin"], "never as an admin")
        core.db().execute("UPDATE users SET admin = 0 WHERE name = 'lpal'")
        self.pal.put("/api/me/pin", json={"pin": "2468", "password": "correct horse"}, headers=H)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "lowner", "a new PIN takes the TV back, as for anyone")
        self.assertEqual(self.tv.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lpal"]}, headers=H).status_code, 403, "the TV asks for it")
        self.assertEqual(self.tv.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lpal"], "pin": "2468"}, headers=H).status_code, 200)

    def test_a_guest_sees_and_picks_only_themselves_and_the_owner(self):
        core.db().execute("UPDATE users SET household = 1 WHERE name IN ('lowner', 'lother')")
        self.link(self.pal, self.code()["code"])
        names = [p["name"] for p in self.pal.get(f"/api/tv/{self.tid}/people").json()["people"]]
        self.assertEqual(sorted(names), ["lowner", "lpal"], "not the rest of the household")
        names = [p["name"] for p in self.owner.get(f"/api/tv/{self.tid}/people").json()["people"]]
        self.assertEqual(sorted(names), ["lother", "lowner", "lpal"], "the owner sees everyone")
        self.assertEqual(self.pal.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lother"]}, headers=H).status_code, 403, "a household account without a PIN is still not a guest's to pick")
        self.assertEqual(self.pal.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lpal"]}, headers=H).status_code, 200)
        self.assertEqual(self.pal.post(f"/api/tv/{self.tid}/viewer", json={"user_id": None}, headers=H).status_code, 200, "back to the owner")
        self.assertEqual(self.tv.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lother"]}, headers=H).status_code, 200, "the household as before")

    def test_the_owner_sees_who_was_added_and_takes_them_off(self):
        self.link(self.pal, self.code()["code"])
        mine = next(t for t in self.owner.get("/api/tv").json()["tvs"] if t["id"] == self.tid)
        self.assertEqual(mine["remotes"], [{"id": self.uid["lpal"], "name": "lpal"}])
        self.assertNotIn("remotes", self.pal.get("/api/tv").json()["tvs"][0], "a guest doesn't list the TV's other guests")
        self.pal.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lpal"]}, headers=H)
        q = tv.asyncio.Queue()
        tv.hub.tvs[self.tid].add(q)
        try:
            self.assertEqual(self.owner.delete(f"/api/tv/{self.tid}/remotes/{self.uid['lpal']}", headers=H).status_code, 200)
            sent = [q.get_nowait() for _ in range(q.qsize())]
        finally:
            tv.hub.tvs[self.tid].discard(q)
        self.assertIn({"type": "viewer", "tv": self.tid, "viewer": None}, sent, "the TV stops showing the guest's rows")
        self.assertEqual(self.tv.get("/api/me").json()["name"], "lowner")

    def test_taking_a_remote_off(self):
        self.link(self.pal, self.code()["code"])
        self.link(self.other, self.code()["code"])
        self.pal.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["lpal"]}, headers=H)
        self.assertEqual(self.other.delete(f"/api/tv/{self.tid}/remotes/{self.uid['lpal']}", headers=H).status_code, 404, "not someone else's")
        self.assertEqual(self.pal.delete(f"/api/tv/{self.tid}", headers=H).status_code, 404, "unpairing stays the owner's")
        self.assertEqual(self.pal.delete(f"/api/tv/{self.tid}/remotes/{self.uid['lpal']}", headers=H).status_code, 200, "themselves")
        self.assertEqual(self.pal.get("/api/tv").json()["tvs"], [])
        self.assertNotIn(self.uid["lpal"], tv.audience(self.tid))
        self.assertEqual(self.tv.get("/api/me").json()["name"], "lowner", "the TV stops acting as them")
        self.assertEqual(self.owner.delete(f"/api/tv/{self.tid}/remotes/{self.uid['lother']}", headers=H).status_code, 200, "the owner takes anyone off")
        self.assertEqual(self.other.get("/api/tv").json()["tvs"], [])
        self.link(self.pal, self.code()["code"])
        self.owner.delete(f"/api/tv/{self.tid}", headers=H)
        self.assertEqual(core.one("SELECT COUNT(*) AS n FROM tv_remotes")["n"], 0, "unpairing the TV ends its links")


if __name__ == "__main__":
    unittest.main()
