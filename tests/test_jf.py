"""Jellyfin playback: browsing, who plays as whom, playback info, progress and the stream proxy,
all against a fake Jellyfin (requests is patched; nothing leaves the machine)."""
import re
import unittest
from unittest import mock
from urllib.parse import urlsplit

from requests.structures import CaseInsensitiveDict

import yeerr_test_env as env
from yeerr_test_env import H, client, signed_in

import core
import jf

KEY = "SECRETKEY0123"  # pragma: allowlist secret
BASE = "http://jf.test:8096"
T = 10**7  # ticks per second


def hid(n):
    return f"{n:032x}"


ALICE, BOB, FAMILY = hid(0xA), hid(0xB), hid(0xF)
USERS = [{"Id": ALICE, "Name": "alice"}, {"Id": BOB, "Name": "Bob"}, {"Id": FAMILY, "Name": "family"}]
VIEWS, MOVIE, SERIES, SEASON, EP, EP2, ALBUM, TRACK = (hid(n) for n in (0x20, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16))
MS = hid(0x99)
MOVIE_JSON = {"Id": MOVIE, "Name": "Heat", "Type": "Movie", "ProductionYear": 1995, "Overview": "Cops and robbers.",
              "RunTimeTicks": 170 * 60 * T, "ImageTags": {"Primary": "tagP"}, "BackdropImageTags": ["tagB"],
              "Path": "/secret/media/heat.mkv", "Genres": ["Crime"], "CommunityRating": 8.3, "OfficialRating": "R",
              "UserData": {"Played": False, "PlaybackPositionTicks": 600 * T, "IsFavorite": True},
              "People": [{"Name": "Al Pacino", "Role": "Vincent", "Type": "Actor"}],
              "Chapters": [{"StartPositionTicks": 0, "Name": "Start"}, {"StartPositionTicks": 300 * T, "Name": "Heist"}]}
EP_JSON = {"Id": EP, "Name": "Pilot", "Type": "Episode", "SeriesName": "Show", "SeriesId": SERIES, "SeasonId": SEASON,
           "ParentIndexNumber": 1, "IndexNumber": 2, "SeriesPrimaryImageTag": "sp", "ImageTags": {},
           "RunTimeTicks": 40 * 60 * T, "UserData": {"Played": True, "PlaybackPositionTicks": 0}}
TRACK_JSON = {"Id": TRACK, "Name": "Song", "Type": "Audio", "Artists": ["Band"], "Album": "Record", "AlbumId": ALBUM,
              "AlbumPrimaryImageTag": "ap", "IndexNumber": 3, "RunTimeTicks": 200 * T, "UserData": {}}
ITEMS = {MOVIE: MOVIE_JSON, EP: EP_JSON, TRACK: TRACK_JSON,
         SERIES: {"Id": SERIES, "Name": "Show", "Type": "Series", "ImageTags": {"Primary": "t"}, "UserData": {}},
         SEASON: {"Id": SEASON, "Name": "Season 1", "Type": "Season", "SeriesId": SERIES, "IndexNumber": 1, "UserData": {}},
         ALBUM: {"Id": ALBUM, "Name": "Record", "Type": "MusicAlbum", "AlbumArtist": "Band", "UserData": {}},
         EP2: {**EP_JSON, "Id": EP2}}


class Resp:
    def __init__(self, status=200, data=None, body=None, headers=None):
        self.status_code, self.headers = status, CaseInsensitiveDict(headers or {})
        self._data = data
        self.content = body if body is not None else (b"" if data is None else b"{}")
        self.closed = False

    ok = property(lambda s: s.status_code < 400)
    text = property(lambda s: s.content.decode())
    json = lambda s: s._data  # noqa: E731

    def iter_content(self, n):
        yield self.content

    def close(self):
        self.closed = True


