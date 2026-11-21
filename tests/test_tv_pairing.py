"""Pairing a TV: the TV asks for a code (no sign-in), a signed-in phone enters it, and the TV claims a session of that
account, so nobody types a password on a TV. Codes are 8 characters without look-alikes, single use, 5 minutes,
stored hashed, and guessing them is throttled; pairings live in the database, and end when the account signs out
everywhere, is deleted, or unpairs the TV."""
import time
import unittest
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import core
import tv
from yeerr_test_env import H, client, signed_in

ALPHABET = set("23456789ABCDEFGHJKMNPQRSTUVWXYZ")


def new_tv():
    c = client()
    r = c.post("/api/tv/code", headers=H)
    assert r.status_code == 200, r.text
    return c, r.json()


class Pairing(unittest.TestCase):
    def setUp(self):
        tv._tries.clear()
        core.db().execute("DELETE FROM tvs")
        core.db().execute("DELETE FROM tv_codes")
        self.owner = signed_in("tvowner")

    def pair(self, code, who=None, name="Living room"):
        return (who or self.owner).post("/api/tv/pair", json={"code": code, "name": name}, headers=H)

    def test_a_tv_pairs_by_code_and_gets_its_own_session(self):
        tvc, got = new_tv()
        code, claim = got["code"], got["claim"]
        self.assertEqual(len(code), 8)
        self.assertTrue(set(code) <= ALPHABET, code)
        self.assertIsNone(core.one("SELECT 1 AS x FROM tv_codes WHERE code = ?", code), "the code is stored hashed")
        self.assertEqual(tvc.post("/api/tv/claim", json={"claim": claim}, headers=H).json(), {"paired": False})
        self.assertEqual(tvc.get("/api/me").status_code, 401)
        r = self.pair(f"{code[:4].lower()}-{code[4:].lower()}")  # as typed on a phone
        self.assertEqual(r.status_code, 200, r.text)
        r = tvc.post("/api/tv/claim", json={"claim": claim}, headers=H)
        self.assertEqual(r.json()["paired"], True)
        self.assertEqual(tvc.get("/api/me").json()["name"], "tvowner", "the TV is signed in as the phone's account")
        tvs = self.owner.get("/api/tv").json()["tvs"]
        self.assertEqual([t["name"] for t in tvs], ["Living room"])
        self.assertEqual(tvc.get("/api/tv/me").json()["tv"]["name"], "Living room", "the TV knows it's a TV")
        self.assertIsNone(self.owner.get("/api/tv/me").json()["tv"])

    def test_a_code_works_once_and_for_five_minutes(self):
        tvc, got = new_tv()
        self.assertEqual(self.pair(got["code"]).status_code, 200)
        self.assertEqual(self.pair(got["code"]).status_code, 404, "single use")
        tvc.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H)
        self.assertEqual(tvc.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H).status_code, 404, "claimed once")
        late, got = new_tv()
        later = time.time() + tv.CODE_LIFE + 1
        with mock.patch.object(tv, "_now", lambda: later):
            self.assertEqual(self.pair(got["code"]).status_code, 404, "expired")
            self.assertEqual(late.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H).status_code, 404)

    def test_guessing_is_throttled_per_address(self):
        _, got = new_tv()
        for _ in range(5):
            self.assertEqual(self.pair("ZZZZZZZZ").status_code, 404)
        self.assertEqual(self.pair(got["code"]).status_code, 429, "the sixth try in a minute waits, even a right one")
        later = time.time() + 61
        with mock.patch.object(tv, "_now", lambda: later):
            self.assertEqual(self.pair(got["code"]).status_code, 200)

    def test_only_the_tv_side_is_open(self):
        self.assertEqual(client().post("/api/tv/pair", json={"code": "ABCDEFGH", "name": "x"}, headers=H).status_code, 401)
        self.assertEqual(client().get("/api/tv").status_code, 401)
        self.assertEqual(client().post("/api/tv/code").status_code, 403, "changes need the header, codes too")
        self.assertEqual(self.pair("ABC", name="x" * 200).status_code, 422)
        self.assertEqual(self.pair("ABC", name="   ").status_code, 422, "a name of spaces is no name")

    def test_a_tv_session_is_never_an_admin(self):
        boss = signed_in("tvboss", admin=True)
        tvc, got = new_tv()
        self.pair(got["code"], who=boss)
        tvc.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H)
        self.assertFalse(tvc.get("/api/me").json()["admin"], "an admin's TV plays things, it doesn't run Parlor")
        self.assertEqual(tvc.get("/api/users").status_code, 403)
        self.assertTrue(boss.get("/api/me").json()["admin"], "the admin's own sign-in is unchanged")

    def test_pairings_end_with_sign_out_everywhere_deletion_or_unpairing(self):
        tvc, got = new_tv()
        self.pair(got["code"])
        tvc.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H)
        tid = self.owner.get("/api/tv").json()["tvs"][0]["id"]
        other = signed_in("tvstranger")
        self.assertEqual(other.delete(f"/api/tv/{tid}", headers=H).status_code, 404, "someone else's TV isn't there for them")
        self.assertEqual(self.owner.delete(f"/api/tv/{tid}", headers=H).status_code, 200)
        self.assertEqual(tvc.get("/api/me").status_code, 401, "unpaired: the TV's session is gone")
        tvc, got = new_tv()
        self.pair(got["code"])
        tvc.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H)
        self.owner.post("/api/auth/logout-everywhere", headers=H)
        self.assertEqual(tvc.get("/api/me").status_code, 401)
        self.assertIsNone(core.one("SELECT 1 AS x FROM tvs"), "the pairing went with the sessions")
        owner = signed_in("tvowner")
        tvc, got = new_tv()
        self.pair(got["code"], who=owner)
        tvc.post("/api/tv/claim", json={"claim": got["claim"]}, headers=H)
        uid = core.one("SELECT id FROM users WHERE name = 'tvowner'")["id"]
        core.db().execute("DELETE FROM users WHERE id = ?", (uid,))
        self.assertIsNone(core.one("SELECT 1 AS x FROM tvs"), "and with the account")


if __name__ == "__main__":
    unittest.main()
