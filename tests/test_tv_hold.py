"""Holding a TV's remote: one person keeps it, and the others' remotes (their presses, casts and Who's watching) are
refused with who has it, until the holder lets go, is quiet for 15 minutes or is taken off the TV. The TV's owner always
outranks: their presses go through and they can take the hold. Everyone who hears the TV hears who holds it."""
import unittest

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import core
import tv
from test_tv_links import paired
from yeerr_test_env import H, signed_in


class Hold(unittest.TestCase):
    def setUp(self):
        tv._tries.clear()
        tv.hub.hold.clear()
        for table in ("tvs", "tv_codes", "tv_links", "tv_remotes"):
            core.db().execute(f"DELETE FROM {table}")
        self.owner = signed_in("howner")
        self.me = signed_in("hme")
        self.sis = signed_in("hsis")
        core.db().execute("UPDATE users SET household = 0, pin = NULL, admin = 0 WHERE name IN ('howner', 'hme', 'hsis')")
        core.db().execute("UPDATE users SET admin = 1 WHERE name = 'howner'")
        self.uid = {r["name"]: r["id"] for r in core.rows("SELECT id, name FROM users")}
        self.tv, self.tid = paired(self.owner)
        for who in ("hme", "hsis"):
            self.assertEqual(self.owner.post(f"/api/tv/{self.tid}/remotes", json={"user_id": self.uid[who]}, headers=H).status_code, 200)
        self.q = tv.asyncio.Queue()  # the TV, listening
        tv.hub.tvs[self.tid].add(self.q)
        tv.hub.seen[self.tid] = tv._now()

    def tearDown(self):
        tv.hub.tvs[self.tid].discard(self.q)

    def press(self, who):
        return who.post(f"/api/tv/{self.tid}/do", json={"cmd": "key", "key": "down"}, headers=H)

    def hold(self, who, on=True):
        return who.post(f"/api/tv/{self.tid}/hold", json={"on": on}, headers=H)

    def test_while_i_hold_it_nobody_else_but_the_owner_drives_it(self):
        self.assertEqual(self.hold(self.me).json()["hold"], {"id": self.uid["hme"], "name": "hme"})
        self.assertEqual(self.press(self.me).status_code, 200)
        r = self.press(self.sis)
        self.assertEqual(r.status_code, 409)
        self.assertIn("hme has the remote", r.json()["detail"])
        self.assertEqual(self.sis.post(f"/api/tv/{self.tid}/do", json={"cmd": "cast", "cast": {"kind": "jf", "id": "x", "title": "x"}}, headers=H).status_code, 409, "no casting over it")
        self.assertEqual(self.sis.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["hsis"]}, headers=H).status_code, 409, "nor switching who's watching")
        self.assertEqual(self.hold(self.sis).status_code, 409, "nor taking it")
        self.assertEqual(self.press(self.owner).status_code, 200, "the owner outranks")
        self.assertEqual(self.hold(self.owner).json()["hold"]["id"], self.uid["howner"], "and can take it")
        self.assertEqual(self.press(self.me).status_code, 409)

    def test_letting_go_and_running_out(self):
        self.hold(self.me)
        self.assertEqual(self.hold(self.sis, False).status_code, 409, "only the holder or the owner lets go")
        self.assertIsNone(self.hold(self.me, False).json()["hold"])
        self.assertEqual(self.press(self.sis).status_code, 200)
        self.hold(self.me)
        tv.hub.hold[self.tid]["at"] -= tv.HOLD_QUIET + 1  # 15 quiet minutes
        self.assertEqual(self.press(self.sis).status_code, 200, "a hold nobody uses runs out")
        self.hold(self.me)
        tv.hub.hold[self.tid]["at"] -= tv.HOLD_QUIET - 60
        self.press(self.me)  # each press of the holder's keeps it
        tv.hub.hold[self.tid]["at"] -= 120
        self.assertEqual(self.press(self.sis).status_code, 409)
        self.owner.delete(f"/api/tv/{self.tid}/remotes/{self.uid['hme']}", headers=H)
        self.assertEqual(self.press(self.sis).status_code, 200, "taken off the TV: the hold goes with them")

    def test_everyone_hears_who_holds_it_and_the_list_says_so(self):
        listening = tv.asyncio.Queue()
        tv.hub.remotes[self.uid["hsis"]].add(listening)
        try:
            self.hold(self.me)
            sent = [listening.get_nowait() for _ in range(listening.qsize())]
        finally:
            tv.hub.remotes[self.uid["hsis"]].discard(listening)
        self.assertIn({"type": "hold", "tv": self.tid, "hold": {"id": self.uid["hme"], "name": "hme"}}, sent)
        listed = next(t for t in self.sis.get("/api/tv").json()["tvs"] if t["id"] == self.tid)
        self.assertEqual(listed["hold"], {"id": self.uid["hme"], "name": "hme"})

    def test_the_tv_itself_is_never_held_off(self):
        self.me.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["hme"]}, headers=H)  # the TV acts as me now
        self.hold(self.sis)
        self.assertEqual(self.tv.post(f"/api/tv/{self.tid}/do", json={"cmd": "key", "key": "down"}, headers=H).status_code, 200, "the TV's own session, acting as someone else")
        self.assertEqual(self.tv.post(f"/api/tv/{self.tid}/viewer", json={"user_id": self.uid["howner"]}, headers=H).status_code, 200, "the couch remote picks")
        self.assertEqual(self.tv.post(f"/api/tv/{self.tid}/hold", json={"on": True}, headers=H).status_code, 403, "and doesn't keep it")

    def test_a_hold_that_runs_out_is_heard_and_an_unpaired_tv_forgets_it(self):
        self.hold(self.me)
        tv.hub.hold[self.tid]["at"] -= tv.HOLD_QUIET + 1
        listening = tv.asyncio.Queue()
        tv.hub.remotes[self.uid["hsis"]].add(listening)
        try:
            self.assertIsNone(tv.held(self.tid))
            sent = [listening.get_nowait() for _ in range(listening.qsize())]
        finally:
            tv.hub.remotes[self.uid["hsis"]].discard(listening)
        self.assertIn({"type": "hold", "tv": self.tid, "hold": None}, sent)
        self.hold(self.me)
        tv.hub.close_tv(self.tid)
        self.assertNotIn(self.tid, tv.hub.hold)


if __name__ == "__main__":
    unittest.main()
