"""Downloaded videos are deleted after the retention period, unless kept; off by default."""
import json
import time
import unittest

from yeerr_test_env import H, signed_in

import core
import yt

DAY = 86400


def saved(vid, days_old, duration=600, user_id=None, keep=0):
    """A downloaded video row and its files, `days_old` days old."""
    folder = core.VIDEOS / "chan"
    folder.mkdir(exist_ok=True)
    (folder / f"t [{vid}].mp4").write_bytes(b"v")
    (folder / f"t [{vid}].jpg").write_bytes(b"j")
    core.db().execute("INSERT OR REPLACE INTO videos (id, item, path, size, quality, user_id, added, keep) VALUES (?, ?, ?, 1, '720', ?, ?, ?)",
                      (vid, json.dumps({"title": "t", "duration": duration}), f"chan/t [{vid}].mp4", user_id, time.time() - days_old * DAY, keep))


def ids():
    return {r["id"] for r in core.rows("SELECT id FROM videos")}


class Retention(unittest.TestCase):
    def setUp(self):
        self.tearDown()

    def tearDown(self):  # the database is shared with the other test files
        core.db().execute("DELETE FROM videos")
        core.db().execute("DELETE FROM history WHERE video_id IN ('halfway1111', 'finished111', 'justdone111')")
        core.db().execute("DELETE FROM settings WHERE key LIKE 'video_keep%'")

    def test_off_by_default_keeps_everything(self):
        saved("old11111111", 400)
        yt.sweep()
        self.assertEqual(ids(), {"old11111111"})

    def test_old_videos_go_with_their_files_unless_kept(self):
        core.set_setting("video_keep_days", "30")
        saved("old11111111", 31)
        saved("new11111111", 5)
        saved("kept1111111", 90, keep=1)
        yt.sweep()
        self.assertEqual(ids(), {"new11111111", "kept1111111"})
        self.assertFalse(list(core.VIDEOS.glob("chan/*old11111111*")), "the video and its thumbnail are gone")
        yt.sweep()  # a second sweep is harmless
        self.assertEqual(ids(), {"new11111111", "kept1111111"})

    def test_a_row_whose_file_is_gone_is_dropped_too(self):
        core.set_setting("video_keep_days", "1")
        saved("gone1111111", 3)
        (core.VIDEOS / "chan" / "t [gone1111111].mp4").unlink()
        yt.sweep()
        self.assertEqual(ids(), set())

    def test_watched_only_counts_from_when_someone_finished_it(self):
        c = signed_in("watcher")
        core.set_setting("video_keep_days", "7")
        core.set_setting("video_keep_watched_only", "1")
        for vid in ("unseen11111", "halfway1111", "finished111", "justdone111"):
            saved(vid, 30)
        card = lambda vid: {"id": vid, "title": "t", "duration": 600}  # noqa: E731
        c.post("/api/me/history", json={"video": card("halfway1111"), "position": 200}, headers=H)
        c.post("/api/me/history", json={"video": card("finished111"), "position": 590}, headers=H)
        c.post("/api/me/history", json={"video": card("justdone111"), "position": 595}, headers=H)
        core.db().execute("UPDATE history SET watched = ? WHERE video_id IN ('halfway1111', 'finished111')", (time.time() - 10 * DAY,))
        yt.sweep()
        self.assertEqual(ids(), {"unseen11111", "halfway1111", "justdone111"}, "finished 10 days ago goes; finished today stays")

    def test_settings_and_keep_over_the_api(self):
        admin, member = signed_in("admin"), signed_in("member-r")
        self.assertEqual(member.get("/api/yt/retention").json(), {"days": 0, "watched_only": False})
        self.assertEqual(member.put("/api/yt/retention", json={"days": 30}, headers=H).status_code, 403, "admins only")
        r = admin.put("/api/yt/retention", json={"days": 14, "watched_only": False}, headers=H)
        self.assertEqual(r.json(), {"days": 14, "watched_only": False})
        self.assertEqual(admin.put("/api/yt/retention", json={"days": -1}, headers=H).status_code, 422)

        mine = core.one("SELECT id FROM users WHERE name = 'member-r'")["id"]
        saved("mine1111111", 1, user_id=mine)
        saved("theirs11111", 1)
        item = next(i for i in member.get("/api/yt/library").json()["items"] if i["id"] == "mine1111111")
        self.assertFalse(item["keep"])
        self.assertAlmostEqual(item["expires"], time.time() + 13 * DAY, delta=60)
        self.assertEqual(member.post("/api/yt/library/mine1111111/keep", json={"keep": True}, headers=H).status_code, 200)
        item = next(i for i in member.get("/api/yt/library").json()["items"] if i["id"] == "mine1111111")
        self.assertTrue(item["keep"])
        self.assertIsNone(item["expires"])
        self.assertEqual(member.post("/api/yt/library/theirs11111/keep", json={"keep": True}, headers=H).status_code, 403)
        self.assertEqual(admin.post("/api/yt/library/theirs11111/keep", json={"keep": True}, headers=H).status_code, 200)

    def test_existing_databases_get_the_keep_column(self):
        self.assertIn("keep", {r["name"] for r in core.rows("PRAGMA table_info(videos)")})


if __name__ == "__main__":
    unittest.main()
