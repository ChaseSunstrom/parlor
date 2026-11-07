"""Sonarr, Radarr and Lidarr in one shape: downloads, calendar, history, summary, and music
requests through Lidarr. The apps are a fake with replies shaped like real ones."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from yeerr_test_env import H, TMP, signed_in

import activity
import arr

KEYS = {"sonarr": "s" * 32, "radarr": "r" * 32, "lidarr": "l" * 32, "prowlarr": "p" * 32}
SOON = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
LATER = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
MB_ARTIST, MB_ALBUM = "a74b1b7f-71a5-4011-9441-d0b5e4122711", "b1392450-e666-3926-a536-22c65f834433"
POSTER = [{"coverType": "poster", "remoteUrl": "https://img.test/p.jpg"}]


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.ok = status, body, status < 400
        self.content = b"x"
        self.text = str(body)

    def json(self):
        return self._body


class FakeArrs:
    def __init__(self, down=()):
        self.down, self.calls, self.artists, self.albums = set(down), [], [], []

    def __call__(self, method, url, headers=None, params=None, json=None, timeout=None, **kw):
        u = urlsplit(url)
        app, path = u.hostname, u.path.split("/api/v")[1][1:]
        q = {**{k: v[0] for k, v in parse_qs(u.query).items()}, **(params or {})}
        self.calls.append((method, app, path, q, json))
        if app in self.down:
            raise arr.requests.ConnectionError("refused")
        if headers.get("X-Api-Key") != KEYS[app]:
            return Resp(401, {})
        return Resp(*getattr(self, app)(method, path, q, json))

    def sonarr(self, method, path, q, body):
        if path == "/queue":
            assert q.get("includeSeries") == "true"
            return 200, {"records": [
                {"id": 1, "title": "Show.S01E02.1080p", "size": 1000, "sizeleft": 250, "timeleft": "00:10:05", "status": "downloading",
                 "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading", "downloadClient": "qBittorrent",
                 "quality": {"quality": {"name": "WEBDL-1080p"}}, "series": {"title": "The Show", "images": POSTER},
                 "episode": {"seasonNumber": 1, "episodeNumber": 2, "title": "Second"}},
                {"id": 2, "title": "Show.S01E03", "size": 1000, "sizeleft": 1000, "timeleft": None, "status": "warning",
                 "trackedDownloadStatus": "warning", "trackedDownloadState": "importPending",
                 "statusMessages": [{"title": "x", "messages": ["No files found are eligible for import"]}],
                 "series": {"title": "The Show", "images": POSTER}, "episode": {"seasonNumber": 1, "episodeNumber": 3, "title": "Third"}}]}
        if path == "/calendar":
            return 200, [{"id": 7, "airDateUtc": SOON, "seasonNumber": 2, "episodeNumber": 1, "title": "Back", "hasFile": False, "monitored": True,
                          "series": {"title": "The Show", "images": POSTER}}]
        if path == "/history":
            return 200, {"records": [{"eventType": "downloadFolderImported", "date": "2026-11-06T10:00:00Z", "sourceTitle": "Show.S01E01",
                                      "series": {"title": "The Show", "images": POSTER}, "episode": {"seasonNumber": 1, "episodeNumber": 1, "title": "Pilot"},
                                      "quality": {"quality": {"name": "HDTV-720p"}}}]}
        return self.common(path)

    def radarr(self, method, path, q, body):
        if path == "/queue":
            return 200, {"records": [{"id": 9, "title": "Film.2026", "size": 4000, "sizeleft": 0, "status": "completed", "trackedDownloadState": "importing",
                                      "movie": {"title": "A Film", "year": 2026, "images": POSTER}}]}
        if path == "/calendar":
            return 200, [{"id": 3, "title": "A Film", "year": 2026, "inCinemas": LATER, "digitalRelease": SOON, "hasFile": False, "images": POSTER},
                         {"id": 4, "title": "Old", "year": 2001, "inCinemas": "2001-01-01T00:00:00Z"}]
        if path == "/history":
            return 200, {"records": [{"eventType": "grabbed", "date": "2026-11-07T10:00:00Z", "sourceTitle": "Film.2026", "movie": {"title": "A Film", "year": 2026}}]}
        return self.common(path)

    def lidarr(self, method, path, q, body):
        if path in ("/queue", "/history"):
            return 200, {"records": []}
        if path == "/calendar":
            return 200, [{"id": 5, "title": "New Record", "releaseDate": SOON, "artist": {"artistName": "Band"}, "images": [{"coverType": "cover", "url": "https://img.test/c.jpg"}]}]
        if path == "/search":
            return 200, [{"artist": {"foreignArtistId": MB_ARTIST, "artistName": "Band", "images": POSTER}},
                         {"album": {"foreignAlbumId": MB_ALBUM, "title": "Record", "releaseDate": "1997-05-21T00:00:00Z", "artist": {"artistName": "Band"}}},
                         {"artist": {"foreignArtistId": None}}]
        if path == "/rootfolder":
            return 200, [{"path": "/data/music", "defaultQualityProfileId": 2, "defaultMetadataProfileId": 1}]
        if path == "/artist":
            if method == "POST":
                self.artists.append({**body, "id": 11})
                return 201, self.artists[-1]
            return 200, [a for a in self.artists if a["foreignArtistId"] == q.get("mbId")]
        if path == "/artist/lookup":
            return 200, [{"foreignArtistId": MB_ARTIST, "artistName": "Band"}]
        if path == "/album":
            if method == "POST":
                self.albums.append({**body, "id": 21, "artistId": 11})
                return 201, self.albums[-1]
            return 200, [a for a in self.albums if a["foreignAlbumId"] == q.get("foreignAlbumId")]
        if path == "/album/lookup":
            return 200, [{"foreignAlbumId": MB_ALBUM, "title": "Record", "artist": {"foreignArtistId": MB_ARTIST, "artistName": "Band"}}]
        if path in ("/album/monitor", "/command"):
            return 202, {}
        return self.common(path)

    def prowlarr(self, method, path, q, body):
        return self.common(path)

    def common(self, path):
        if path == "/system/status":
            return 200, {"version": "4.0.0"}
        if path == "/health":
            return 200, [{"type": "warning", "message": "Indexers unavailable"}]
        if path == "/wanted/missing":
            return 200, {"totalRecords": 12}
        return 404, {"message": f"no {path}"}


class Base(unittest.TestCase):
    down = ()

    def setUp(self):
        root = TMP / "activity-stack"
        for app, k in KEYS.items():
            (root / app).mkdir(parents=True, exist_ok=True)
            (root / app / "config.xml").write_text(f"<Config><ApiKey>{k}</ApiKey></Config>")
        self.fake = FakeArrs(self.down)
        for p in (mock.patch.object(arr, "STACK", root), mock.patch.object(arr.requests, "request", self.fake)):
            p.start()
            self.addCleanup(p.stop)
        arr._keys.clear()
        self.c = signed_in("watcher-arr")


class Views(Base):
    def test_queue_in_one_shape_stuck_first(self):
        r = self.c.get("/api/arr/queue").json()
        self.assertEqual(r["errors"], [])
        first, *rest = r["items"]
        self.assertEqual((first["state"], first["why"], first["title"], first["subtitle"]),
                         ("stuck", "No files found are eligible for import", "The Show", "S01E03 · Third"))
        dl = next(i for i in r["items"] if i["id"] == 1)
        self.assertEqual((dl["progress"], dl["eta"], dl["quality"], dl["image"]), (0.75, 605, "WEBDL-1080p", "https://img.test/p.jpg"))
        film = next(i for i in r["items"] if i["app"] == "radarr")
        self.assertEqual((film["state"], film["title"], film["subtitle"], film["progress"]), ("importing", "A Film", "2026", 1.0))

    def test_calendar_merges_and_picks_the_release_in_the_window(self):
        r = self.c.get("/api/arr/calendar?days=14").json()
        self.assertEqual([(i["app"], i["what"]) for i in r["items"]], [("sonarr", "Episode"), ("radarr", "Digital"), ("lidarr", "Album")])
        self.assertEqual(r["items"][2]["image"], "https://img.test/c.jpg")

    def test_history_newest_first_with_plain_words(self):
        r = self.c.get("/api/arr/history").json()
        self.assertEqual([(i["app"], i["event"]) for i in r["items"]], [("radarr", "Grabbed"), ("sonarr", "Imported")])

    def test_summary(self):
        apps = {a["app"]: a for a in self.c.get("/api/arr/summary").json()["apps"]}
        self.assertEqual(apps["sonarr"]["missing"], 12)
        self.assertNotIn("missing", apps["prowlarr"])
        self.assertEqual(apps["radarr"]["health"][0]["message"], "Indexers unavailable")

    def test_no_session_no_answer(self):
        from yeerr_test_env import client
        self.assertEqual(client().get("/api/arr/queue").status_code, 401)


class OneDown(Base):
    down = ("radarr",)

    def test_one_app_down_doesnt_sink_the_rest(self):
        r = self.c.get("/api/arr/queue").json()
        self.assertEqual({i["app"] for i in r["items"]}, {"sonarr"})
        self.assertEqual(len(r["errors"]), 1)
        self.assertIn("Radarr didn't answer", r["errors"][0])


class MusicRequests(Base):
    def test_search_then_request_an_album_without_its_whole_discography(self):
        items = self.c.get("/api/arr/music/search", params={"q": "band record"}).json()["items"]
        self.assertEqual([(i["type"], i["id"]) for i in items], [("artist", MB_ARTIST), ("album", MB_ALBUM)])
        self.assertEqual(items[1]["year"], "1997")
        r = self.c.post("/api/arr/music/request", json={"type": "album", "id": MB_ALBUM}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        album = self.fake.albums[0]
        self.assertTrue(album["monitored"] and album["addOptions"]["searchForNewAlbum"])
        self.assertEqual(album["artist"]["addOptions"], {"monitor": "none", "searchForMissingAlbums": False}, "only this album, not the artist's others")
        self.assertEqual((album["artist"]["rootFolderPath"], album["artist"]["qualityProfileId"]), ("/data/music", 2))
        again = self.c.post("/api/arr/music/request", json={"type": "album", "id": MB_ALBUM}, headers=H).json()
        self.assertTrue(again["already"])
        self.assertIn(("POST", "lidarr", "/command"), [c[:3] for c in self.fake.calls], "asking again searches again")

    def test_request_an_artist(self):
        r = self.c.post("/api/arr/music/request", json={"type": "artist", "id": MB_ARTIST}, headers=H).json()
        self.assertEqual((r["artist_id"], r["already"]), (11, False))
        self.assertEqual(self.fake.artists[0]["addOptions"], {"monitor": "all", "searchForMissingAlbums": True})
        self.assertTrue(self.c.post("/api/arr/music/request", json={"type": "artist", "id": MB_ARTIST}, headers=H).json()["already"])

    def test_bad_ids_refused(self):
        self.assertEqual(self.c.post("/api/arr/music/request", json={"type": "album", "id": "../x"}, headers=H).status_code, 422)
        self.assertEqual(self.c.post("/api/arr/music/request", json={"type": "song", "id": MB_ALBUM}, headers=H).status_code, 422)


if __name__ == "__main__":
    unittest.main()
