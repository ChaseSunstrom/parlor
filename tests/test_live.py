"""Live streams, against stand-ins for YouTube's and Twitch's servers (made-up addresses and playlists shaped like
theirs): every address in a playlist goes through Parlor, signed, so Parlor isn't an open proxy; Twitch's ad
segments become gaps and its ad markers go, so no ad reaches the browser; YouTube lives play through Parlor, and a
stale address gets one fresh look; Twitch channels are followed per person, who's live is asked once a minute; chat
lines are read from Twitch's IRC format."""
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import core
import live
from yeerr_test_env import client, signed_in

TW_MASTER = """#EXTM3U
#EXT-X-TWITCH-INFO:NODE="video-edge",CLUSTER="xyz"
#EXT-X-MEDIA:TYPE=VIDEO,GROUP-ID="chunked",NAME="1080p60 (source)",AUTOSELECT=YES,DEFAULT=YES
#EXT-X-STREAM-INF:BANDWIDTH=8000000,RESOLUTION=1920x1080,CODECS="avc1.64002A,mp4a.40.2",VIDEO="chunked",FRAME-RATE=60.000
https://video-weaver.example.hls.ttvnw.net/v1/playlist/AAAA.m3u8
"""
TW_MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:500
#EXT-X-DATERANGE:ID="stitched-ad-1",CLASS="twitch-stitched-ad",START-DATE="2026-11-08T00:00:00Z",DURATION=30.0
#EXTINF:2.000,Amazon|123456
https://video-edge-ad.example.abs.hls.ttvnw.net/ad-1.ts
#EXTINF:2.000,Amazon|123456
https://video-edge-ad.example.abs.hls.ttvnw.net/ad-2.ts
#EXTINF:2.000,live
https://video-edge-1.example.abs.hls.ttvnw.net/seg-502.ts
#EXT-X-TWITCH-PREFETCH:https://video-edge-1.example.abs.hls.ttvnw.net/seg-503.ts
"""
YT_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000,CODECS="avc1.4d401f,mp4a.40.2",RESOLUTION=1280x720
https://manifest.example.googlevideo.com/api/manifest/hls_playlist/id/x/720.m3u8
"""
YT_MEDIA = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:76973
#EXTINF:5.0,
https://rr1---sn-example.googlevideo.com/videoplayback/sq/76973/seg.ts
#EXTINF:5.0,
seg-76974.ts
"""
STALE = {"yes": True}
ASKED: list = []


class Upstream(BaseHTTPRequestHandler):
    """stands in for gql.twitch.tv, usher, the video edges, and YouTube's manifest server (addresses mapped below)"""

    def log_message(self, *a):
        pass

    def reply(self, code, body, kind="application/vnd.apple.mpegurl"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        ASKED.append(u.path)
        if u.path == "/api/channel/hls/somestreamer.m3u8":
            return self.reply(200, TW_MASTER)
        if u.path == "/api/channel/hls/sleepy.m3u8":
            return self.reply(404, "[]", "application/json")
        if u.path == "/vod/77.m3u8":
            return self.reply(200, TW_MASTER)
        if u.path.endswith("/AAAA.m3u8"):
            return self.reply(200, TW_MEDIA)
        if u.path.endswith("/live.m3u8"):
            return self.reply(403 if STALE["yes"] else 200, YT_MASTER)
        if u.path.endswith("/720.m3u8"):
            return self.reply(200, YT_MEDIA)
        if u.path.endswith(".ts"):
            return self.reply(200, b"\x47" * 188, "video/mp2t")
        if u.path.endswith(".mp4"):
            return self.reply(200, b"\0\0\0\x18ftypmp42", "binary/octet-stream")
        self.reply(404, "no")

    def do_POST(self):
        import json

        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        q, v = body["query"], body["variables"]
        ASKED.append("gql:" + q.split("(", 1)[0].strip()[:40])
        if "clip(slug" in q:
            data = {"clip": {"title": "Nice", "durationSeconds": 9, "broadcaster": {"login": "somestreamer", "displayName": "SomeStreamer"},
                             "playbackAccessToken": {"signature": "sig", "value": "{}"},
                             "videoQualities": [{"quality": "480", "sourceURL": "https://production.assets.clips.twitchcdn.net/a/480.mp4"},
                                                {"quality": "1080", "sourceURL": "https://production.assets.clips.twitchcdn.net/a/1080.mp4"}]}} if v["s"] == "Nice-clip" else {"clip": None}
        elif "user(login" in q:
            data = {"user": {"login": v["login"], "displayName": "SomeStreamer", "description": "", "profileImageURL": "https://evil.example/x.png", "stream": None,
                             "videos": {"edges": [{"node": {"id": "77", "title": "Yesterday", "lengthSeconds": 60, "publishedAt": "2026-11-07T00:00:00Z", "viewCount": 3,
                                                             "previewThumbnailURL": "https://static-cdn.jtvnw.net/v.jpg"}}]},
                             "clips": {"edges": []}}} if v["login"] == "somestreamer" else {"user": None}
        elif "searchFor" in q:
            data = {"searchFor": {"channels": {"edges": [{"item": {"login": "somestreamer", "displayName": "SomeStreamer", "stream": None}}, {"item": {}}]}}}
        elif "PlaybackAccessToken" in q:
            data = ({"streamPlaybackAccessToken": {"value": '{"channel":"x"}', "signature": "abc"}} if v.get("login") != "nobody" else {"streamPlaybackAccessToken": None}) | (
                {"videoPlaybackAccessToken": {"value": "{}", "signature": "abc"}} if v.get("vod") else {})
        elif "users(logins" in q:
            data = {"users": [{"login": l, "displayName": l.title(), "profileImageURL": "https://static-cdn.jtvnw.net/x.png",
                               "stream": {"title": "On air", "viewersCount": 42, "createdAt": "2026-11-08T00:00:00Z", "game": {"name": "Chess"},
                                          "previewImageURL": "https://static-cdn.jtvnw.net/p.jpg"} if l == "somestreamer" else None}
                              if l in ("somestreamer", "sleepy") else None for l in v["l"]]}
        else:
            data = {}
        self.reply(200, json.dumps({"data": data}), "application/json")


SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{SERVER.server_port}"
_real_get = live.requests.get


def routed_get(url, **kw):
    """the made-up https addresses, answered by the stand-in"""
    p = urlparse(url)
    return _real_get(f"{BASE}{p.path}" + (f"?{p.query}" if p.query else ""), **kw)


_real_post = live.requests.post


def routed_post(url, **kw):
    return _real_post(f"{BASE}/gql", **kw)


def parlor_addresses(text):
    return [l for l in text.splitlines() if l and not l.startswith("#")]


class Playlists(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(live.requests, "get", routed_get), mock.patch.object(live.requests, "post", routed_post)]
        for p in self.patches:
            p.start()
        live._live_cache.clear()
        core.db().execute("DELETE FROM twitch_follows")
        self.me = signed_in("viewer")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_every_address_goes_through_parlor_signed(self):
        text = live.rewrite(YT_MEDIA, "https://manifest.example.googlevideo.com/api/manifest/hls_playlist/id/x/720.m3u8")
        lines = parlor_addresses(text)
        self.assertTrue(all(l.startswith("/api/live/seg?u=") for l in lines), lines)
        self.assertIn("manifest.example.googlevideo.com%2Fapi%2Fmanifest%2Fhls_playlist%2Fid%2Fx%2Fseg-76974.ts", lines[1], "relative ones too")
        got = self.me.get(lines[0])
        self.assertEqual((got.status_code, got.content[:1]), (200, b"\x47"))
        forged = lines[0].replace("sq%2F76973", "sq%2F99999")
        self.assertEqual(self.me.get(forged).status_code, 404, "a changed address isn't signed")
        other = live.via("seg", "https://example.org/x.ts")
        self.assertEqual(self.me.get(other).status_code, 404, "only YouTube's and Twitch's servers, even when signed")
        self.assertEqual(client().get(lines[0]).status_code, 401)

    def test_twitch_ads_never_reach_the_browser(self):
        text = live.rewrite(TW_MEDIA, "https://video-weaver.example.hls.ttvnw.net/v1/playlist/AAAA.m3u8")
        self.assertNotIn("stitched-ad", text)
        self.assertNotIn("PREFETCH", text, "no address bypasses the filter")
        self.assertNotIn("Amazon", "".join(l for l in text.splitlines() if l.startswith("/")))
        self.assertEqual(text.count("#EXT-X-GAP"), 2, "both ad segments are gaps: the player holds instead")
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:500", text, "numbering kept, so the live segments stay in place")
        lines = text.splitlines()
        i = lines.index("#EXTINF:2.000,live")
        self.assertTrue(lines[i + 1].startswith("/api/live/seg?u="), "the live segment plays")

    def test_twitch_live_through_parlor(self):
        r = self.me.get("/api/live/tw/SomeStreamer.m3u8")
        self.assertEqual(r.status_code, 200, r.text)
        variant = parlor_addresses(r.text)[0]
        self.assertTrue(variant.startswith("/api/live/pl?u="))
        media = self.me.get(variant).text
        self.assertEqual(media.count("#EXT-X-GAP"), 2)
        self.assertEqual(self.me.get("/api/live/tw/sleepy.m3u8").status_code, 409, "offline says so")
        self.assertEqual(self.me.get("/api/live/tw/nobody.m3u8").status_code, 404)
        self.assertEqual(self.me.get("/api/live/tw/..%2Fx.m3u8").status_code, 404)

    def test_youtube_live_gets_one_fresh_look(self):
        info = {"live_status": "is_live", "formats": [{"manifest_url": "https://manifest.example.googlevideo.com/api/manifest/hls_variant/live.m3u8"}]}
        calls = []

        def video_info(vid, uid, fresh=False):
            calls.append(fresh)
            STALE["yes"] = not fresh  # the cached address has run out; a fresh one works
            return info

        with mock.patch.object(live.yt, "video_info", video_info):
            r = self.me.get("/api/live/yt/abcdefghijk.m3u8")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(calls, [False, True])
        self.assertTrue(parlor_addresses(r.text)[0].startswith("/api/live/pl?u="))
        with mock.patch.object(live.yt, "video_info", lambda *a, **k: {"live_status": "is_upcoming"}):
            self.assertEqual(self.me.get("/api/live/yt/abcdefghijk.m3u8").status_code, 409)
        self.assertEqual(self.me.get("/api/live/yt/bad.m3u8").status_code, 404)

    def test_follows_are_each_persons_and_live_ones_come_first(self):
        self.assertEqual(self.me.post("/api/live/tw/follows", json={"login": "https://www.twitch.tv/Sleepy"}, headers=env.H).status_code, 200)
        self.assertEqual(self.me.post("/api/live/tw/follows", json={"login": "somestreamer"}, headers=env.H).status_code, 200)
        self.assertEqual(self.me.post("/api/live/tw/follows", json={"login": "nobody"}, headers=env.H).status_code, 404)
        self.assertEqual(self.me.post("/api/live/tw/follows", json={"login": "bad name!"}, headers=env.H).status_code, 422)
        chans = self.me.get("/api/live/tw/follows").json()["channels"]
        self.assertEqual([c["login"] for c in chans], ["somestreamer", "sleepy"], "live first")
        self.assertEqual(chans[0]["live"]["viewers"], 42)
        self.assertTrue(chans[0]["avatar"].startswith("/api/live/seg?u="), "pictures through Parlor too")
        n = ASKED.count("gql:query")
        self.me.get("/api/live/tw/follows")
        self.assertEqual(ASKED.count("gql:query"), n, "who's live is asked at most once a minute")
        live._live_cache.clear()
        self.me.get("/api/live/tw/follows")
        self.assertGreater(ASKED.count("gql:query"), n, "and asked again once that's run out")
        self.assertEqual(signed_in("viewer2").get("/api/live/tw/follows").json()["channels"], [], "someone else's follows aren't theirs")
        got = self.me.post("/api/live/tw/follows/import", json={"logins": "login\ntwitch.tv/somestreamer, m.twitch.tv/sleepy/videos\nnobody bad!name"},
                           headers=env.H).json()
        self.assertEqual((got["added"], got["unknown"]), (["sleepy", "somestreamer"], ["nobody", "bad!name"]), "one bad name doesn't stop the rest")
        self.me.delete("/api/live/tw/follows/sleepy", headers=env.H)
        self.assertEqual([c["login"] for c in self.me.get("/api/live/tw/follows").json()["channels"]], ["somestreamer"])

    def test_ads_get_no_address_at_all_and_odd_signatures_are_refused(self):
        text = live.rewrite(TW_MEDIA, "https://video-weaver.example.hls.ttvnw.net/v1/playlist/AAAA.m3u8")
        self.assertNotIn("video-edge-ad", text.replace("%2F", "/").replace("%3A", ":"), "not even a signed address of an ad")
        self.assertIn("#EXT-X-VERSION:8", text, "gaps are HLS version 8")
        self.assertEqual(self.me.get("/api/live/gap.ts").status_code, 404)
        self.assertEqual(self.me.get("/api/live/seg?u=https%3A%2F%2Fa.googlevideo.com%2Fx&s=%C3%A9").status_code, 404, "not a 500")
        heading = '#EXT-X-DATERANGE:ID="heading",CLASS="chapter",START-DATE="2026-11-08T00:00:00Z"'
        self.assertIn(heading, live.rewrite(heading + "\n", "https://x.ttvnw.net/p.m3u8"), "only Twitch's ad markers go")

    def test_past_broadcasts_clips_channels_and_search(self):
        vod = self.me.get("/api/live/tw/vod/77.m3u8")
        self.assertEqual(vod.status_code, 200)
        self.assertTrue(parlor_addresses(vod.text)[0].startswith("/api/live/pl?u="))
        self.assertEqual(self.me.get("/api/live/tw/vod/abc.m3u8").status_code, 404)
        clip = self.me.get("/api/live/tw/clip/Nice-clip").json()
        self.assertEqual(self.me.get(clip["src"]).headers["content-type"], "video/mp4", "Twitch's octet-stream named for what it is")
        self.assertIn("1080.mp4", clip["src"].replace("%2F", "/"), "the best quality")
        self.assertIn("twitchcdn.net", clip["src"].replace("%2F", "/"))
        self.assertEqual(self.me.get("/api/live/tw/clip/Gone").status_code, 404)
        ch = self.me.get("/api/live/tw/channel/somestreamer").json()
        self.assertIsNone(ch["avatar"], "a picture from anywhere but Twitch's servers isn't passed on")
        self.assertEqual([v["id"] for v in ch["videos"]], ["77"])
        self.assertEqual(self.me.get("/api/live/tw/channel/nobody").status_code, 404)
        self.assertEqual([c["login"] for c in self.me.get("/api/live/tw/search?q=some").json()["channels"]], ["somestreamer"])

    def test_live_now_reads_each_channels_live_page(self):
        pages = {"UCaaaaaaaaaaaaaaaaaaaaaa": '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk"><meta name="title" content="Night &amp; rain">"isLive":true',
                 "UCbbbbbbbbbbbbbbbbbbbbbb": '<link rel="canonical" href="https://www.youtube.com/watch?v=bcdefghijkl">"scheduledStartTime":"1794200000"',
                 "UCcccccccccccccccccccccc": '<link rel="canonical" href="https://www.youtube.com/channel/UCcccccccccccccccccccccc">'}
        uid = core.one("SELECT id FROM users WHERE name = 'viewer'")["id"]
        core.db().execute("DELETE FROM subs WHERE user_id = ?", (uid,))
        for cid in pages:
            core.db().execute("INSERT INTO subs(user_id, channel_id, name, added) VALUES (?, ?, ?, 0)", (uid, cid, "Chan " + cid[2]))
        live._yt_live.clear()
        answer = lambda url, **kw: mock.Mock(status_code=200, text=pages[url.split("/channel/")[1].split("/")[0]])
        with mock.patch.object(live.requests, "get", answer):
            got = self.me.get("/api/live/now").json()
        self.assertEqual([(v["id"], v["title"], v["channel"]) for v in got["youtube"]], [("abcdefghijk", "Night & rain", "Chan a")])
        self.assertEqual([(v["id"], v["published"]) for v in got["upcoming"]], [("bcdefghijkl", 1794200000)])
        with mock.patch.object(live.requests, "get", lambda *a, **k: self.fail("asked again within five minutes")):
            self.assertEqual(len(self.me.get("/api/live/now").json()["youtube"]), 1)
        # the Subscriptions feed marks what Live now found, without asking YouTube anything more
        uploads = {cid: ("Chan " + cid[2], [{"type": "video", "id": vid, "title": "", "channel_id": cid, "published": n, "live": None}
                                            for n, vid in enumerate((own, cid[2] * 11))])
                   for cid, own in zip(pages, ("abcdefghijk", "bcdefghijkl", "cdefghijklm"))}
        with mock.patch.object(live.yt, "channel_feed", lambda cid: uploads[cid]), \
                mock.patch.object(live.requests, "get", lambda *a, **k: self.fail("the feed asked YouTube")):
            marked = {v["id"]: v["live"] for v in self.me.get("/api/yt/feed").json()["items"]}
        self.assertEqual(marked["abcdefghijk"], "is_live")
        self.assertEqual(marked["bcdefghijkl"], "is_upcoming")
        self.assertEqual([marked[v] for v in ("cdefghijklm", "a" * 11, "b" * 11, "c" * 11)], [None] * 4)
        self.assertIsNone(uploads["UCaaaaaaaaaaaaaaaaaaaaaa"][1][0]["live"], "the channel's cached feed is left as it was")

    def test_chat_lines(self):
        line = "@badge-info=;color=#1E90FF;display-name=Viewer_1;emotes= :viewer_1!viewer_1@viewer_1.tmi.twitch.tv PRIVMSG #somestreamer :hello <b>there</b>"
        self.assertEqual(live._irc(line), {"name": "Viewer_1", "color": "#1E90FF", "text": "hello <b>there</b>"})
        self.assertIsNone(live._irc(":tmi.twitch.tv 001 justinfan123 :Welcome"))
        self.assertEqual(live._irc("@color=red;display-name=x :x!x@x PRIVMSG #a :hi")["color"], "", "only plain colours")

    def test_only_people_with_watch(self):
        core.db().execute("UPDATE users SET blocked = 'watch' WHERE name = 'viewer'")
        try:
            self.assertEqual(self.me.get("/api/live/tw/follows").status_code, 403)
        finally:
            core.db().execute("UPDATE users SET blocked = '' WHERE name = 'viewer'")


if __name__ == "__main__":
    unittest.main()