class FakeJellyfin:
    """Answers the paths yeerr uses. `old=True` plays a Jellyfin before 10.9 (no /UserItems, no /Items/{id})."""

    def __init__(self, old=False):
        self.old, self.calls = old, []

    def __call__(self, method, url, headers=None, params=None, json=None, stream=False, timeout=None, allow_redirects=True, **kw):
        assert url.startswith(BASE), url
        path = urlsplit(url).path
        q = {k.lower(): v for k, v in dict(params or []).items()}
        self.calls.append({"method": method, "path": path, "q": q, "json": json, "headers": CaseInsensitiveDict(headers or {}),
                           "params": list(params.items() if isinstance(params, dict) else params or []), "stream": stream})
        if headers.get("Authorization", "").count(KEY) != 1:
            return Resp(401)
        for verb, pattern, fn in self.routes():
            m = re.fullmatch(pattern, path)
            if m and verb == method:
                out = fn(m, q, json, headers)
                return out if isinstance(out, Resp) else Resp(200, out)
        return Resp(404, {"message": "nope"})

    def routes(self):
        uid = r"/Users/(?P<uid>\w+)"
        user_data = lambda m, q, body, h: {"Played": body.get("Played"), "PlaybackPositionTicks": body.get("PlaybackPositionTicks")}  # noqa: E731
        item = lambda m, q, b, h: ITEMS.get(m["id"]) or Resp(404)  # noqa: E731
        listing = lambda items: (lambda m, q, b, h: {"Items": items, "TotalRecordCount": 7})  # noqa: E731
        routes = [
            ("GET", "/System/Info", lambda *a: {"ServerName": "Fake", "Version": "10.10.3"}),
            ("GET", "/Users", lambda *a: USERS),
            ("GET", "/UserViews", lambda m, q, b, h: {"Items": [{"Id": VIEWS, "Name": "Movies", "Type": "CollectionFolder", "CollectionType": "movies"}]}),
            ("GET", uid + "/Views", lambda m, q, b, h: {"Items": [{"Id": VIEWS, "Name": "OldMovies", "Type": "CollectionFolder"}]}),
            ("GET", "/Items", lambda m, q, b, h: {"Items": [TRACK_JSON] if q.get("includeitemtypes") == "Audio" else [MOVIE_JSON, EP_JSON],
                                                  "TotalRecordCount": 7}),
            ("GET", "/Items/Latest", lambda *a: [MOVIE_JSON]),
            ("GET", uid + "/Items/Latest", lambda *a: [MOVIE_JSON]),
            ("GET", "/UserItems/Resume", listing([MOVIE_JSON])),
            ("GET", uid + "/Items/Resume", listing([MOVIE_JSON])),
            ("GET", "/Shows/NextUp", listing([EP_JSON])),
            ("GET", "/Items/(?P<id>\\w+)", item),
            ("GET", uid + "/Items/(?P<id>\\w+)", item),
            ("GET", "/Items/\\w+/Similar", listing([TRACK_JSON])),
            ("GET", "/Shows/\\w+/Seasons", listing([ITEMS[SEASON]])),
            ("GET", "/Shows/\\w+/Episodes", listing([EP_JSON])),
            ("POST", "/Items/(?P<id>\\w+)/PlaybackInfo", self.playback),
            ("POST", "/UserItems/(?P<id>\\w+)/UserData", user_data),
            ("POST", uid + "/Items/(?P<id>\\w+)/UserData", user_data),
            ("GET", "/(?:Videos|Audio)/.*|/Items/\\w+/(?:Images|Download).*", self.media),
        ]
        gone = ("/UserViews", "/UserItems/Resume", "/Items/Latest", "/Items/(?P<id>\\w+)", "/UserItems/(?P<id>\\w+)/UserData")  # before 10.9
        return [r for r in routes if not (self.old and r[1] in gone)]

    def media(self, m, q, body, headers):
        path = m.string
        if path.endswith(".m3u8"):
            text = (f"#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4?api_key={KEY}&PlaySessionId=p\"\nmain.m3u8?a=1&ApiKey={KEY}&b=2\n"
                    f"hls1/main/0.mp4?api_key={KEY}\nhls1/main/1.mp4?x=1&api_key={KEY}\n")
            return Resp(200, body=text.encode(), headers={"Content-Type": "application/vnd.apple.mpegurl", "Content-Length": str(len(text))})
        if path.startswith("/Items/") and "Images" in path:
            return Resp(200, body=b"\xff\xd8img", headers={"Content-Type": "image/jpeg"})
        if headers.get("Range"):
            return Resp(206, body=b"hello", headers={"Content-Range": "bytes 5-9/100", "Content-Length": "5", "Content-Type": "video/mp4",
                                                     "Accept-Ranges": "bytes", "X-Leak": "x"})
        return Resp(200, body=b"whole", headers={"Content-Type": "video/mp4", "Content-Length": "5", "Accept-Ranges": "bytes"})

    def playback(self, m, q, body, headers):
        sub = {"Index": 3, "Type": "Subtitle", "Codec": "subrip", "Language": "eng", "DisplayTitle": "English", "IsDefault": True,
               "DeliveryMethod": "External", "DeliveryUrl": f"/Videos/{m['id']}/{MS}/Subtitles/3/0/Stream.vtt?ApiKey={KEY}"}
        pgs = {"Index": 4, "Type": "Subtitle", "Codec": "pgssub", "Language": "fre", "DeliveryMethod": "Encode"}
        streams = [{"Index": 0, "Type": "Video", "Codec": "h264"},
                   {"Index": 1, "Type": "Audio", "Codec": "aac", "Language": "eng", "DisplayTitle": "English AAC", "Channels": 6, "IsDefault": True},
                   {"Index": 2, "Type": "Audio", "Codec": "ac3", "Language": "fre", "DisplayTitle": "French AC3", "Channels": 2}, sub, pgs]
        src = {"Id": MS, "Name": "1080p", "Container": "mp4", "ETag": "etag", "RunTimeTicks": 170 * 60 * T, "MediaStreams": streams,
               "DefaultAudioStreamIndex": 1, "SupportsDirectPlay": m["id"] in (MOVIE, TRACK), "SupportsTranscoding": True}
        if m["id"] == EP:
            src["TranscodingUrl"] = (f"/videos/{EP}/master.m3u8?DeviceId=d&MediaSourceId={MS}&PlaySessionId=ps2&api_key={KEY}"
                                     "&VideoCodec=h264&AudioCodec=aac")
        if m["id"] == EP2:
            return {"MediaSources": [src], "ErrorCode": "NoCompatibleStream"}
        return {"MediaSources": [src], "PlaySessionId": "ps1"}

    def last(self, path):
        return next(c for c in reversed(self.calls) if c["path"] == path)

    def paths(self):
        return [c["path"] for c in self.calls]


