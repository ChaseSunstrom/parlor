"""Browsing without links: YouTube's related videos, topic shelves, your topics, and
recommendations built from what you watched. No network: YouTube's answers are fixtures."""
import json
import unittest
from pathlib import Path
from unittest import mock

from yeerr_test_env import H, signed_in

import yt

NEXT = json.loads((Path(__file__).parent / "fixtures" / "next.json").read_text())  # a real `next` reply, trimmed


def card(vid, channel_id="UCaaaaaaaaaaaaaaaaaaaaaa", **kw):
    return {"type": "video", "id": vid, "title": vid, "channel": "c", "channel_id": channel_id, "thumb": "", **kw}


class Related(unittest.TestCase):
    def setUp(self):
        yt._related.data.clear()

    def test_parses_youtubes_up_next_list(self):
        items = yt.lockup_cards(NEXT)
        self.assertEqual(len(items), 3, "videos only: the Shorts shelf and the 'more' button are skipped")
        first = items[0]
        self.assertEqual((first["id"], first["title"], first["channel"]), ("vE57fPUAQh0", "The Good Dinosaur -  Best Scenes", "Hinl"))
        self.assertRegex(first["channel_id"], r"^UC[\w-]{22}$")
        self.assertEqual(first["duration"], 23 * 60 + 57)
        self.assertEqual(first["views"], 16_000_000)
        self.assertAlmostEqual(first["published"], yt._ago("9 years ago"), delta=5)
        for i in items:
            self.assertTrue(i["title"] and i["channel"] and i["duration"] and i["views"], i)

    def test_ago(self):
        self.assertEqual(yt._ago("9 years ago", now=10**9), 10**9 - 9 * 365 * 86400)
        self.assertEqual(yt._ago("Streamed 1 week ago", now=10**6), 10**6 - 7 * 86400)
        self.assertIsNone(yt._ago("Premieres tomorrow"))

    def test_fetched_once_then_cached_and_failures_are_empty(self):
        with mock.patch.object(yt.YoutubeIE, "_call_api", return_value=NEXT) as api:
            self.assertEqual(len(yt.related("aqz-KE-bpKQ")), 3)
            self.assertEqual(len(yt.related("aqz-KE-bpKQ")), 3)
        self.assertEqual(api.call_count, 1)
        with mock.patch.object(yt.YoutubeIE, "_call_api", side_effect=RuntimeError("HTTP Error 429")):
            self.assertEqual(yt.related("jNQXAC9IVRw"), [])
        with mock.patch.object(yt.YoutubeIE, "_call_api", return_value={"contents": {}}):
            self.assertEqual(yt.related("dQw4w9WgXcQ"), [], "a changed layout means no related videos, not an error")

    def test_endpoint_marks_and_checks_the_id(self):
        c = signed_in("browser")
        with mock.patch.object(yt.YoutubeIE, "_call_api", return_value=NEXT):
            r = c.get("/api/yt/related/aqz-KE-bpKQ")
        self.assertEqual(r.status_code, 200)
        self.assertIn("later", r.json()["items"][0])
        self.assertEqual(c.get("/api/yt/related/not-an-id!").status_code, 422)


    def test_failures_are_retried_after_a_few_minutes_not_on_every_page(self):
        yt._related_miss.data.clear()
        with mock.patch.object(yt.YoutubeIE, "_call_api", side_effect=RuntimeError("HTTP Error 429")) as api:
            yt.related("jNQXAC9IVRw")
            yt.related("jNQXAC9IVRw")
        self.assertEqual(api.call_count, 1)

    def test_channel_cards_say_whether_you_follow_them(self):
        c = signed_in("follower")
        cid = "UCffffffffffffffffffffff"
        c.post("/api/me/subs", json={"channel_id": cid, "name": "f"}, headers=H)
        user = yt.one("SELECT id FROM users WHERE name = 'follower'")["id"]
        marked = yt.mark([{"type": "channel", "id": cid}, {"type": "channel", "id": "UCgggggggggggggggggggggg"}], user)
        self.assertEqual([m["subscribed"] for m in marked], [True, False])


