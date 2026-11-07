"""YouTube: link and range parsing, feeds, imports, formats, the stream proxy,
history/subscriptions/watch later, the video library, and download options."""
import os
import unittest
from pathlib import Path
from unittest import mock

import yeerr_test_env as env
from yeerr_test_env import H, signed_in

import core
import yt
from fastapi import HTTPException

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>Some Channel</title>
 <entry><yt:videoId>aaaaaaaaaaa</yt:videoId><title>Long one</title>
  <link rel="alternate" href="https://www.youtube.com/watch?v=aaaaaaaaaaa"/><author><name>Some Channel</name></author>
  <published>2026-11-01T10:00:00+00:00</published>
  <media:group><media:community><media:statistics views="1234"/></media:community></media:group></entry>
 <entry><yt:videoId>bbbbbbbbbbb</yt:videoId><title>A short</title>
  <link rel="alternate" href="https://www.youtube.com/shorts/bbbbbbbbbbb"/><author><name>Some Channel</name></author>
  <published>2026-11-02T10:00:00+00:00</published></entry>
 <entry><yt:videoId>not an id</yt:videoId><title>junk</title></entry>
</feed>"""
UC1, UC2, UC3 = "UC" + "a" * 22, "UC" + "b" * 22, "UC" + "c" * 22


class Parsing(unittest.TestCase):
    def test_links(self):
        cases = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=90s": {"type": "video", "id": "dQw4w9WgXcQ", "t": 90},
            "youtu.be/dQw4w9WgXcQ?t=1m5s": {"type": "video", "id": "dQw4w9WgXcQ", "t": 65},
            "https://m.youtube.com/shorts/dQw4w9WgXcQ": {"type": "video", "id": "dQw4w9WgXcQ"},
            "https://www.youtube.com/playlist?list=PLabcdefghij12345": {"type": "playlist", "id": "PLabcdefghij12345"},
            f"https://www.youtube.com/channel/{UC1}/videos": {"type": "channel", "id": UC1},
            "https://www.youtube.com/@LofiGirl": {"type": "channel", "id": "@LofiGirl"},
        }
        for url, want in cases.items():
            got = yt.parse_link(url)
            self.assertEqual({k: got.get(k) for k in want}, want, url)
        self.assertIsNone(yt.parse_link("https://www.youtube.com/shorts/dQw4w9WgXcQ/extra"))
        self.assertFalse(yt.VIDEO_ID.fullmatch("dQw4w9WgXc\n"), "no trailing newline")
        self.assertFalse(yt.VIDEO_ID.fullmatch("dQw4w9WgXcé"), "ASCII only")
        for not_youtube in ("lofi girl", "https://example.com/watch?v=dQw4w9WgXcQ", "https://notyoutube.com/@x"):
            self.assertIsNone(yt.parse_link(not_youtube), not_youtube)

    def test_ranges(self):
        self.assertEqual(yt.parse_range("bytes=0-", 100), (0, 99))
        self.assertEqual(yt.parse_range("bytes=10-20", 100), (10, 20))
        self.assertEqual(yt.parse_range("bytes=50-500", 100), (50, 99))
        self.assertEqual(yt.parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(yt.parse_range("bytes=5-", None), (5, None))
        self.assertIsNone(yt.parse_range(None, 100))
        self.assertIsNone(yt.parse_range("items=0-5", 100))
        with self.assertRaises(HTTPException) as e:
            yt.parse_range("bytes=100-", 100)
        self.assertEqual(e.exception.status_code, 416)

    def test_feed(self):
        name, items = yt.parse_feed(FEED, UC1)
        self.assertEqual(name, "Some Channel")
        self.assertEqual([i["id"] for i in items], ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        self.assertEqual((items[0]["views"], items[0]["short"], items[1]["short"]), (1234, False, True))
        self.assertEqual(items[0]["published"], 1793527200)  # 2026-11-01T10:00:00Z
        self.assertEqual(items[0]["channel_id"], UC1)

    def test_subscription_exports(self):
        takeout = f"Channel Id,Channel Url,Channel Title\n{UC1},http://www.youtube.com/channel/{UC1},Takeout, Inc\n"
        newpipe = '{"app_version": "0.27", "subscriptions": [{"service_id": 0, "url": "https://www.youtube.com/channel/%s", "name": "Piped"}]}' % UC2
        opml = f'<opml><body><outline text="Opml Chan" title="Opml Chan" type="rss" xmlUrl="https://www.youtube.com/feeds/videos.xml?channel_id={UC3}"/></body></opml>'
        self.assertEqual(yt.find_channels(takeout), {UC1: "Takeout, Inc"})
        self.assertEqual(yt.find_channels(newpipe), {UC2: "Piped"})
        self.assertEqual(yt.find_channels(opml), {UC3: "Opml Chan"})
        self.assertEqual(yt.find_channels(f"https://www.youtube.com/channel/{UC1}\n{UC2}"), {UC1: "", UC2: ""})
        self.assertEqual(yt.find_channels("UC" + "x" * 30), {}, "too long to be a channel id")

    def test_stream_formats(self):
        def f(fid, **kw):
            return {"format_id": fid, "protocol": "https", "url": "https://rr1.googlevideo.com/x", "filesize": 10, **kw}

        info = {"formats": [
            f("137", vcodec="avc1.640028", acodec="none", ext="mp4", height=1080, fps=30),
            f("248", vcodec="vp9", acodec="none", ext="webm", height=1080, fps=30),
            f("160", vcodec="avc1.4d400c", acodec="none", ext="mp4", height=144, fps=30),
            f("140", vcodec="none", acodec="mp4a.40.2", ext="m4a", abr=129),
            f("140-drc", vcodec="none", acodec="mp4a.40.2", ext="m4a", abr=129),
            f("251", vcodec="none", acodec="opus", ext="webm", abr=130, format_note="English original"),
            f("380", vcodec="none", acodec="ac-3", ext="m4a", abr=384),
            f("18", vcodec="avc1.42001E", acodec="mp4a.40.2", ext="mp4", height=360),
            f("96", vcodec="avc1", acodec="mp4a", ext="mp4", height=1080, protocol="m3u8_native"),
        ]}
        video, audio, muxed = yt.stream_formats(info)
        self.assertEqual([v["id"] for v in video][:2], ["137", "248"])
        self.assertEqual(video[0]["mime"], 'video/mp4; codecs="avc1.640028"')
        self.assertEqual(video[1]["mime"], 'video/webm; codecs="vp9"')
        self.assertEqual([a["id"] for a in audio], ["251", "140"], "original language first; DRC and surround dropped")
        self.assertEqual([m["id"] for m in muxed], ["18"], "HLS isn't proxied")

    def test_captions_keep_the_original_auto_track_only(self):
        info = {"language": "en", "subtitles": {"de": [{"name": "German", "ext": "vtt"}], "live_chat": [{}]},
                "automatic_captions": {"en": [{"name": "English"}], "en-orig": [{"name": "English (Original)"}], "fr": [{"name": "French"}]}}
        tracks = yt.caption_tracks(info)
        self.assertEqual([t["lang"] for t in tracks], ["de", "en", "en-orig"])
        self.assertEqual([t["auto"] for t in tracks], [False, True, True])


class FakeUpstream:
    def __init__(self, statuses):
        self.statuses, self.ranges = list(statuses), []

    def __call__(self, url, headers=None, stream=False, timeout=None):
        self.ranges.append(headers["Range"])
        status = self.statuses.pop(0)
        r = mock.Mock(status_code=status, headers={"content-range": "bytes 0-3/100", "content-length": "4"} if status == 206 else {})
        r.iter_content = lambda n: iter([b"abcd"])
        return r


class Streaming(unittest.TestCase):
    INFO = {"formats": [{"format_id": "137", "protocol": "https", "url": "https://rr1.googlevideo.com/v", "filesize": 30 * 1024 * 1024,
                         "vcodec": "avc1.640028", "acodec": "none", "ext": "mp4", "http_headers": {"User-Agent": "x"}}]}

    def test_ranges_are_capped_and_an_expired_link_is_refreshed(self):
        c = signed_in("admin")
        looked_up = []

        def info(vid, user_id=None, fresh=False):
            looked_up.append(fresh)
            return self.INFO

        up = FakeUpstream([403, 206])
        with mock.patch.object(yt, "video_info", side_effect=info), mock.patch.object(yt.requests, "get", side_effect=up):
            r = c.get("/api/yt/stream/dQw4w9WgXcQ/137", headers={"Range": "bytes=0-"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, b"abcd")
        self.assertEqual(r.headers["content-range"], "bytes 0-3/100")
        self.assertEqual(r.headers["content-type"], "video/mp4")
        self.assertEqual(up.ranges, [f"bytes=0-{yt.CHUNK - 1}"] * 2, "at most one chunk per request")
        self.assertIn(True, looked_up, "the expired link was looked up again")

    def test_bad_ids_are_refused(self):
        c = signed_in("admin")
        self.assertEqual(c.get("/api/yt/stream/../../etc/137").status_code, 404)
        self.assertEqual(c.get("/api/yt/stream/short/137").status_code, 422)
        self.assertEqual(c.get("/api/yt/video/not-an-id").status_code, 422)


class YourStuff(unittest.TestCase):
    def test_subscriptions(self):
        c = signed_in("subber")
        self.assertEqual(c.post("/api/me/subs", json={"channel_id": "nope"}, headers=H).status_code, 422)
        c.post("/api/me/subs", json={"channel_id": UC1, "name": "One", "avatar": "javascript:alert(1)"}, headers=H)
        r = c.post("/api/me/subs/import", json={"text": f"{UC2},x,Two\n{UC1},x,One again"}, headers=H).json()
        self.assertEqual(r["found"], 2)
        subs = c.get("/api/me/subs").json()
        self.assertEqual([(s["channel_id"], s["name"]) for s in subs], [(UC1, "One"), (UC2, "Two")])
        self.assertIsNone(subs[0]["avatar"], "only https pictures are kept")
        self.assertIn(f"channel_id={UC2}", c.get("/api/me/subs/export").text)
        other = signed_in("someone else")
        self.assertEqual(other.get("/api/me/subs").json(), [], "subscriptions are per account")

        def feed(cid):
            return {UC1: ("One", [{"id": "aaaaaaaaaaa", "published": 10, "type": "video"}]),
                    UC2: ("Two", [{"id": "bbbbbbbbbbb", "published": 20, "type": "video"}])}[cid]

        with mock.patch.object(yt, "channel_feed", side_effect=feed):
            r = c.get("/api/yt/feed").json()
        self.assertEqual([i["id"] for i in r["items"]], ["bbbbbbbbbbb", "aaaaaaaaaaa"], "newest first")
        c.delete(f"/api/me/subs/{UC2}", headers=H)
        self.assertEqual(len(c.get("/api/me/subs").json()), 1)

    def test_a_failing_channel_doesnt_sink_the_feed(self):
        def feed(cid):
            if cid == UC2:
                raise HTTPException(502, "boom")
            return ("One", [{"id": "aaaaaaaaaaa", "published": 1, "type": "video"}])

        with mock.patch.object(yt, "channel_feed", side_effect=feed):
            items, failed, _ = yt.merged_feed([UC1, UC2])
        self.assertEqual(([i["id"] for i in items], failed), (["aaaaaaaaaaa"], [UC2]))

    def test_history_and_continue_watching(self):
        c = signed_in("watcher")
        card = {"id": "ccccccccccc", "title": "Half watched", "channel": "Chan", "channel_id": UC1, "duration": 600}
        c.post("/api/me/history", json={"video": card, "position": 120}, headers=H)
        c.post("/api/me/history", json={"video": {**card, "id": "ddddddddddd"}, "position": 595}, headers=H)
        self.assertEqual([i["id"] for i in c.get("/api/me/history?unfinished=true").json()["items"]], ["ccccccccccc"])
        self.assertEqual(len(c.get("/api/me/history").json()["items"]), 2)
        self.assertEqual(c.post("/api/me/history", json={"video": {**card, "id": "<script>"}, "position": 1}, headers=H).status_code, 422)
        # search results and cards carry where you left off
        self.assertEqual(yt.mark([{"type": "video", "id": "ccccccccccc"}], core.one("SELECT id FROM users WHERE name = 'watcher'")["id"])[0]["position"], 120)
        c.delete("/api/me/history/ccccccccccc", headers=H)
        self.assertEqual([i["id"] for i in c.get("/api/me/history").json()["items"]], ["ddddddddddd"])
        c.delete("/api/me/history", headers=H)
        self.assertEqual(c.get("/api/me/history").json()["items"], [])

    def test_watch_later(self):
        c = signed_in("watcher")
        c.post("/api/me/later", json={"id": "eeeeeeeeeee", "title": "Later"}, headers=H)
        self.assertEqual([i["id"] for i in c.get("/api/me/later").json()["items"]], ["eeeeeeeeeee"])
        c.delete("/api/me/later/eeeeeeeeeee", headers=H)
        self.assertEqual(c.get("/api/me/later").json()["items"], [])

    def test_recommended_without_cookies(self):
        c = signed_in("recs")
        for vid, chan in [("fffffffffff", UC1), ("ggggggggggg", UC1), ("hhhhhhhhhhh", UC2)]:
            c.post("/api/me/history", json={"video": {"id": vid, "channel_id": chan}, "position": 5}, headers=H)

        def feed(cid):
            return ("x", [{"id": cid[2:13], "published": 2, "channel_id": cid, "type": "video"},
                          {"id": cid[2:12] + "z", "published": 1, "channel_id": cid, "type": "video"},
                          {"id": "fffffffffff", "published": 3, "channel_id": cid, "type": "video"}])

        with mock.patch.object(yt, "channel_feed", side_effect=feed):
            r = c.get("/api/yt/recommended").json()
        ids = [i["id"] for i in r["items"]]
        self.assertEqual(r["source"], "local")
        self.assertNotIn("fffffffffff", ids, "already watched")
        self.assertEqual(ids[:2], [UC1[2:13], UC2[2:13]], "channels take turns")

    def test_cookies(self):
        c = signed_in("cookie monster")
        self.assertEqual(c.put("/api/me/youtube-cookies", json={"text": "not cookies"}, headers=H).status_code, 422)
        good = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tsecret\n.google.com\tTRUE\t/\tTRUE\t1999999999\tX\ty\n"
        self.assertEqual(c.put("/api/me/youtube-cookies", json={"text": good}, headers=H).json()["cookies"], 1)
        me = c.get("/api/me").json()
        path = yt.cookie_path(me["id"])
        self.assertTrue(me["youtube_cookies"])
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertNotIn("google.com", path.read_text(), "only youtube.com cookies are kept")
        c.delete("/api/me/youtube-cookies", headers=H)
        self.assertFalse(path.exists())


class CookieCache(unittest.TestCase):
    def test_info_fetched_with_cookies_stays_with_that_person(self):
        calls = []

        def extract(url, user_id=None, report=None, **extra):
            calls.append(user_id)
            report["cookies"] = user_id == 1  # pretend person 1 needed their cookies
            return {"id": "nnnnnnnnnnn", "who": user_id}

        yt._info.data.clear()
        with mock.patch.object(yt, "extract", side_effect=extract):
            self.assertEqual(yt.video_info("nnnnnnnnnnn", 1)["who"], 1)
            self.assertEqual(yt.video_info("nnnnnnnnnnn", 2)["who"], 2, "person 2 doesn't get person 1's copy")
            self.assertEqual(yt.video_info("nnnnnnnnnnn", 3)["who"], 2, "an ordinary lookup is shared")
        self.assertEqual(calls, [1, 2])


class Library(unittest.TestCase):
    def test_play_with_ranges_and_delete(self):
        owner, other, admin = signed_in("owner"), signed_in("other"), signed_in("admin")
        folder = core.VIDEOS / "Chan"
        folder.mkdir(exist_ok=True)
        (folder / "Clip [iiiiiiiiiii].mp4").write_bytes(b"0123456789")
        (folder / "Clip [iiiiiiiiiii].jpg").write_bytes(b"jpg")
        owner_id = owner.get("/api/me").json()["id"]
        core.db().execute("INSERT INTO videos(id, item, path, size, quality, user_id, added) VALUES (?, ?, ?, ?, ?, ?, 1)",
                          ("iiiiiiiiiii", '{"title": "Clip"}', "Chan/Clip [iiiiiiiiiii].mp4", 10, "1080", owner_id))
        r = other.get("/api/yt/file/iiiiiiiiiii", headers={"Range": "bytes=2-5"})
        self.assertEqual((r.status_code, r.content, r.headers["content-range"]), (206, b"2345", "bytes 2-5/10"))
        self.assertEqual(other.get("/api/yt/file/iiiiiiiiiii").content, b"0123456789")
        self.assertEqual(other.get("/api/yt/file/iiiiiiiiiii/thumb").content, b"jpg")
        lib = other.get("/api/yt/library").json()
        self.assertEqual([(i["id"], i["mine"]) for i in lib["items"]], [("iiiiiiiiiii", False)])
        self.assertEqual(other.delete("/api/yt/library/iiiiiiiiiii", headers=H).status_code, 403)
        self.assertEqual(owner.delete("/api/yt/library/iiiiiiiiiii", headers=H).status_code, 200)
        self.assertFalse(folder.exists(), "the video, its thumbnail and the emptied folder are gone")
        self.assertEqual(admin.get("/api/yt/file/iiiiiiiiiii").status_code, 404)

    def test_a_row_cant_point_outside_the_folder(self):
        core.db().execute("INSERT OR REPLACE INTO videos(id, item, path, added) VALUES ('jjjjjjjjjjj', '{}', '../data/yeerr.db', 1)")
        self.assertEqual(signed_in("admin").get("/api/yt/file/jjjjjjjjjjj").status_code, 404)


class Downloads(unittest.TestCase):
    def test_video_options(self):
        args = yt.download_args({"kind": "video", "quality": "1080", "sponsorblock": True})
        fmt = args[args.index("-f") + 1]
        self.assertTrue(fmt.startswith("bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]"), fmt)
        self.assertIn("--embed-chapters", args)
        self.assertEqual(args[args.index("--merge-output-format") + 1], "mp4")
        self.assertIn("sponsor,selfpromo,interaction", args)
        self.assertTrue(args[args.index("-o") + 1].startswith(str(core.VIDEOS)))
        best = yt.download_args({"kind": "video", "quality": "best", "sponsorblock": False})
        self.assertEqual(best[best.index("-f") + 1], "bv*[height<=4320]+ba/b")
        import yt_dlp

        self.assertTrue(yt_dlp.parse_options(args).ydl_opts["postprocessors"], "yt-dlp accepts them")

    def test_music_options_go_to_the_music_library(self):
        with mock.patch.dict(os.environ, {"FORMAT": "m4a", "BITRATE": "disable"}):
            args = yt.download_args({"kind": "music", "quality": "best"})
        self.assertEqual(args[args.index("--audio-format") + 1], "m4a")
        self.assertEqual(args[args.index("--audio-quality") + 1], "0")
        self.assertTrue(args[args.index("-o") + 1].startswith(str(Path(os.environ["MUSIC_DIR"]))))
        import yt_dlp

        yt_dlp.parse_options(args)  # raises if yt-dlp rejects any of them

    def test_queueing(self):
        c = signed_in("admin")
        self.assertEqual(c.post("/api/yt/downloads", json={"id": "bad"}, headers=H).status_code, 422)
        self.assertEqual(c.post("/api/yt/downloads", json={"id": "kkkkkkkkkkk", "quality": "9000"}, headers=H).status_code, 422)
        with mock.patch.object(yt._pending, "put"):
            a = c.post("/api/yt/downloads", json={"id": "kkkkkkkkkkk"}, headers=H).json()
            b = c.post("/api/yt/downloads", json={"id": "kkkkkkkkkkk"}, headers=H).json()
            self.assertEqual(a["job"], b["job"], "the same video isn't queued twice")
            self.assertTrue(yt.STATE().is_file(), "the queue survives restarts")
            self.assertEqual(c.delete(f"/api/yt/downloads/{a['job']}", headers=H).json(), {"ok": True})
        self.assertEqual(c.get("/api/yt/downloads").json(), [])

    def test_rate_limited_downloads_wait_and_retry(self):
        limited = yt.yt_dlp.utils.DownloadError("ERROR: [youtube] kkkkkkkkkkk: This content isn't available, try again later. "
                                                "The current session has been rate-limited by YouTube for up to an hour.")
        job = {"job": "rl", "id": "kkkkkkkkkkk", "kind": "video", "quality": "720", "sponsorblock": False, "status": "queued",
               "stop": False, "title": "", "channel": "", "user_id": None}
        with mock.patch.object(yt.yt_dlp, "YoutubeDL") as ydl, mock.patch.object(yt.threading, "Timer") as timer, \
                mock.patch.object(yt.yt_dlp, "parse_options", return_value=mock.Mock(ydl_opts={})):
            ydl.return_value.__enter__.return_value.extract_info.side_effect = limited
            yt.run_download(job)
        self.assertEqual(job["status"], "waiting")
        self.assertIn("rate-limiting", job["error"])
        delay, again = timer.call_args[0]
        self.assertEqual(delay, yt.LIMIT_WAIT)
        with mock.patch.object(yt._pending, "put") as put:
            again()
        self.assertEqual(job["status"], "queued")
        put.assert_called_once_with(job)
        # looking a video up while limited says so plainly
        with mock.patch.object(yt.yt_dlp, "YoutubeDL") as ydl:
            ydl.return_value.__enter__.return_value.extract_info.side_effect = limited
            with self.assertRaises(HTTPException) as e:
                yt.extract("https://www.youtube.com/watch?v=kkkkkkkkkkk")
        self.assertEqual(e.exception.status_code, 429)

    def test_restore_resumes_running_jobs(self):
        yt.STATE().write_text('[{"job": "j1", "id": "lllllllllll", "status": "downloading"}, {"job": "j2", "id": "mmmmmmmmmmm", "status": "done"}, "junk"]')
        with mock.patch.object(yt._pending, "put") as put:
            yt.restore_jobs()
        self.assertEqual([j["job"] for j in yt.jobs], ["j1", "j2"])
        self.assertEqual(yt.jobs[0]["status"], "queued")
        put.assert_called_once()
        yt.jobs.clear()


if __name__ == "__main__":
    unittest.main()