class Base(unittest.TestCase):
    old = False

    def setUp(self):
        self.admin = signed_in("admin")
        for k, v in (("jellyfin_url", BASE), ("jellyfin_key", KEY), ("jellyfin_user", "family")):
            core.set_setting(k, v)
        jf._users.data.clear()
        jf._seen.data.clear()
        self.fake = FakeJellyfin(self.old)
        patch = mock.patch.object(jf.requests, "request", self.fake)
        patch.start()
        self.addCleanup(patch.stop)

    def get(self, path, **kw):
        return self.admin.get(path, **kw)


class Settings(Base):
    def test_key_is_write_only_and_user_link_is_set_by_admin(self):
        r = self.admin.put("/api/settings", json={"jellyfin_url": BASE + "/", "jellyfin_key": "k" * 20, "jellyfin_user": "alice"}, headers=H)
        body = r.json()
        self.assertEqual((body["jellyfin_url"], body["jellyfin_user"], body["jellyfin_key_set"]), (BASE, "alice", True))
        self.assertNotIn("k" * 20, r.text)
        self.assertNotIn("k" * 20, self.admin.get("/api/settings").text)
        self.admin.put("/api/settings", json={"jellyfin_key": ""}, headers=H)
        self.assertFalse(self.admin.get("/api/settings").json()["jellyfin_key_set"])
        pal = signed_in("pal")
        pid = core.one("SELECT id FROM users WHERE name = 'pal'")["id"]
        self.assertEqual(pal.get("/api/settings").status_code, 403)
        self.assertEqual(pal.patch(f"/api/users/{pid}", json={"jf_user": "Bob"}, headers=H).status_code, 403)
        self.assertEqual(self.admin.patch(f"/api/users/{pid}", json={"jf_user": "Bob"}, headers=H).status_code, 200)
        self.assertEqual([u["jf_user"] for u in self.admin.get("/api/users").json() if u["name"] == "pal"], ["Bob"])
        self.admin.patch(f"/api/users/{pid}", json={"jf_user": ""}, headers=H)
        self.assertIsNone(core.one("SELECT jf_user FROM users WHERE id = ?", pid)["jf_user"])

    def test_old_databases_gain_the_column(self):
        cols = [r["name"] for r in core.db().execute("PRAGMA table_info(users)")]
        self.assertIn("jf_user", cols)
        core.db().execute("ALTER TABLE users DROP COLUMN jf_user")
        core._ready.clear()
        core._local.__dict__.clear()
        self.assertIn("jf_user", [r["name"] for r in core.db().execute("PRAGMA table_info(users)")])

    def test_guard_and_unconfigured(self):
        self.assertEqual(client().get("/api/jf/views").status_code, 401)
        self.assertEqual(client().get("/api/jf/stream/Items/" + MOVIE + "/Images/Primary").status_code, 401)
        core.set_setting("jellyfin_key", "")
        self.assertEqual(self.get("/api/jf/views").status_code, 503)
        self.assertEqual(self.get("/api/jf/status").json()["connected"], False)


