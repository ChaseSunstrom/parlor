"""A TV acts as the person picked: an admin marks accounts as the household; the owner, the current viewer or a household
account picks who a TV acts as (with the person's PIN, when they set one); from then on the TV's session answers as that
person, never as an admin, and falls back to its owner when the person is deleted or leaves the household. Only the owner,
the viewer and household accounts pick or drive; wrong PINs are throttled."""
import unittest

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import tv
from yeerr_test_env import H, client, signed_in

PW = "correct horse"


def paired(owner, name="Den"):
    t = client()
    got = t.post("/api/tv/code", headers=H).json()
    assert owner.post("/api/tv/pair", json={"code": got["code"], "name": name}, headers=H).status_code == 200
    assert t.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H).json()["paired"]
    return t, t.get("/api/tv/me").json()["tv"]["id"]


class Profiles(unittest.TestCase):
    def setUp(self):
        tv._tries.clear()
        auth._failures.clear()
        for table in ("tvs", "tv_codes"):
            core.db().execute(f"DELETE FROM {table}")
        self.admin = signed_in("admin")
        self.owner = signed_in("pownerr")
        self.kid = signed_in("pkid")
        self.mum = signed_in("pmum")
        self.guest = signed_in("pguest")  # not in the household
        for name in ("pkid", "pmum", "pownerr"):
            core.db().execute("UPDATE users SET household = 1, pin = NULL, blocked = '' WHERE name = ?", (name,))
        core.db().execute("UPDATE users SET household = 0, pin = NULL WHERE name = 'pguest'")
        self.uid = {r["name"]: r["id"] for r in core.rows("SELECT id, name FROM users")}
        self.tv, self.tid = paired(self.owner)

    def pick(self, who, name=None, pin=None, tid=None):
        body = {"user_id": self.uid[name] if name else None, **({"pin": pin} if pin is not None else {})}
        return who.post(f"/api/tv/{tid or self.tid}/viewer", json=body, headers=H)

    def set_pin(self, who, pin, password=PW):
        return who.put("/api/me/pin", json={"pin": pin, "password": password}, headers=H)

    def test_a_tv_answers_as_the_person_picked_and_never_as_an_admin(self):
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr", "no viewer yet: its owner")
        self.assertEqual(self.pick(self.owner, "pkid").status_code, 200)
        me = self.tv.get("/api/me").json()
        self.assertEqual((me["name"], me["id"]), ("pkid", self.uid["pkid"]))
        self.assertEqual(self.tv.get("/api/tv/me").json()["tv"]["viewer"], {"id": self.uid["pkid"], "name": "pkid"})
        core.db().execute("UPDATE users SET admin = 1 WHERE name = 'pkid'")  # even an admin account is only a viewer
        self.assertFalse(self.tv.get("/api/me").json()["admin"])
        self.assertEqual(self.tv.get("/api/users").status_code, 403)
        core.db().execute("UPDATE users SET admin = 0 WHERE name = 'pkid'")
        core.db().execute("UPDATE users SET blocked = 'games' WHERE name = 'pkid'")
        self.assertFalse(self.tv.get("/api/me").json()["sections"]["games"], "the person's own limits apply")
        self.assertEqual(self.owner.get("/api/me").json()["name"], "pownerr", "the owner's own phone is untouched")

    def test_the_owner_comes_back_when_the_viewer_goes(self):
        self.pick(self.owner, "pkid")
        core.db().execute("UPDATE users SET household = 0 WHERE name = 'pkid'")
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr", "left the household")
        self.assertEqual(self.tv.get("/api/tv/me").json()["tv"]["viewer"], None)
        core.db().execute("UPDATE users SET household = 1 WHERE name = 'pkid'")
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pkid", "back in: the TV still has them picked")
        self.assertEqual(self.admin.delete(f"/api/users/{self.uid['pkid']}", headers=H).status_code, 200)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr", "deleted")
        self.assertIsNone(core.one("SELECT viewer_id FROM tvs WHERE id = ?", self.tid)["viewer_id"])

    def test_picking_the_owner_or_nobody_goes_back(self):
        self.pick(self.owner, "pkid")
        self.assertEqual(self.pick(self.owner, None).status_code, 200)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr")
        self.pick(self.owner, "pmum")
        self.assertEqual(self.pick(self.owner, "pownerr").status_code, 200)
        self.assertIsNone(core.one("SELECT viewer_id FROM tvs WHERE id = ?", self.tid)["viewer_id"])

    def test_only_household_accounts_can_be_picked(self):
        self.assertEqual(self.pick(self.owner, "pguest").status_code, 404)
        self.assertEqual(self.pick(self.owner, "admin").status_code, 404, "an admin who isn't in the household too")
        self.assertEqual(self.owner.post(f"/api/tv/{self.tid}/viewer", json={"user_id": 99999}, headers=H).status_code, 404)

    def test_who_may_pick_and_drive(self):
        people = self.owner.get(f"/api/tv/{self.tid}/people").json()
        self.assertEqual(sorted(p["name"] for p in people["people"]), ["pkid", "pmum", "pownerr"])
        self.assertTrue(next(p for p in people["people"] if p["name"] == "pownerr")["owner"])
        self.assertIsNone(people["viewer"])
        self.assertEqual(self.tv.get(f"/api/tv/{self.tid}/people").status_code, 200, "the TV shows its own picker")
        for who in (self.guest, self.admin):  # a stranger, even an admin outside Settings → TVs
            self.assertEqual(who.get(f"/api/tv/{self.tid}/people").status_code, 404)
            self.assertEqual(self.pick(who, "pkid").status_code, 404)
            self.assertEqual(who.post(f"/api/tv/{self.tid}/do", json={"cmd": "toggle"}, headers=H).status_code, 404)
            self.assertEqual(who.get("/api/tv").json()["tvs"], [])
        # a household account sees and drives it ("off" here is the right answer: it is allowed, just not listening)
        self.assertEqual([t["id"] for t in self.mum.get("/api/tv").json()["tvs"]], [self.tid])
        self.assertEqual(self.mum.post(f"/api/tv/{self.tid}/do", json={"cmd": "toggle"}, headers=H).status_code, 409)
        self.assertEqual(self.pick(self.mum, "pkid").status_code, 200)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pkid")
        # the viewer can pick again and drive, even if the household flag was taken off meanwhile
        core.db().execute("UPDATE users SET household = 0 WHERE name = 'pkid'")
        self.assertEqual(self.pick(self.kid, "pmum").status_code, 404, "left the household: no longer the viewer")
        core.db().execute("UPDATE users SET household = 1 WHERE name = 'pkid'")
        self.assertEqual(self.pick(self.kid, "pmum").status_code, 200)
        self.assertEqual(self.pick(self.kid, "pkid").status_code, 200)
        self.assertEqual(self.kid.get("/api/tv").json()["tvs"][0]["viewer"], {"id": self.uid["pkid"], "name": "pkid"})
        # unpairing stays the owner's
        self.assertEqual(self.mum.delete(f"/api/tv/{self.tid}", headers=H).status_code, 404)
        self.assertEqual(self.owner.delete(f"/api/tv/{self.tid}", headers=H).status_code, 200)

    def test_the_audience_is_the_owner_the_viewer_and_the_household(self):
        self.assertEqual(tv.audience(self.tid), {self.uid["pownerr"], self.uid["pkid"], self.uid["pmum"]})
        self.assertNotIn(self.uid["pguest"], tv.audience(self.tid))

    def test_a_pin_is_checked(self):
        self.assertEqual(self.set_pin(self.kid, "1234", "wrong password").status_code, 403, "setting one needs the password")
        for bad in ("12", "abcd", "123456789", ""):
            self.assertEqual(self.set_pin(self.kid, bad).status_code, 422, bad)
        self.assertEqual(self.set_pin(self.kid, "1234").status_code, 200)
        self.assertNotIn("1234", core.one("SELECT pin FROM users WHERE name = 'pkid'")["pin"], "stored hashed")
        self.assertTrue(next(p for p in self.owner.get(f"/api/tv/{self.tid}/people").json()["people"] if p["name"] == "pkid")["pin"])
        self.assertEqual(self.pick(self.owner, "pkid").status_code, 403, "no PIN given")
        self.assertEqual(self.pick(self.owner, "pkid", pin="4321").status_code, 403)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr", "not switched")
        self.assertEqual(self.pick(self.owner, "pkid", pin="1234").status_code, 200)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pkid")
        self.assertEqual(self.pick(self.tv, "pkid").status_code, 200, "already them: nothing to prove")
        self.assertEqual(self.pick(self.owner, None).status_code, 200)
        self.assertEqual(self.pick(self.tv, "pkid").status_code, 403, "the TV itself has to type it")
        self.assertEqual(self.pick(self.kid, "pkid").status_code, 200, "their own phone is them: no PIN to be themselves")
        self.pick(self.owner, None)
        self.assertEqual(self.set_pin(self.kid, None).status_code, 200, "a null PIN clears it")
        self.assertEqual(self.pick(self.tv, "pkid").status_code, 200)

    def test_the_owner_s_pin_guards_the_way_back(self):
        self.assertEqual(self.set_pin(self.owner, "5555").status_code, 200)
        self.pick(self.mum, "pkid")
        self.assertEqual(self.pick(self.tv, None).status_code, 403, "the kid's TV can't go back to the owner without the PIN")
        self.assertEqual(self.pick(self.tv, None, pin="5555").status_code, 200)

    def test_wrong_pins_are_throttled(self):
        self.set_pin(self.kid, "1234")
        for _ in range(auth.FAIL_LIMIT):
            self.assertEqual(self.pick(self.owner, "pkid", pin="0000").status_code, 403)
        self.assertEqual(self.pick(self.owner, "pkid", pin="0000").status_code, 429)
        self.assertEqual(self.pick(self.owner, "pkid", pin="1234").status_code, 429, "even the right one waits")
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr")

    def test_an_admin_sets_the_household_and_clears_a_pin(self):
        self.assertEqual(self.admin.patch(f"/api/users/{self.uid['pguest']}", json={"household": True}, headers=H).status_code, 200)
        listed = {p["name"]: p for p in self.admin.get("/api/users").json()}
        self.assertTrue(listed["pguest"]["household"])
        self.assertFalse(listed["admin"]["household"])
        self.assertNotIn("pin", listed["pguest"], "the hash never leaves")
        self.set_pin(self.guest, "1111")
        self.assertTrue(next(p for p in self.admin.get("/api/users").json() if p["name"] == "pguest")["has_pin"])
        self.assertEqual(self.admin.patch(f"/api/users/{self.uid['pguest']}", json={"clear_pin": True}, headers=H).status_code, 200)
        self.assertFalse(next(p for p in self.admin.get("/api/users").json() if p["name"] == "pguest")["has_pin"])
        self.assertEqual(self.guest.patch("/api/users/1", json={"household": True}, headers=H).status_code, 403, "only an admin")
        me = self.mum.get("/api/me").json()
        self.assertEqual((me["household"], me["has_pin"]), (True, False))

    def test_a_tv_paired_from_outside_the_household_stays_its_owner_s(self):
        t, tid = paired(self.guest, "Guest room")
        self.assertEqual(self.kid.post(f"/api/tv/{tid}/do", json={"cmd": "toggle"}, headers=H).status_code, 404, "the household can't drive it")
        self.assertEqual(self.kid.get(f"/api/tv/{tid}/people").status_code, 404)
        self.assertNotIn(tid, [x["id"] for x in self.kid.get("/api/tv").json()["tvs"]])
        self.assertEqual(self.pick(self.guest, "pkid", tid=tid).status_code, 404, "nor can it act as one of the household")
        self.assertEqual([p["name"] for p in self.guest.get(f"/api/tv/{tid}/people").json()["people"]], ["pguest"])
        self.assertEqual(tv.audience(tid), {self.uid["pguest"]})

    def test_a_tv_never_manages_an_account(self):
        self.pick(self.owner, "pmum")
        for method, path, body in (("get", "/api/me/sessions", None), ("post", "/api/auth/logout-everywhere", {}), ("get", "/api/me/api-keys", None),
                                   ("post", "/api/me/api-keys", {"name": "x"}), ("post", "/api/me/password", {"old": PW, "new": "another horse"}),
                                   ("put", "/api/me/pin", {"pin": "1234", "password": PW}), ("post", "/api/tv/pair", {"code": "ABCDEFGH", "name": "x"})):
            r = getattr(self.tv, method)(path, headers=H, **({"json": body} if body is not None else {}))
            self.assertEqual(r.status_code, 403, f"{method} {path}")
            self.assertIn("Not from a TV", r.json()["detail"])
        self.assertEqual(self.mum.get("/api/me/sessions").status_code, 200, "her own phone still does")
        self.assertEqual(self.tv.get("/api/me/history").status_code, 200, "what a TV shows is still hers")

    def test_signing_out_everywhere_or_a_new_pin_takes_the_tv_back(self):
        self.pick(self.owner, "pmum")
        self.assertEqual(self.set_pin(self.mum, "4321").status_code, 200)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr", "a new PIN: picking her asks for it")
        self.assertEqual(self.pick(self.owner, "pmum", "4321").status_code, 200)
        self.assertEqual(self.mum.post("/api/auth/logout-everywhere", headers=H).status_code, 200)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr")
        self.mum = signed_in("pmum")

    def test_leaving_the_household_clears_the_pick_for_good(self):
        self.pick(self.owner, "pkid")
        kid = self.uid["pkid"]
        self.assertEqual(self.admin.patch(f"/api/users/{kid}", json={"household": False}, headers=H).status_code, 200)
        self.assertEqual(self.admin.patch(f"/api/users/{kid}", json={"household": True}, headers=H).status_code, 200)
        self.assertEqual(self.tv.get("/api/me").json()["name"], "pownerr", "back in the household, but nobody picked them again")


if __name__ == "__main__":
    unittest.main()
