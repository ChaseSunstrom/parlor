"""A person's age limit: an admin sets it in People, and it reaches Jellyfin as MaxParentalRating (with
BlockUnratedItems) on the person's own Jellyfin user, so Jellyfin hides what is too old everywhere, in Parlor
and in any Jellyfin app. Clearing removes it; a failure leaves the stored limit as it was."""
import unittest
from unittest import mock

from yeerr_test_env import H, signed_in

import core
import jf
from test_access import FakeJellyfin, access


class AgeLimit(unittest.TestCase):
    def setUp(self):
        core.set_setting("jellyfin_url", "http://jellyfin:8096")
        core.set_setting("jellyfin_key", "k")
        core.set_setting("jellyfin_user", "default")
        self.addCleanup(core.set_setting, "jellyfin_url", "")
        self.fake = FakeJellyfin()
        p = mock.patch.object(jf.requests, "request", self.fake)
        p.start()
        self.addCleanup(p.stop)
        jf._users.drop("all")
        self.admin = signed_in("admin")
        self.member = signed_in("age-member")
        self.mid = self.member.get("/api/me").json()["id"]
        core.db().execute("UPDATE users SET jf_user = NULL, jf_own = 0, libraries = NULL, blocked = '', max_rating = NULL,"
                          " jellyfin_id = NULL WHERE id = ?", (self.mid,))

    def stored(self):
        return core.one("SELECT max_rating FROM users WHERE id = ?", self.mid)["max_rating"]

    def policy(self):
        return self.fake.policies[core.one("SELECT jf_user FROM users WHERE id = ?", self.mid)["jf_user"]]

    def test_setting_it_posts_the_rating_to_their_own_jellyfin_user(self):
        r = access(self.admin, self.mid, max_rating=10)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["max_rating"], 10)
        p = self.policy()
        self.assertEqual(p["MaxParentalRating"], 10)
        self.assertIn("Movie", p["BlockUnratedItems"])
        self.assertIn("Series", p["BlockUnratedItems"])
        self.assertNotIn("Music", p["BlockUnratedItems"], "music has no rating: it stays")
        self.assertTrue(p["EnableAllFolders"], "no library was closed by it")
        self.assertTrue(self.fake.users[0]["Policy"]["EnableAllFolders"], "the shared default user isn't touched")
        self.assertEqual(self.stored(), 10)
        listed = {u["id"]: u for u in self.admin.get("/api/users").json()}
        self.assertEqual(listed[self.mid]["max_rating"], 10)
        self.assertEqual(self.member.get("/api/me").json()["max_rating"], 10)

    def test_changing_it_reuses_their_jellyfin_user(self):
        access(self.admin, self.mid, max_rating=10)
        access(self.admin, self.mid, max_rating=7)
        self.assertEqual(len([c for c in self.fake.calls if c[1] == "/Users/New"]), 1)
        self.assertEqual(self.policy()["MaxParentalRating"], 7)

    def test_clearing_it_removes_it(self):
        access(self.admin, self.mid, max_rating=10)
        self.assertEqual(access(self.admin, self.mid, max_rating=None).status_code, 200)
        p = self.policy()
        self.assertNotIn("MaxParentalRating", p)
        self.assertEqual(p["BlockUnratedItems"], [])
        self.assertIsNone(self.stored())

    def test_leaving_it_out_changes_nothing(self):
        access(self.admin, self.mid, max_rating=10)
        n = len(self.fake.calls)
        self.assertEqual(access(self.admin, self.mid, blocked=["music"]).status_code, 200)
        self.assertEqual(len(self.fake.calls), n, "no call to Jellyfin")
        self.assertEqual(self.stored(), 10)
        access(self.admin, self.mid, blocked=[])

    def test_no_limit_and_clearing_it_again_never_calls_jellyfin(self):
        self.assertEqual(access(self.admin, self.mid, max_rating=None).status_code, 200)
        self.assertEqual(self.fake.calls, [])

    def test_libraries_and_the_rating_keep_each_other(self):
        access(self.admin, self.mid, max_rating=10)
        access(self.admin, self.mid, libraries=["f" * 32])
        p = self.policy()
        self.assertEqual((p["MaxParentalRating"], p["EnabledFolders"]), (10, ["f" * 32]))
        access(self.admin, self.mid, max_rating=5)
        p = self.policy()
        self.assertEqual((p["MaxParentalRating"], p["EnabledFolders"], p["EnableAllFolders"]), (5, ["f" * 32], False))

    def test_a_failure_leaves_the_stored_limit_as_it_was(self):
        access(self.admin, self.mid, max_rating=10)

        def down(*a, **k):
            raise jf.requests.ConnectionError("down")
        with mock.patch.object(jf.requests, "request", down):
            self.assertEqual(access(self.admin, self.mid, max_rating=5).status_code, 502)
            self.assertEqual(access(self.admin, self.mid, max_rating=None).status_code, 502)
        self.assertEqual(self.stored(), 10)
        self.assertEqual(self.policy()["MaxParentalRating"], 10)

    def test_a_first_limit_that_fails_leaves_none(self):
        def down(*a, **k):
            raise jf.requests.ConnectionError("down")
        with mock.patch.object(jf.requests, "request", down):
            self.assertEqual(access(self.admin, self.mid, max_rating=5).status_code, 502)
        self.assertIsNone(self.stored())

    def test_only_an_admin_sets_it_and_only_a_sane_number(self):
        self.assertEqual(access(self.member, self.mid, max_rating=1).status_code, 403)
        self.assertIsNone(self.stored())
        for bad in (-1, 1001, "PG", 1.5):
            self.assertEqual(access(self.admin, self.mid, max_rating=bad).status_code, 422, bad)
        self.assertEqual(self.fake.calls, [])
        admin_id = self.admin.get("/api/me").json()["id"]
        self.assertEqual(access(self.admin, admin_id, max_rating=5).status_code, 409, "admins always have everything")

    def test_becoming_an_admin_lifts_it(self):
        access(self.admin, self.mid, max_rating=10)
        uid = core.one("SELECT jf_user FROM users WHERE id = ?", self.mid)["jf_user"]
        self.assertEqual(self.admin.patch(f"/api/users/{self.mid}", json={"admin": True}, headers=H).status_code, 200)
        self.assertIsNone(self.stored())
        self.assertNotIn("MaxParentalRating", self.fake.policies[uid])
        core.db().execute("UPDATE users SET admin = 0 WHERE id = ?", (self.mid,))


if __name__ == "__main__":
    unittest.main()