class WhoPlaysAsWhom(Base):
    def uid(self, who):
        self.fake.calls.clear()
        self.assertEqual(who.get("/api/jf/views").status_code, 200)
        return self.fake.last("/UserViews")["q"]["userid"]

    def test_resolution_order(self):
        plain = signed_in("plain")
        self.assertEqual(self.uid(plain), FAMILY, "an unlinked account plays as the default user, found by name")
        pid = core.one("SELECT id FROM users WHERE name = 'plain'")["id"]
        core.db().execute("UPDATE users SET jf_user = 'bob' WHERE id = ?", (pid,))
        self.assertEqual(self.uid(plain), BOB, "an admin link wins over the default, names match any case")
        core.db().execute("UPDATE users SET jf_user = ? WHERE id = ?", (ALICE, pid))
        self.assertEqual(self.uid(plain), ALICE, "the link may be an id")
        core.db().execute("UPDATE users SET jellyfin_id = ? WHERE id = ?", (BOB[:8] + "-" + BOB[8:], pid))
        self.assertEqual(self.uid(plain), BOB, "signing in with Jellyfin wins over the link, dashes dropped")
        core.db().execute("UPDATE users SET jellyfin_id = NULL, jf_user = 'nobody' WHERE id = ?", (pid,))
        r = plain.get("/api/jf/views")
        self.assertEqual(r.status_code, 404)
        self.assertIn("nobody", r.json()["detail"])
        core.db().execute("UPDATE users SET jf_user = NULL WHERE id = ?", (pid,))
        core.set_setting("jellyfin_user", "")
        r = plain.get("/api/jf/views")
        self.assertEqual(r.status_code, 409)
        self.assertIn("link", r.json()["detail"].lower())

    def test_status_names_the_server_and_the_way_you_play(self):
        s = self.get("/api/jf/status").json()
        self.assertEqual((s["connected"], s["version"], s["user"]["id"], s["via"]), (True, "10.10.3", FAMILY, "default"))


class Browse(Base):
    def test_views_items_latest_resume_nextup(self):
        v = self.get("/api/jf/views").json()
        self.assertEqual((v[0]["id"], v[0]["name"], v[0]["collection"]), (VIEWS, "Movies", "movies"))
        r = self.get(f"/api/jf/items?parent={VIEWS}&types=Movie,Series&sort=SortName&order=Descending&start=20&limit=10"
                     "&search=hea&filters=IsFavorite,IsUnplayed")
        self.assertEqual(r.status_code, 200, r.text)
        q = self.fake.last("/Items")["q"]
        self.assertEqual((q["parentid"], q["includeitemtypes"], q["sortby"], q["sortorder"], q["startindex"], q["limit"], q["searchterm"],
                          q["filters"], q["recursive"], q["userid"]),
                         (VIEWS, "Movie,Series", "SortName", "Descending", 20, 10, "hea", "IsFavorite,IsUnplayed", "true", FAMILY))
        body = r.json()
        self.assertEqual((body["total"], body["start"], len(body["items"])), (7, 20, 2))
        movie = body["items"][0]
        self.assertEqual(movie, {**movie, "id": MOVIE, "type": "Movie", "name": "Heat", "year": 1995, "overview": "Cops and robbers.",
                                 "runtime": 170 * 60, "played": False, "position": 600, "favourite": True,
                                 "image": f"/api/jf/stream/Items/{MOVIE}/Images/Primary?tag=tagP&maxWidth=600",
                                 "backdrop": f"/api/jf/stream/Items/{MOVIE}/Images/Backdrop/0?tag=tagB&maxWidth=1920"})
        self.assertNotIn("/secret", r.text, "Jellyfin's own fields don't leak")
        self.assertNotIn(KEY, r.text)
        ep = body["items"][1]
        self.assertEqual((ep["series"], ep["series_id"], ep["season"], ep["episode"], ep["played"]), ("Show", SERIES, 1, 2, True))
        self.assertEqual(ep["image"], f"/api/jf/stream/Items/{SERIES}/Images/Primary?tag=sp&maxWidth=600", "an episode without a picture shows its series'")
        self.get("/api/jf/items")
        q = self.fake.last("/Items")["q"]
        self.assertNotIn("recursive", q, "Recursive only when types (or a search) say what to look for")
        self.assertEqual(self.get("/api/jf/items?types=Bogus;x").status_code, 422)
        self.assertEqual(self.get("/api/jf/items?parent=../x").status_code, 422)
        self.assertEqual(self.get("/api/jf/items?limit=100000").status_code, 422)

        self.assertEqual(self.get(f"/api/jf/latest?parent={VIEWS}").json()[0]["name"], "Heat")
        self.assertEqual(self.fake.last("/Items/Latest")["q"]["parentid"], VIEWS)
        self.assertEqual(self.get("/api/jf/resume").json()[0]["position"], 600)
        self.assertEqual(self.get("/api/jf/nextup").json()[0]["name"], "Pilot")
        self.assertEqual(self.fake.last("/Shows/NextUp")["q"]["userid"], FAMILY)

    def test_older_jellyfin_paths(self):
        self.fake.old = True
        self.assertEqual(self.get("/api/jf/views").json()[0]["name"], "OldMovies")
        self.assertEqual(self.get(f"/api/jf/item/{MOVIE}").json()["name"], "Heat")
        self.assertEqual(self.get("/api/jf/resume").json()[0]["position"], 600)

    def test_item_detail(self):
        d = self.get(f"/api/jf/item/{MOVIE}").json()
        self.assertEqual((d["name"], d["genres"], d["rating"], d["certification"]), ("Heat", ["Crime"], 8.3, "R"))
        self.assertEqual(d["people"], [{"name": "Al Pacino", "role": "Vincent"}])
        self.assertEqual(d["similar"][0]["name"], "Song")
        self.assertNotIn("seasons", d)
        s = self.get(f"/api/jf/item/{SERIES}").json()
        self.assertEqual(s["seasons"][0]["name"], "Season 1")
        se = self.get(f"/api/jf/item/{SEASON}").json()
        self.assertEqual(se["episodes"][0]["name"], "Pilot")
        self.assertEqual(self.fake.last(f"/Shows/{SERIES}/Episodes")["q"]["seasonid"], SEASON)
        a = self.get(f"/api/jf/item/{ALBUM}").json()
        self.assertEqual(a["tracks"][0]["artists"], ["Band"])
        self.assertEqual(self.fake.last("/Items")["q"]["parentid"], ALBUM)
        t = self.get(f"/api/jf/item/{TRACK}").json()
        self.assertEqual((t["album"], t["album_id"]), ("Record", ALBUM))
        self.assertEqual(t["image"], f"/api/jf/stream/Items/{ALBUM}/Images/Primary?tag=ap&maxWidth=600")
        self.assertEqual(self.get("/api/jf/item/nothex").status_code, 422)
        self.assertEqual(self.get(f"/api/jf/item/{hid(0x777)}").status_code, 404)