class TopicShelves(unittest.TestCase):
    def setUp(self):
        yt._topic.data.clear()

    def test_this_weeks_most_watched_videos_cached(self):
        c = signed_in("browser")
        entries = [{"id": "aaaaaaaaaaa", "ie_key": "Youtube", "title": "a", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "view_count": 10},
                   {"id": "ccccccccccc", "ie_key": "Youtube", "title": "c", "url": "https://www.youtube.com/watch?v=ccccccccccc", "view_count": 900},
                   {"id": "UCbbbbbbbbbbbbbbbbbbbbbb", "url": "https://www.youtube.com/channel/UCbbbbbbbbbbbbbbbbbbbbbb", "title": "chan"}]
        with mock.patch.object(yt, "extract", return_value={"entries": entries}) as ex:
            first = c.get("/api/yt/topic", params={"q": "Space & stars"}).json()
            again = c.get("/api/yt/topic", params={"q": "space & STARS"}).json()
        self.assertEqual([i["id"] for i in first["items"]], ["ccccccccccc", "aaaaaaaaaaa"], "videos only, most watched first")
        self.assertEqual(again, first)
        self.assertEqual(ex.call_count, 1)
        url = ex.call_args[0][0]
        self.assertIn("search_query=Space%20%26%20stars", url)
        self.assertIn("sp=" + yt.TOPIC_FILTER, url)
        self.assertEqual(c.get("/api/yt/topic", params={"q": "  "}).status_code, 422)


class YourTopics(unittest.TestCase):
    def test_defaults_then_saved_per_account(self):
        a, b = signed_in("topics-a"), signed_in("topics-b")
        self.assertEqual(a.get("/api/me/topics").json()["topics"], yt.DEFAULT_TOPICS)
        r = a.put("/api/me/topics", json={"topics": [" Retro  games ", "retro games", "", "Woodworking"] + [f"t{i}" for i in range(30)]}, headers=H)
        self.assertEqual(r.status_code, 200)
        saved = r.json()["topics"]
        self.assertEqual(saved[:2], ["Retro games", "Woodworking"], "trimmed, and the same topic twice is kept once")
        self.assertEqual(len(saved), 20)
        self.assertEqual(a.get("/api/me/topics").json()["topics"], saved)
        self.assertEqual(b.get("/api/me/topics").json()["topics"], yt.DEFAULT_TOPICS, "each account has its own")
        self.assertEqual(a.put("/api/me/topics", json={"topics": []}, headers=H).json()["topics"], [])
        self.assertEqual(a.get("/api/me/topics").json()["topics"], [], "no topics is a choice, not the defaults")


class Recommended(unittest.TestCase):
    def test_built_from_videos_like_the_ones_you_watched(self):
        c = signed_in("recommends")
        for vid, ch in (("seen1111111", "UCone11111111111111111111"[:24]), ("seen2222222", "UCtwo22222222222222222222"[:24])):
            c.post("/api/me/history", json={"video": card(vid, ch), "position": 30}, headers=H)
        like = {"seen1111111": [card("rel1aaaaaaa"), card("seen2222222"), card("shared11111")],
                "seen2222222": [card("rel2aaaaaaa"), card("shared11111")]}
        with mock.patch.object(yt, "related", side_effect=lambda v: like[v]), \
                mock.patch.object(yt, "merged_feed", return_value=([card("feed1111111", "UCzzzzzzzzzzzzzzzzzzzzzz")], [], {})):
            r = c.get("/api/yt/recommended").json()
        ids = [i["id"] for i in r["items"]]
        self.assertEqual(r["source"], "local")
        self.assertNotIn("seen2222222", ids, "nothing you've already watched")
        self.assertEqual(len(ids), len(set(ids)), "no repeats")
        self.assertEqual(set(ids), {"rel1aaaaaaa", "rel2aaaaaaa", "shared11111", "feed1111111"})
        self.assertEqual(ids[0], "rel2aaaaaaa", "the newest video watched leads")


class NewAccount(unittest.TestCase):
    def test_a_brand_new_account_still_gets_a_full_home(self):
        c = signed_in("newcomer")
        yt._topic.data.clear()
        self.assertEqual(c.get("/api/me/history").json()["items"], [])
        self.assertEqual(c.get("/api/yt/recommended").json()["source"], "none")
        topics = c.get("/api/me/topics").json()["topics"]
        self.assertGreaterEqual(len(topics), 6, "topic shelves fill the page before there's any history")
        video = {"id": "ddddddddddd", "ie_key": "Youtube", "title": "d", "url": "https://www.youtube.com/watch?v=ddddddddddd"}
        with mock.patch.object(yt, "extract", return_value={"entries": [video]}):
            for q in topics:
                self.assertEqual(c.get("/api/yt/topic", params={"q": q}).json()["items"][0]["id"], "ddddddddddd")


if __name__ == "__main__":
    unittest.main()
