"""Signed-in devices: Settings lists the sessions of your own account and signs one out. Only the owner sees or ends a
session; the page never gets the stored session token, only a derived id."""
import re
import unittest

import yeerr_test_env as env
from yeerr_test_env import H, signed_in

import core
import private


class Devices(unittest.TestCase):
    def setUp(self):
        core.db().execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'dev-%')")  # the earlier tests' devices
        self.phone, self.laptop = signed_in("dev-owner"), signed_in("dev-owner")  # two sessions of one account
        self.stranger = signed_in("dev-stranger")

    def listed(self, c):
        r = c.get("/api/me/sessions")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_it_lists_your_devices_and_marks_this_one(self):
        mine = self.listed(self.phone)
        self.assertEqual(len(mine), 2)
        self.assertEqual([d["current"] for d in mine].count(True), 1)
        self.assertTrue(all({"id", "agent", "created", "seen", "tv"} <= set(d) for d in mine))

    def test_the_page_never_gets_the_stored_token(self):
        stored = {r["token"] for r in core.rows("SELECT token FROM sessions")}
        for d in self.listed(self.phone):
            self.assertRegex(d["id"], r"^[0-9a-f]{16}$")
            self.assertFalse(any(d["id"] in t for t in stored), "not a piece of the stored token either")
        self.assertNotIn(self.phone.cookies.get(core.COOKIE), str(self.listed(self.phone)))

    def test_you_sign_out_one_of_your_devices_and_the_rest_stay(self):
        other = next(d for d in self.listed(self.phone) if not d["current"])
        r = self.phone.delete(f"/api/me/sessions/{other['id']}", headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.laptop.get("/api/me").status_code, 401, "that device is signed out")
        self.assertEqual(self.phone.get("/api/me").status_code, 200, "this one stays")
        self.assertEqual(len(self.listed(self.phone)), 1)

    def test_signing_out_this_device_is_a_sign_out(self):
        here = next(d for d in self.listed(self.phone) if d["current"])
        self.assertEqual(self.phone.delete(f"/api/me/sessions/{here['id']}", headers=H).status_code, 200)
        self.assertEqual(self.phone.get("/api/me").status_code, 401)
        self.assertEqual(self.laptop.get("/api/me").status_code, 200)

    def test_nobody_else_sees_or_ends_your_device(self):
        theirs = self.listed(self.phone)
        self.assertEqual(len(self.listed(self.stranger)), 1, "only their own")
        self.assertFalse({d["id"] for d in theirs} & {d["id"] for d in self.listed(self.stranger)})
        for d in theirs:
            r = self.stranger.delete(f"/api/me/sessions/{d['id']}", headers=H)
            self.assertEqual(r.status_code, 404, "the same answer as for no such device")
        self.assertEqual((self.phone.get("/api/me").status_code, self.laptop.get("/api/me").status_code), (200, 200))
        self.assertEqual(len(self.listed(self.phone)), 2)

    def test_a_made_up_id_is_not_found(self):
        for bad in ("0" * 16, "x", "%2e%2e", "%00"):
            self.assertEqual(self.phone.delete(f"/api/me/sessions/{bad}", headers=H).status_code, 404, bad)

    def test_signed_in_and_the_header_are_needed(self):
        self.assertEqual(env.client().get("/api/me/sessions").status_code, 401)
        self.assertEqual(env.client().delete("/api/me/sessions/" + "0" * 16, headers=H).status_code, 401)
        any_id = self.listed(self.phone)[0]["id"]
        self.assertEqual(self.phone.delete(f"/api/me/sessions/{any_id}").status_code, 403, "no X-Requested-With")
        self.assertEqual(len(self.listed(self.phone)), 2)

    def test_signing_a_device_out_closes_its_private_items_too(self):
        other = next(d for d in self.listed(self.phone) if not d["current"])
        digest = next(r["token"] for r in core.rows("SELECT token FROM sessions") if re.fullmatch(r"[0-9a-f]{64}", r["token"])
                      and core._digest("device:" + r["token"])[:16] == other["id"])
        private._held[digest] = (0, object())
        self.addCleanup(private._held.pop, digest, None)
        self.phone.delete(f"/api/me/sessions/{other['id']}", headers=H)
        self.assertNotIn(digest, private._held)


if __name__ == "__main__":
    unittest.main()