class Playback(Base):
    def play(self, item, **body):
        return self.admin.post(f"/api/jf/playback/{item}", json=body, headers=H)

    def test_direct_play_with_streams_chapters_and_resume(self):
        r = self.play(MOVIE)
        self.assertEqual(r.status_code, 200, r.text)
        p = r.json()
        self.assertEqual((p["method"], p["media_source"], p["play_session"], p["runtime"], p["position"]), ("direct", MS, "ps1", 170 * 60, 600))
        u = urlsplit(p["url"])
        self.assertEqual(u.path, f"/api/jf/stream/Videos/{MOVIE}/stream")
        self.assertIn("static=true", u.query)
        self.assertIn(f"MediaSourceId={MS}", u.query)
        self.assertEqual([(a["index"], a["language"], a["channels"], a["default"]) for a in p["audio"]], [(1, "eng", 6, True), (2, "fre", 2, False)])
        eng, fre = p["subtitles"]
        self.assertEqual(eng["url"], f"/api/jf/stream/Videos/{MOVIE}/{MS}/Subtitles/3/0/Stream.vtt")
        self.assertIsNone(fre["url"], "a picture subtitle can't be sent as text")
        self.assertEqual(p["chapters"], [{"start": 0, "name": "Start"}, {"start": 300, "name": "Heist"}])
        self.assertNotIn(KEY, r.text)
        sent = self.fake.last(f"/Items/{MOVIE}/PlaybackInfo")
        self.assertEqual(sent["q"]["userid"], FAMILY)
        self.assertEqual(sent["json"]["StartTimeTicks"], 600 * T, "asks for the resume point")
        prof = sent["json"]["DeviceProfile"]
        direct = " ".join(d["Container"] for d in prof["DirectPlayProfiles"])
        for c in ("mp4", "webm", "mp3", "flac", "opus", "m4a"):
            self.assertIn(c, direct)
        self.assertNotIn("hevc", str(prof["DirectPlayProfiles"]), "HEVC only when the browser says it can")
        self.assertEqual([t["Protocol"] for t in prof["TranscodingProfiles"] if t["Type"] == "Video"], ["hls", "hls"])
        self.assertTrue(all(t["VideoCodec"] == "h264" for t in prof["TranscodingProfiles"] if t["Type"] == "Video"))
        self.play(MOVIE, hevc=True)
        self.assertIn("hevc", str(self.fake.last(f"/Items/{MOVIE}/PlaybackInfo")["json"]["DeviceProfile"]["DirectPlayProfiles"]))

    def test_transcodes_to_hls_through_the_proxy(self):
        p = self.play(EP, media_source=MS, audio_index=2, max_bitrate=4_000_000).json()
        self.assertEqual(p["method"], "transcode")
        self.assertEqual(p["url"], f"/api/jf/stream/Videos/{EP}/master.m3u8?DeviceId=d&MediaSourceId={MS}&PlaySessionId=ps2&VideoCodec=h264&AudioCodec=aac")
        body = self.fake.last(f"/Items/{EP}/PlaybackInfo")["json"]
        self.assertEqual((body["MediaSourceId"], body["AudioStreamIndex"], body["MaxStreamingBitrate"]), (MS, 2, 4_000_000))
        forced = self.play(MOVIE, transcode=True)
        self.assertEqual(forced.status_code, 409, "forcing a transcode where Jellyfin offers none is an error, not a direct play")
        self.assertFalse(self.fake.last(f"/Items/{MOVIE}/PlaybackInfo")["json"]["EnableDirectPlay"])

    def test_nothing_playable_and_bad_ids(self):
        r = self.play(EP2)
        self.assertEqual(r.status_code, 409)
        self.assertIn("NoCompatibleStream", r.json()["detail"])
        self.assertEqual(self.play("nope").status_code, 422)

    def test_music_plays_from_the_audio_routes(self):
        p = self.play(TRACK).json()
        self.assertEqual((p["method"], urlsplit(p["url"]).path), ("direct", f"/api/jf/stream/Audio/{TRACK}/stream"))
        self.assertEqual(self.fake.last(f"/Items/{TRACK}/PlaybackInfo")["json"]["DeviceProfile"]["TranscodingProfiles"][-1]["Type"], "Audio")


