"""Music requests followed through Lidarr, with YouTube as the fallback: the states from Lidarr's queue and history,
the three hand-over triggers (nothing found in 6 h, two failed downloads, a download stuck for 12 h) with Lidarr
stopped first, matching on Spotify (one match goes; several wait for a pick), the staging folder, tags and import,
retries with backoff when Lidarr is down, artists expanded into albums, Lidarr's wanted list, and the page's routes.
Lidarr and music-fndr are fakes; time is passed in."""
import json
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from yeerr_test_env import H, TMP, signed_in

import arr
import core
import music
import musicreq

KEY = "l" * 32
H6, H12 = 6 * 3600, 12 * 3600


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.ok = status, body, status < 400
        self.content = b"x"
        self.text = json.dumps(body)

    def json(self):
        return self._body


def album(i, title="Record", tracks=10, files=0, monitored=True):
    return {"id": i, "title": title, "artistId": 7, "monitored": monitored, "foreignAlbumId": f"rg-{i}", "releaseDate": "1997-05-21T00:00:00Z",
            "artist": {"artistName": "Band", "foreignArtistId": "mb-artist"}, "images": [{"coverType": "cover", "remoteUrl": f"https://img.test/{i}.jpg"}],
            "releases": [{"id": 100 + i, "foreignReleaseId": f"rel-{i}", "monitored": True, "trackCount": tracks},
                         {"id": 200 + i, "foreignReleaseId": f"rel-short-{i}", "monitored": False, "trackCount": 1}],
            "statistics": {"trackCount": tracks, "trackFileCount": files, "totalTrackCount": tracks * 2}}