class Progress(Base):
    def post(self, **body):
        return self.admin.post("/api/jf/progress", json=body, headers=H)

    def test_position_played_and_favourite(self):
        self.assertEqual(self.post(id=MOVIE, position=61.5).status_code, 200)
        c = self.fake.last(f"/UserItems/{MOVIE}/UserData")
        self.assertEqual((c["q"]["userid"], c["json"]), (FAMILY, {"PlaybackPositionTicks": 615_000_000}))
        self.post(id=MOVIE, played=True)
        self.assertEqual(self.fake.last(f"/UserItems/{MOVIE}/UserData")["json"], {"Played": True, "PlaybackPositionTicks": 0})
        self.post(id=MOVIE, played=False)
        self.assertEqual(self.fake.last(f"/UserItems/{MOVIE}/UserData")["json"], {"Played": False, "PlaybackPositionTicks": 0})
        self.post(id=MOVIE, favourite=True)
        self.assertEqual(self.fake.last(f"/UserItems/{MOVIE}/UserData")["json"], {"IsFavorite": True})
        self.post(id=MOVIE, favourite=False)
        self.assertEqual(self.fake.last(f"/UserItems/{MOVIE}/UserData")["json"], {"IsFavorite": False})
        self.assertEqual(self.post(id=MOVIE).status_code, 422, "nothing to save")
        self.assertEqual(self.post(id="x", played=True).status_code, 422)
        self.assertEqual(self.post(id=MOVIE, position=-5).status_code, 422)

    def test_falls_back_to_the_old_route(self):
        self.fake.old = True
        self.assertEqual(self.post(id=MOVIE, position=10).status_code, 200)
        c = self.fake.last(f"/Users/{FAMILY}/Items/{MOVIE}/UserData")
        self.assertEqual(c["json"], {"PlaybackPositionTicks": 100_000_000})


class Proxy(Base):
    def stream(self, path, **kw):
        return self.get("/api/jf/stream/" + path, **kw)

    def test_allowed_paths_get_the_key_server_side_and_lose_the_clients(self):
        r = self.stream(f"Videos/{MOVIE}/stream?static=true&MediaSourceId={MS}&api_key=EVIL&ApiKey=EVIL2&X-Emby-Token=EVIL3&userId=u",
                        headers={"Range": "bytes=5-9"})
        self.assertEqual((r.status_code, r.content), (206, b"hello"))
        c = self.fake.calls[-1]
        self.assertEqual(c["path"], f"/Videos/{MOVIE}/stream")
        self.assertEqual(c["headers"]["Range"], "bytes=5-9", "Range goes through")
        self.assertIn(KEY, c["headers"]["Authorization"])
        self.assertEqual(dict(c["params"]), {"static": "true", "MediaSourceId": MS}, "the browser can't pick another userId")
        self.assertTrue(c["stream"])
        for h, v in (("content-range", "bytes 5-9/100"), ("content-length", "5"), ("content-type", "video/mp4"), ("accept-ranges", "bytes")):
            self.assertEqual(r.headers[h], v)
        self.assertNotIn("x-leak", r.headers, "only the headers a player needs")
        self.assertNotIn("EVIL", str(self.fake.calls))
        whole = self.stream(f"Audio/{TRACK}/stream.mp3")
        self.assertEqual((whole.status_code, whole.content), (200, b"whole"))
        self.assertNotIn("range", self.fake.calls[-1]["headers"])
        for ok in (f"Videos/{MOVIE}/master.m3u8", f"Videos/{MOVIE}/main.m3u8", f"Videos/{MOVIE}/hls1/main/0.mp4", f"Videos/{MOVIE}/stream.mkv",
                   f"Videos/{MOVIE}/{MS}/Subtitles/3/Stream.srt", f"Videos/{MOVIE}/{MS}/Subtitles/3/0/Stream.ass", f"Audio/{TRACK}/universal",
                   f"Audio/{TRACK}/master.m3u8", f"Audio/{TRACK}/hls1/main/3.aac", f"Items/{MOVIE}/Images/Logo", f"Items/{MOVIE}/Images/Backdrop/2",
                   f"Items/{MOVIE}/Download"):
            self.assertEqual(self.stream(ok).status_code, 200, ok)

    def test_everything_else_is_refused_without_asking_jellyfin(self):
        bad = ["System/Info", "Users", "Users/" + ALICE, "Items", f"Items/{MOVIE}", f"Items/{MOVIE}/PlaybackInfo", "Sessions", "ScheduledTasks",
               f"Videos/{MOVIE}", f"Videos/{MOVIE}/stream/x", f"Videos/short/stream", f"Videos/{MOVIE[:-1]}g/stream", f"Videos/{MOVIE}/Stream",
               f"Videos/{MOVIE}/hls1/../../../System/Info", f"Videos/{MOVIE}/hls1/a%2F..%2F..%2FSystem%2FInfo", f"Videos/{MOVIE}/hls1/%2e%2e/x",
               f"Videos/{MOVIE}/hls1/%252e%252e/x", f"Videos/{MOVIE}/hls1/", f"Videos/{MOVIE}/hls1//x",
               f"Videos/{MOVIE}/hls1/a%5Cb", f"Items/{MOVIE}/Images/Nope", f"Items/{MOVIE}/Images/Primary/x", f"Items/{MOVIE}/Images/Primary/../../x",
               f"Items/{MOVIE}/Images/Primary%2F..%2F..%2FUsers", f"Items/{MOVIE}/Download/x", f"Audio/{TRACK}/Download", "..%2FSystem%2FInfo",
               f"Videos/{MOVIE}/{MS}/Subtitles/3/Stream.exe", f"Videos/{MOVIE}/stream%00.mp4", f"Videos/{MOVIE}/stream%3F.mp4",
               f"videos/{MOVIE}/stream"]  # (a trailing %0a is dropped by Starlette's route `$` before we see it: harmless)
        before = len(self.fake.calls)
        for path in bad:
            self.assertEqual(self.stream(path).status_code, 404, path)
        self.assertEqual(len(self.fake.calls), before, "none of them reached Jellyfin")
        self.assertEqual(client().get("/api/jf/stream/Users").status_code, 401)
        self.assertEqual(self.admin.post(f"/api/jf/stream/Videos/{MOVIE}/stream", headers=H).status_code, 405)

    def test_playlists_lose_the_key_and_keep_relative_paths_working(self):
        r = self.stream(f"Videos/{MOVIE}/master.m3u8?api_key=EVIL")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/vnd.apple.mpegurl")
        self.assertNotIn("api_key", self.fake.calls[-1]["params"].__str__().lower(), "the client's key isn't forwarded")
        self.assertNotIn(KEY, r.text)
        self.assertNotIn("apikey", r.text.lower())
        self.assertEqual(r.text, '#EXTM3U\n#EXT-X-MAP:URI="init.mp4?PlaySessionId=p"\nmain.m3u8?a=1&b=2\nhls1/main/0.mp4\nhls1/main/1.mp4?x=1\n')
        self.assertEqual(int(r.headers["content-length"]), len(r.content), "length is of the rewritten body")

    def test_strip_key_cases(self):
        for text, want in [("a?api_key=K", "a"), ("a?api_key=K&b=1", "a?b=1"), ("a?b=1&ApiKey=K", "a?b=1"), ("a?b=1&APIKEY=K&c=2", "a?b=1&c=2"),
                           ("a?x=1", "a?x=1"), ("a?not_api_key=K", "a?not_api_key=K")]:
            self.assertEqual(jf.strip_key(text), want)

    def test_images_are_cached_briefly_and_other_streams_are_not(self):
        img = self.stream(f"Items/{MOVIE}/Images/Primary?tag=tagP&maxWidth=300")
        self.assertEqual(img.headers["cache-control"], "private, max-age=3600")
        self.assertEqual(dict(self.fake.calls[-1]["params"]), {"tag": "tagP", "maxWidth": "300"})
        self.assertEqual(self.stream(f"Videos/{MOVIE}/stream").headers["cache-control"], "no-store")

    def test_only_what_your_jellyfin_user_can_see(self):
        hidden = hid(0x777)  # the fake's Jellyfin has no such item for this user
        before = len(self.fake.calls)
        self.assertEqual(self.stream(f"Videos/{hidden}/stream").status_code, 404)
        self.assertFalse([c for c in self.fake.calls[before:] if "/Videos/" in c.get("url", "")], "nothing was streamed")
        for sneaky in (f"Videos/{hidden}/hls1/Images/0.ts", f"Audio/{hidden}/hls1/Images/main.m3u8"):
            self.assertEqual(self.stream(sneaky).status_code, 404, "a playlist named Images is still a stream")
        self.assertFalse([c for c in self.fake.calls[before:] if "/hls1/" in c.get("url", "")], "nothing was streamed")
        jf._users.put("all", [{**u, "Policy": {"EnableContentDownloading": False}} for u in jf._users.get("all") or jf.call("GET", "/Users").json()])
        self.assertEqual(self.stream(f"Items/{MOVIE}/Download").status_code, 403)

    def test_upstream_trouble(self):
        self.stream(f"Videos/{MOVIE}/stream")  # the access check is cached, so what follows tests the stream itself
        for status, want in ((404, 404), (500, 502), (401, 502), (302, 502)):
            with mock.patch.object(jf.requests, "request", lambda *a, **k: Resp(status)):
                self.assertEqual(self.stream(f"Videos/{MOVIE}/stream").status_code, want, status)
        with mock.patch.object(jf.requests, "request", lambda *a, **k: Resp(416, headers={"Content-Range": "bytes */9"})):
            r = self.stream(f"Videos/{MOVIE}/stream", headers={"Range": "bytes=50-"})
            self.assertEqual((r.status_code, r.headers["content-range"]), (416, "bytes */9"))
        with mock.patch.object(jf.requests, "request", side_effect=jf.requests.ConnectionError("down")):
            self.assertEqual(self.stream(f"Videos/{MOVIE}/stream").status_code, 502)
        self.assertNotIn(KEY, self.stream(f"Videos/{MOVIE}/stream").text)