class FakeLidarr:
    def __init__(self):
        self.albums, self.queue, self.history, self.calls, self.down, self.wanted = {}, [], [], [], False, []
        self.commands, self.notifications = {}, []

    def __call__(self, method, url, headers=None, params=None, json=None, timeout=None, **kw):
        u = urlsplit(url)
        path = u.path.split("/api/v1")[1]
        q = {**{k: v[0] for k, v in parse_qs(u.query).items()}, **{k: str(v) for k, v in (params or {}).items()}}
        self.calls.append((method, path, q, json))
        if self.down:
            raise arr.requests.ConnectionError("refused")
        if path.startswith("/album/") and path[7:].isdigit():
            a = self.albums.get(int(path[7:]))
            return Resp(200, a) if a else Resp(404, {"message": "NotFound"})
        if path == "/album":
            return Resp(200, [a for a in self.albums.values() if str(a["artistId"]) == q.get("artistId")])
        if path == "/album/monitor":
            for i in json["albumIds"]:
                self.albums[i]["monitored"] = json["monitored"]
            return Resp(202, {})
        if path == "/queue/details":
            return Resp(200, [x for x in self.queue if str(x["albumId"]) == q.get("albumIds")])
        if path.startswith("/queue/") and method == "DELETE":
            self.queue = [x for x in self.queue if x["id"] != int(path[7:])]
            return Resp(200, {})
        if path == "/history/artist":
            return Resp(200, [h for h in self.history if str(h["albumId"]) == q.get("albumId")])
        if path == "/notification/schema":
            return Resp(200, [{"implementation": "Webhook", "fields": [{"name": "url", "value": ""}, {"name": "method", "value": 1}]},
                              {"implementation": "Discord", "fields": []}])
        if path == "/notification":
            if method == "POST":
                self.notifications.append(json)
            return Resp(200 if method == "GET" else 201, self.notifications if method == "GET" else json)
        if path == "/track":
            n = next(r["trackCount"] for a in self.albums.values() for r in a["releases"] if str(r["id"]) == q.get("albumReleaseId"))
            return Resp(200, [{"id": 1000 + k, "absoluteTrackNumber": k, "trackNumber": str(k), "title": ["One", "Two", "Three"][k - 1] if k <= 3 else f"T{k}"}
                              for k in range(1, n + 1)])
        if path == "/manualimport":
            from pathlib import Path
            return Resp(200, [{"path": str(f), "quality": {"quality": {"id": 1, "name": "MP3-320"}}} for f in sorted(Path(q["folder"]).rglob("*")) if f.is_file()])
        if path == "/wanted/missing":
            return Resp(200, {"records": self.wanted})
        if path == "/command" and method == "POST":
            cid = len(self.commands) + 1
            self.commands[cid] = {"id": cid, "status": "started", **json}
            return Resp(201, self.commands[cid])
        if path.startswith("/command/"):
            return Resp(200, self.commands[int(path[9:])])
        return Resp(404, {"message": f"no {path}"})

    def event(self, album_id, kind, at, **data):
        self.history.append({"albumId": album_id, "eventType": kind, "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)), "data": data})


class Base(unittest.TestCase):
    def setUp(self):
        root = TMP / "musicreq-stack"
        (root / "lidarr").mkdir(parents=True, exist_ok=True)
        (root / "lidarr" / "config.xml").write_text(f"<Config><ApiKey>{KEY}</ApiKey></Config>")
        self.lidarr = FakeLidarr()
        self.staging = TMP / "staging"
        import shutil
        shutil.rmtree(self.staging, ignore_errors=True)
        self.spotify = {"albums": []}
        self.jobs = []

        def queue_download(kind, id, title=None, subtitle=None, image=None, output=None):
            job = {"job": f"j{len(self.jobs) + 1}", "kind": kind, "id": id, "output": output, "status": "queued", "songs": {}, "error": None}
            self.jobs.append(job)
            return job

        for p in (mock.patch.object(arr, "STACK", root), mock.patch.object(arr.requests, "request", self.lidarr),
                  mock.patch.object(musicreq, "STAGING", self.staging), mock.patch.object(music, "queue_download", queue_download),
                  mock.patch.object(music, "jobs", self.jobs), mock.patch.object(music, "search", lambda q: self.spotify),
                  mock.patch.object(music, "album", lambda sid: {"total": next((a.get("total", 10) for a in self.spotify["albums"] if a["id"] == sid), 10)})):
            p.start()
            self.addCleanup(p.stop)
        arr._keys.clear()
        core.db().execute("DELETE FROM music_requests")
        self.t0 = 1_800_000_000.0
        self.user = signed_in("asker-music")
        self.uid = self.user.get("/api/me").json()["id"]

    def ask(self, album_id, **kw):
        self.lidarr.albums[album_id] = album(album_id, **kw)
        musicreq.add(self.uid, {"album_id": album_id, "title": kw.get("title", "Record")}, now=self.t0)
        return core.one("SELECT id FROM music_requests WHERE album = ?", album_id)["id"]

    def row(self, rid):
        return core.one("SELECT * FROM music_requests WHERE id = ?", rid)

    def tick(self, after):
        musicreq.tick(self.t0 + after)


class FollowsLidarr(Base):
    def test_searching_downloading_importing_imported(self):
        rid = self.ask(1)
        self.tick(60)
        self.assertEqual(self.row(rid)["state"], "searching")
        self.lidarr.event(1, "grabbed", self.t0 + 100)
        self.lidarr.queue.append({"id": 50, "albumId": 1, "sizeleft": 900, "trackedDownloadState": "downloading", "statusMessages": []})
        self.tick(200)
        self.assertEqual(self.row(rid)["state"], "downloading")
        self.lidarr.queue[0].update(sizeleft=0, trackedDownloadState="importPending", statusMessages=[{"messages": ["Waiting to import"]}])
        self.tick(400)
        r = self.row(rid)
        self.assertEqual((r["state"], r["why"]), ("importing", "Waiting to import"))
        self.lidarr.queue.clear()
        self.lidarr.albums[1]["statistics"]["trackFileCount"] = 10
        self.tick(600)
        r = self.row(rid)
        self.assertEqual((r["state"], r["title"], r["artist_name"], r["via"]), ("imported", "Record", "Band", "lidarr"))

    def test_an_album_lidarr_stopped_watching_is_watched_again(self):
        rid = self.ask(3)
        self.lidarr.albums[3]["monitored"] = False  # what adding a new artist with "monitor none" does to it
        self.tick(60)
        self.assertTrue(self.lidarr.albums[3]["monitored"])
        self.assertEqual(self.row(rid)["state"], "searching")

    def test_lidarr_down_backs_off_and_survives(self):
        rid = self.ask(2)
        self.lidarr.down = True
        self.tick(60)
        r = self.row(rid)
        self.assertEqual((r["state"], r["tries"]), ("searching", 1))
        self.assertIn("Lidarr didn't answer", r["why"])
        self.assertGreater(r["next_at"], self.t0 + 60 + 600, "it waits before asking again")
        asked = lambda: len([c for c in self.lidarr.calls if c[1].startswith("/album/")])  # noqa: E731  (about this request)
        calls = asked()
        self.tick(120)
        self.assertEqual(asked(), calls, "not due yet: Lidarr isn't asked about it")
        self.lidarr.down = False
        self.tick(H6 - 10)
        self.assertEqual(self.row(rid)["state"], "searching", "back to normal, from the database alone")

    def test_an_artist_becomes_its_albums_and_the_wanted_list_joins(self):
        self.lidarr.albums[3] = album(3, "One")
        self.lidarr.albums[4] = album(4, "Two", files=10)  # already there
        self.lidarr.albums[5] = album(5, "Three", monitored=False)  # not wanted
        musicreq.add(self.uid, {"artist_id": 7, "title": "Band"}, now=self.t0)
        self.lidarr.wanted = [{**album(6, "From Lidarr"), "artistId": 8}]  # someone else's album
        self.lidarr.albums[6] = self.lidarr.wanted[0]
        self.tick(60)
        got = {r["title"]: r["user_id"] for r in core.rows("SELECT * FROM music_requests")}
        self.assertEqual(got, {"One": self.uid, "From Lidarr": None})
        mine = self.user.get("/api/arr/music/requests").json()["items"]
        self.assertEqual([x["title"] for x in mine], ["One"], "a member sees their own")
        admin = signed_in("admin").get("/api/arr/music/requests").json()["items"]
        self.assertEqual(sorted(x["title"] for x in admin), ["From Lidarr", "One"], "an admin sees Lidarr's own list too")


class HandsOver(Base):
    def assert_stopped(self, album_id):
        self.assertFalse(self.lidarr.albums[album_id]["monitored"], "Lidarr stops looking")
        self.assertEqual([x for x in self.lidarr.queue if x["albumId"] == album_id], [])

    def one_match(self, total=10):
        self.spotify["albums"] = [{"id": "sp1", "name": "Record (Deluxe Edition)", "artists": [{"name": "Band"}], "year": "1997", "total": total, "image": "i"},
                                  {"id": "sp2", "name": "Record", "artists": [{"name": "Other Band"}], "year": "1997"},
                                  {"id": "sp3", "name": "Record Live", "artists": [{"name": "Band"}], "year": "1997"}]

    def test_nothing_found_in_six_hours(self):
        rid = self.ask(1)
        self.one_match()
        self.tick(H6 - 60)
        self.assertEqual(self.row(rid)["state"], "searching")
        self.tick(H6 + 60)
        r = self.row(rid)
        self.assertEqual((r["state"], r["via"], r["spotify"]), ("fetching", "youtube", "sp1"))
        self.assertIn("6 hours", r["why"])
        self.assert_stopped(1)
        self.assertEqual(self.jobs[0]["output"], f"{self.staging}/request-{rid}/{{album-artist}}/{{album}}/{{track-number}} - {{title}}.{{output-ext}}")

    def test_lidarr_watching_a_deluxe_edition_still_matches_the_plain_album(self):
        """Lidarr often picks a big edition as the album's release (91 tracks); Spotify's plain album (12) is still it."""
        rid = self.ask(1, tracks=91)
        self.lidarr.albums[1]["releases"].append({"id": 301, "foreignReleaseId": "rel-plain", "monitored": False, "trackCount": 12})
        self.one_match(total=12)
        self.tick(H6 + 60)
        self.assertEqual((self.row(rid)["state"], self.row(rid)["spotify"]), ("fetching", "sp1"))

    def test_two_failed_downloads(self):
        rid = self.ask(1)
        self.one_match()
        self.lidarr.queue.append({"id": 51, "albumId": 1, "sizeleft": 10, "trackedDownloadState": "downloading"})
        self.lidarr.event(1, "downloadFailed", self.t0 + 10)
        self.tick(60)
        self.assertEqual(self.row(rid)["state"], "downloading", "one failure: Lidarr tries again")
        self.lidarr.event(1, "downloadFailed", self.t0 + 100)
        self.tick(200)
        self.assertEqual(self.row(rid)["state"], "fetching")
        self.assert_stopped(1)
        delete = next(c for c in self.lidarr.calls if c[0] == "DELETE")
        self.assertEqual((delete[1], delete[2]["blocklist"], delete[2]["removeFromClient"]), ("/queue/51", "true", "true"))

    def test_a_download_stuck_for_twelve_hours(self):
        rid = self.ask(1)
        self.one_match()
        self.lidarr.queue.append({"id": 52, "albumId": 1, "sizeleft": 500, "trackedDownloadState": "downloading"})
        self.tick(60)
        self.lidarr.queue[0]["sizeleft"] = 400  # it moved
        self.tick(H12 - 120)
        self.tick(H12 + 60)
        self.assertEqual(self.row(rid)["state"], "downloading", "12 h since it last moved, not since it started")
        self.tick(H12 * 2)
        self.assertEqual(self.row(rid)["state"], "fetching")
        self.assert_stopped(1)

    def test_several_matches_wait_for_a_pick(self):
        rid = self.ask(1)
        self.spotify["albums"] = [{"id": "spA", "name": "Record", "artists": [{"name": "Band"}], "year": "1997"},
                                  {"id": "spB", "name": "Record (Remastered)", "artists": [{"name": "Band"}], "year": "1998"}]
        self.tick(H6 + 60)
        r = self.row(rid)
        self.assertEqual(r["state"], "needs_look")
        self.assertTrue(self.lidarr.albums[1]["monitored"], "Lidarr keeps looking until someone picks")
        item = next(x for x in self.user.get("/api/arr/music/requests").json()["items"] if x["id"] == rid)
        self.assertEqual([c["id"] for c in item["candidates"]], ["spA", "spB"])
        self.assertEqual(self.user.post(f"/api/arr/music/requests/{rid}/choose", json={"spotify": "../x"}, headers=H).status_code, 409)
        r = self.user.post(f"/api/arr/music/requests/{rid}/choose", json={"spotify": "spB"}, headers=H)
        self.assertEqual((r.status_code, r.json()["state"]), (200, "fetching"))
        self.assertEqual(self.jobs[-1]["id"], "spB")
        self.assert_stopped(1)

    def test_youtube_then_lidarr_imports_it(self):
        rid = self.ask(1)
        self.one_match()
        self.tick(H6 + 60)
        job = self.jobs[0]
        folder = self.staging / f"request-{rid}" / "Band" / "Record"
        folder.mkdir(parents=True)
        song = folder / "01 - One.mp3"
        import subprocess
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", "-c:a", "libmp3lame", str(song)], check=True)
        job.update(status="done", songs={"u1": {"status": "done"}})
        self.tick(H6 + 700)
        r = self.row(rid)
        self.assertEqual(r["state"], "importing")
        cmd = self.lidarr.commands[r["command"]]
        self.assertEqual((cmd["name"], cmd["importMode"]), ("ManualImport", "move"))
        self.assertEqual([(f["path"], f["albumId"], f["albumReleaseId"], f["trackIds"]) for f in cmd["files"]], [(str(song), 1, 201, [1001])],
                         "Lidarr is told which album, release and track the file is")
        import mutagen
        tags = mutagen.File(song, easy=True)
        self.assertEqual((tags["musicbrainz_albumid"], tags["musicbrainz_releasegroupid"]), (["rel-short-1"], ["rg-1"]),
                         "the album's id, and the release with as many tracks as YouTube gave")
        song.unlink()  # Lidarr moved it
        self.lidarr.albums[1]["statistics"]["trackFileCount"] = 10
        self.tick(H6 + 1400)
        r = self.row(rid)
        self.assertEqual((r["state"], r["via"]), ("imported", "youtube"))
        self.assertTrue(self.lidarr.albums[1]["monitored"], "monitored again, so Lidarr can find a better copy later")
        self.assertFalse((self.staging / f"request-{rid}").exists(), f"the empty staging folder goes: {list((self.staging / f'request-{rid}').rglob('*'))}")

    def test_a_long_import_is_waited_for(self):
        rid = self.ask(1)
        self.one_match()
        self.tick(H6 + 60)
        (self.staging / f"request-{rid}").mkdir(parents=True)
        (self.staging / f"request-{rid}" / "01.mp3").write_bytes(b"x")
        self.jobs[0].update(status="done", songs={"u1": {"status": "done"}})
        self.tick(H6 + 700)
        self.assertTrue(self.lidarr.albums[1]["monitored"], "watched again before the import, so its counts show it")
        self.lidarr.commands[self.row(rid)["command"]]["message"] = "Processing file 7 of 12"
        self.tick(H6 + 700 + 3 * 3600)
        r = self.row(rid)
        self.assertEqual((r["state"], r["why"]), ("importing", "Processing file 7 of 12"), "still running after 3 h: not stuck")

    def test_an_import_that_fails_says_why_and_retries(self):
        rid = self.ask(1)
        self.one_match()
        self.tick(H6 + 60)
        (self.staging / f"request-{rid}").mkdir(parents=True)
        (self.staging / f"request-{rid}" / "01.mp3").write_bytes(b"x")
        self.jobs[0].update(status="done", songs={"u1": {"status": "done"}})
        self.tick(H6 + 700)
        cid = self.row(rid)["command"]
        self.lidarr.commands[cid].update(status="completed", message="Failed to import")
        self.lidarr.event(1, "albumImportIncomplete", self.t0 + H6 + 750, statusMessages="Album match is not close enough: 62%")
        self.tick(H6 + 700 + 200)
        r = self.row(rid)
        self.assertEqual(r["state"], "failed")
        self.assertIn("not close enough", r["why"])
        r = self.user.post(f"/api/arr/music/requests/{rid}/retry", headers=H)
        self.assertEqual((r.status_code, r.json()["state"]), (200, "importing"), "Retry imports the files that are there")

    def test_youtube_now_and_only_your_own(self):
        rid = self.ask(1)
        self.one_match()
        other = signed_in("someone-else-music")
        self.assertEqual(other.post(f"/api/arr/music/requests/{rid}/youtube", headers=H).status_code, 404)
        self.assertNotIn(rid, [x["id"] for x in other.get("/api/arr/music/requests").json()["items"]])
        r = self.user.post(f"/api/arr/music/requests/{rid}/youtube", headers=H)
        self.assertEqual((r.status_code, r.json()["state"]), (200, "fetching"))
        self.assert_stopped(1)


class StaysMoving(Base):
    """Nothing sits forever: each way a request could get stuck ends in a hand-over, a retry or a clear failure."""

    def one_match(self):
        self.spotify["albums"] = [{"id": "sp1", "name": "Record", "artists": [{"name": "Band"}], "year": "1997", "total": 10}]

    def test_grabbed_then_nothing(self):
        rid = self.ask(1)
        self.one_match()
        self.lidarr.event(1, "grabbed", self.t0 + 3600)  # grabbed, then the download vanished: no queue, no files
        self.tick(3600 + 60)
        self.assertEqual(self.row(rid)["state"], "importing", "just grabbed: on its way")
        self.tick(3600 + 3 * 3600)
        self.assertEqual(self.row(rid)["state"], "searching")
        self.tick(3600 + H6 + 60)
        r = self.row(rid)
        self.assertEqual(r["state"], "fetching", "6 h after the grab that went nowhere, YouTube")
        self.assertIn("never arrived", r["why"])

    def test_stuck_waiting_to_import(self):
        rid = self.ask(1)
        self.one_match()
        self.lidarr.queue.append({"id": 60, "albumId": 1, "sizeleft": 0, "trackedDownloadState": "importBlocked",
                                  "statusMessages": [{"messages": ["Album match is not close enough"]}]})
        self.tick(60)
        self.assertEqual(self.row(rid)["state"], "importing")
        self.tick(H12 + 120)
        r = self.row(rid)
        self.assertEqual(r["state"], "fetching")
        self.assertIn("couldn't import", r["why"])
        self.assertEqual(self.lidarr.queue, [], "its stuck download is removed (and blocklisted)")

    def test_a_stopped_youtube_download_is_tried_again_then_fails(self):
        rid = self.ask(1)
        self.one_match()
        self.tick(H6 + 60)
        for n in range(musicreq.MAX_TRIES):
            self.jobs[-1]["status"] = "stopped"
            self.tick(H6 + 120 + n)
        r = self.row(rid)
        self.assertEqual(r["state"], "failed")
        self.assertIn("stopped", r["why"])
        self.assertEqual(len(self.jobs), musicreq.MAX_TRIES)

    def test_asking_again_reopens_from_lidarr(self):
        rid = self.ask(1)
        core.db().execute("UPDATE music_requests SET state = 'imported', via = 'youtube', spotify = 'old', job = 'j9' WHERE id = ?", (rid,))
        musicreq.add(self.uid, {"album_id": 1, "title": "Record"}, now=self.t0 + 100)
        r = self.row(rid)
        self.assertEqual((r["state"], r["via"], r["spotify"], r["job"]), ("searching", "lidarr", None, None))

    def test_lidarrs_own_list_waits_for_an_admin_and_goes_a_few_at_a_time(self):
        self.one_match()
        for i in range(1, 6):
            self.lidarr.albums[i] = album(i)
            musicreq.track_album(None, i, self.t0)
        self.tick(H6 + 60)
        self.assertEqual({r["state"] for r in core.rows("SELECT state FROM music_requests")}, {"searching"}, "not without the admin's say")
        self.assertTrue(all(a["monitored"] for a in self.lidarr.albums.values()))
        core.set_setting("music_fallback_wanted", "1")
        self.addCleanup(core.set_setting, "music_fallback_wanted", "0")
        self.tick(H6 + 120)
        self.assertEqual(len(self.jobs), musicreq.HAND_OVERS, "a few each time round")
        self.tick(H6 + 720)
        self.assertEqual(len(self.jobs), 5)

    def test_errors_back_off_then_end(self):
        rid = self.ask(1)
        self.lidarr.down = True
        for n in range(musicreq.MAX_ERRORS):
            core.db().execute("UPDATE music_requests SET next_at = 0 WHERE id = ?", (rid,))
            self.tick(60 + n)
        r = self.row(rid)
        self.assertEqual(r["state"], "failed")
        self.lidarr.down = False
        self.assertEqual(self.user.post(f"/api/arr/music/requests/{rid}/retry", headers=H).json()["state"], "searching")


class Webhook(Base):
    def test_lidarr_tells_parlor_and_the_worker_wakes(self):
        import stack
        from yeerr_test_env import client
        status, _ = stack.lidarr_webhook()
        self.assertEqual(status, "done")
        hook = self.lidarr.notifications[0]
        url = next(f["value"] for f in hook["fields"] if f["name"] == "url")
        self.assertTrue(url.startswith("http://parlor:8800/hooks/lidarr/"), url)
        self.assertTrue(hook["onGrab"] and hook["onReleaseImport"] and hook["onDownloadFailure"])
        self.assertEqual(stack.lidarr_webhook()[0], "ok", "once")
        anon = client()  # Lidarr has no session and sends no special header
        woken = mock.patch.object(musicreq, "wake", __import__("threading").Event())  # not the running worker's: it would clear it
        woken.start()
        self.addCleanup(woken.stop)
        self.assertEqual(anon.post("/hooks/lidarr/not-the-one", json={"eventType": "Grab"}).status_code, 404)
        self.assertFalse(musicreq.wake.is_set())
        self.assertEqual(anon.post(url.split("8800", 1)[1], json={"eventType": "Grab"}).status_code, 200)
        self.assertTrue(musicreq.wake.is_set(), "the requests are checked now, not in ten minutes")
        with mock.patch("vault.locked", lambda: True):
            self.assertEqual(anon.post(url.split("8800", 1)[1], json={}).status_code, 423, "locked: a plain answer, not a crash")


class Requests(Base):
    def test_requesting_through_the_page_starts_the_ledger(self):
        with mock.patch("activity.request_music", lambda kind, mbid: {"album_id": 9, "artist_id": 7, "title": "Asked", "already": False}):
            self.lidarr.albums[9] = album(9, "Asked")
            r = self.user.post("/api/arr/music/request", json={"type": "album", "id": "b1392450-e666-3926-a536-22c65f834433"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        row = core.one("SELECT * FROM music_requests WHERE album = 9")
        self.assertEqual((row["user_id"], row["state"]), (self.uid, "searching"))


if __name__ == "__main__":
    unittest.main()