class LocalMusic(unittest.TestCase):
    def test_ranges_and_confinement(self):
        c = signed_in("admin")
        song = env.TMP / "music" / "Band" / "Record" / "01 Song.mp3"
        song.parent.mkdir(parents=True, exist_ok=True)
        song.write_bytes(b"0123456789")
        (env.TMP / "music" / "notes.txt").write_text("hi")
        (env.TMP / "outside.mp3").write_bytes(b"secret")
        url = "/api/jf/music/Band/Record/01 Song.mp3"
        r = c.get(url, headers={"Range": "bytes=2-5"})
        self.assertEqual((r.status_code, r.content, r.headers["content-range"], r.headers["content-type"]), (206, b"2345", "bytes 2-5/10", "audio/mpeg"))
        self.assertEqual(c.get(url, headers={"Range": "bytes=-3"}).content, b"789")
        whole = c.get(url)
        self.assertEqual((whole.status_code, whole.content, whole.headers["accept-ranges"]), (200, b"0123456789", "bytes"))
        self.assertEqual(c.get(url, headers={"Range": "bytes=40-"}).status_code, 416)
        for bad in ("notes.txt", "../outside.mp3", "%2e%2e/outside.mp3", "Band/missing.mp3", "Band"):
            self.assertEqual(c.get("/api/jf/music/" + bad).status_code, 404, bad)
        self.assertEqual(client().get(url).status_code, 401)


if __name__ == "__main__":
    unittest.main()
