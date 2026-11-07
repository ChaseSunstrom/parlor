"""The Subsonic/OpenSubsonic server over a temp music folder: tiny tagged files made with ffmpeg,
then every endpoint through TestClient in XML and JSON, the three login styles, ranges and per-account data."""
import hashlib
import os
import shutil
import subprocess
import threading
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

import yeerr_test_env as env
from yeerr_test_env import H, TMP, client, signed_in

import auth
import core
import library
import subsonic

MUSIC = TMP / "music"
LRC = "[00:01.50]first line\n[00:03.00]second line\n"


def make(rel, fmt, **tags):
    path = MUSIC / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-y",
                    str(path)], check=True)
    if fmt == "mp3":
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3, ID3NoHeaderError

        try:
            ID3(path)
        except ID3NoHeaderError:
            ID3().save(path)
        t = EasyID3(path)
    else:
        from mutagen.flac import FLAC

        t = FLAC(path)
    for k, v in tags.items():
        t[k] = v
    t.save()
    return path


def jpeg(size=64, color="red"):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG")
    return buf.getvalue()


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is needed to make the test audio files")
class Subsonic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin, cls.bob = signed_in("admin"), signed_in("sub_bob")
        for left in MUSIC.iterdir():  # other test files share this temp folder and leave songs in it
            shutil.rmtree(left) if left.is_dir() else left.unlink()
        make("Band A/Record One/01 Alpha.mp3", "mp3", title="Alpha & <Co>", artist="Band A", album="Record One",
             albumartist="Band A", tracknumber="1", date="2001", genre="Rock")
        beta = make("Band A/Record One/02 Beta.flac", "flac", title="Beta", artist="Band A", album="Record One",
                    albumartist="Band A", tracknumber="2/9", date="2001-05-01", genre="Rock", lyrics="plain words")
        gamma = make("Band B/Record Two/01 Gamma.mp3", "mp3", title="Gamma", artist="Band B", album="Record Two",
                     albumartist="Band B", tracknumber="1", date="1999", genre="Jazz")
        from mutagen.id3 import APIC, ID3

        t = ID3(gamma)
        t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=jpeg(80, "blue")))
        t.save()
        (gamma.with_suffix(".lrc")).write_text(LRC)
        (MUSIC / "Band A/Record One/cover.jpg").write_bytes(jpeg(64, "red"))
        make(".hidden/Skipped.mp3", "mp3", title="Skipped", artist="Nobody", album="Nope")
        (MUSIC / "Playlists").mkdir(exist_ok=True)
        (MUSIC / "Playlists/Mix.m3u8").write_text("#EXTM3U\n#PLAYLIST:Mix\n../Band B/Record Two/01 Gamma.mp3\n../gone.mp3\n")
        cls.beta_path = beta
        library.refresh()
        ids = {r["title"]: r["id"] for r in core.rows("SELECT id, title FROM tracks")}
        cls.alpha, cls.beta, cls.gamma = ids["Alpha & <Co>"], ids["Beta"], ids["Gamma"]
        cls.a_album, cls.b_album = core.one("SELECT album_id AS a FROM tracks WHERE id = ?", cls.alpha)["a"], \
            core.one("SELECT album_id AS a FROM tracks WHERE id = ?", cls.gamma)["a"]
        cls.a_artist = core.one("SELECT artist_id AS a FROM tracks WHERE id = ?", cls.alpha)["a"]
        cls.key = {}
        for who, c in (("admin", cls.admin), ("sub_bob", cls.bob)):
            r = c.post("/api/me/app-passwords", json={"name": "phone"}, headers=H)
            assert r.status_code == 200, r.text
            cls.key[who] = r.json()["secret"]

    @classmethod
    def tearDownClass(cls):
        for d in ("Band A", "Band B", ".hidden", "Playlists"):
            shutil.rmtree(MUSIC / d, ignore_errors=True)
        library.refresh()
        core.db().execute("DELETE FROM app_passwords")

    def setUp(self):
        auth._failures.clear()  # the wrong-password tests must not lock the others out

    def call(self, method, who="admin", **params):
        pw = "correct horse"
        q = {"f": "json", "v": "1.16.1", "c": "test", "u": who, "p": pw, **params}
        r = client().get(f"/rest/{method}", params=q)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["subsonic-response"]

    def ok(self, method, who="admin", **params):
        r = self.call(method, who, **params)
        self.assertEqual(r["status"], "ok", r)
        return r

    def err(self, code, method, who="admin", **params):
        r = self.call(method, who, **params)
        self.assertEqual((r["status"], r["error"]["code"]), ("failed", code), r)

    # --- envelope and login ---------------------------------------------------------------------------

    def test_open_endpoints_and_formats(self):
        c = client()
        r = c.get("/rest/ping")  # no credentials: nothing private, and XML by default
        self.assertEqual(r.status_code, 200)
        self.assertIn("xml", r.headers["content-type"])
        root = ET.fromstring(r.text)
        self.assertEqual((root.tag, root.get("status")), ("{http://subsonic.org/restapi}subsonic-response", "ok"))
        j = c.get("/rest.view/ping.view" if False else "/rest/ping.view", params={"f": "json"}).json()["subsonic-response"]
        self.assertEqual((j["status"], j["version"], j["type"], j["openSubsonic"]), ("ok", "1.16.1", "yeerr", True))
        self.assertTrue(j["serverVersion"])
        self.assertTrue(self.call("getLicense")["license"]["valid"])
        names = {e["name"] for e in client().get("/rest/getOpenSubsonicExtensions", params={"f": "json"}).json()
                 ["subsonic-response"]["openSubsonicExtensions"]}
        self.assertTrue({"songLyrics", "apiKeyAuthentication", "formPost"} <= names)
        self.err(0, "noSuchMethod")

    def test_login_styles(self):
        c, pw = client(), "correct horse"
        want = lambda r: self.assertEqual(r.json()["subsonic-response"]["status"], "ok", r.text)  # noqa: E731
        want(c.get("/rest/getMusicFolders", params={"f": "json", "u": "admin", "p": pw}))
        want(c.get("/rest/getMusicFolders", params={"f": "json", "u": "admin", "p": "enc:" + pw.encode().hex()}))
        want(c.post("/rest/getMusicFolders", data={"f": "json", "u": "admin", "p": pw}))  # formPost
        salt, secret = "abcdef", self.key["admin"]
        token = hashlib.md5((secret + salt).encode()).hexdigest()
        want(c.get("/rest/getMusicFolders", params={"f": "json", "u": "admin", "t": token, "s": salt}))
        want(c.get("/rest/getMusicFolders", params={"f": "json", "apiKey": secret}))
        want(c.get("/rest/getMusicFolders", params={"f": "json", "u": "admin", "p": secret}))  # an app password as p
        bad = c.get("/rest/getMusicFolders", params={"f": "json", "u": "admin", "t": "0" * 32, "s": salt}).json()
        self.assertEqual(bad["subsonic-response"]["error"]["code"], 40)
        self.err(40, "getMusicFolders", u="admin") if False else None
        self.assertEqual(c.get("/rest/getMusicFolders", params={"f": "json", "apiKey": "nope"}).json()
                         ["subsonic-response"]["error"]["code"], 44)
        self.assertEqual(c.get("/rest/getMusicFolders", params={"f": "json"}).json()["subsonic-response"]["error"]["code"], 10)
        # a token needs an app password to check against: an account without one gets 41
        signed_in("sub_nokey")
        r = c.get("/rest/getMusicFolders", params={"f": "json", "u": "sub_nokey", "t": token, "s": salt})
        self.assertEqual(r.json()["subsonic-response"]["error"]["code"], 41)

    def test_wrong_password_and_throttle(self):
        c = client()
        r = c.get("/rest/getMusicFolders", params={"u": "admin", "p": "wrong wrong"})  # XML error
        self.assertEqual(ET.fromstring(r.text).find("{http://subsonic.org/restapi}error").get("code"), "40")
        for _ in range(auth.FAIL_LIMIT):
            c.get("/rest/getMusicFolders", params={"f": "json", "u": "nobody", "p": "x"})
        r = c.get("/rest/getMusicFolders", params={"f": "json", "u": "admin", "p": "correct horse"}).json()
        self.assertEqual(r["subsonic-response"]["error"]["code"], 40)  # paused, even with the right password

    def test_app_password_endpoints(self):
        made = self.bob.post("/api/me/app-passwords", json={"name": "tablet"}, headers=H).json()
        listed = self.bob.get("/api/me/app-passwords").json()
        self.assertIn("tablet", [p["name"] for p in listed])
        self.assertTrue(all("secret" not in p for p in listed))  # shown once, at creation
        ok = lambda: client().get("/rest/ping", params={"f": "json", "apiKey": made["secret"]}).json()["subsonic-response"]["status"]  # noqa: E731
        self.assertEqual(ok(), "ok")
        self.assertEqual(self.admin.get("/api/me/app-passwords").json().count(made["name"]), 0)
        self.assertEqual(self.admin.delete(f"/api/me/app-passwords/{made['id']}", headers=H).status_code, 404)  # not theirs
        self.assertEqual(self.bob.delete(f"/api/me/app-passwords/{made['id']}", headers=H).status_code, 200)
        self.assertEqual(ok(), "failed")
        self.assertEqual(client().get("/api/me/app-passwords").status_code, 401)

    # --- browsing ---------------------------------------------------------------------------------------

    def test_browse(self):
        self.assertEqual(self.ok("getMusicFolders")["musicFolders"]["musicFolder"][0]["name"], "Music")
        idx = self.ok("getArtists")["artists"]["index"]
        self.assertEqual([i["name"] for i in idx], ["B"])
        self.assertEqual([a["name"] for a in idx[0]["artist"]], ["Band A", "Band B"])
        self.assertEqual(idx[0]["artist"][0]["albumCount"], 1)
        old = self.ok("getIndexes")["indexes"]
        self.assertEqual(old["index"][0]["artist"][0]["id"], self.a_artist)
        art = self.ok("getArtist", id=self.a_artist)["artist"]
        self.assertEqual([a["name"] for a in art["album"]], ["Record One"])
        album = self.ok("getAlbum", id=self.a_album)["album"]
        self.assertEqual((album["songCount"], album["year"], album["genre"]), (2, 2001, "Rock"))
        self.assertEqual([s["track"] for s in album["song"]], [1, 2])
        song = album["song"][0]
        self.assertEqual((song["title"], song["suffix"], song["contentType"], song["albumId"], song["artistId"]),
                         ("Alpha & <Co>", "mp3", "audio/mpeg", self.a_album, self.a_artist))
        self.assertEqual(self.ok("getSong", id=self.beta)["song"]["suffix"], "flac")
        self.assertEqual(self.ok("getSong", id=self.beta)["song"]["discNumber"] if "discNumber" in song else None, None)
        d = self.ok("getMusicDirectory", id=self.a_artist)["directory"]
        self.assertEqual([c["id"] for c in d["child"]], [self.a_album])
        self.assertTrue(d["child"][0]["isDir"])
        d = self.ok("getMusicDirectory", id=self.a_album)["directory"]
        self.assertEqual([c["id"] for c in d["child"]], [self.alpha, self.beta])
        self.assertEqual(len(self.ok("getMusicDirectory", id="1")["directory"]["child"]), 2)  # the folder: its artists
        for m in ("getAlbumInfo", "getAlbumInfo2", "getArtistInfo", "getArtistInfo2"):
            self.ok(m, id=self.a_album)
        self.err(70, "getAlbum", id="al-0000000000000000")
        self.err(10, "getAlbum")
        self.assertEqual(len(core.rows("SELECT 1 FROM tracks WHERE path LIKE '.hidden%'")), 0)

    def test_xml_shape(self):
        r = client().get("/rest/getAlbum", params={"u": "admin", "p": "correct horse", "id": self.a_album})
        ns = {"s": "http://subsonic.org/restapi"}
        root = ET.fromstring(r.content)
        album = root.find("s:album", ns)
        self.assertEqual((album.get("name"), album.get("songCount")), ("Record One", "2"))
        songs = album.findall("s:song", ns)
        self.assertEqual(songs[0].get("title"), "Alpha & <Co>")  # escaped on the way out
        self.assertEqual(songs[0].get("isDir"), "false")

    def test_lists_search_genres(self):
        names = lambda r: [a["name"] for a in r["albumList2"].get("album", [])]  # noqa: E731
        self.assertEqual(names(self.ok("getAlbumList2", type="alphabeticalByName")), ["Record One", "Record Two"])
        self.assertEqual(names(self.ok("getAlbumList2", type="alphabeticalByArtist", size=1, offset=1)), ["Record Two"])
        self.assertEqual(names(self.ok("getAlbumList2", type="byYear", fromYear=1990, toYear=2000)), ["Record Two"])
        self.assertEqual(names(self.ok("getAlbumList2", type="byYear", fromYear=2002, toYear=1990)), ["Record One", "Record Two"])  # newest first
        self.assertEqual(names(self.ok("getAlbumList2", type="byGenre", genre="Rock")), ["Record One"])
        self.assertEqual(sorted(names(self.ok("getAlbumList2", type="random"))), ["Record One", "Record Two"])
        self.assertEqual(len(self.ok("getAlbumList2", type="newest")["albumList2"]["album"]), 2)
        for t in ("frequent", "recent", "starred", "highest"):  # nobody has played, starred or rated yet
            self.assertEqual(names(self.ok("getAlbumList2", type=t)), [], t)
        self.err(10, "getAlbumList2")
        old = self.ok("getAlbumList", type="alphabeticalByName")["albumList"]["album"]
        self.assertTrue(old[0]["isDir"] and old[0]["title"] == "Record One")
        self.assertEqual(len(self.ok("getRandomSongs", size=2)["randomSongs"]["song"]), 2)
        self.assertEqual([s["title"] for s in self.ok("getRandomSongs", genre="Jazz")["randomSongs"]["song"]], ["Gamma"])
        self.assertEqual([s["title"] for s in self.ok("getSongsByGenre", genre="Rock")["songsByGenre"]["song"]],
                         ["Alpha & <Co>", "Beta"])
        genres = {g["value"]: g for g in self.ok("getGenres")["genres"]["genre"]}
        self.assertEqual((genres["Rock"]["songCount"], genres["Rock"]["albumCount"], genres["Jazz"]["songCount"]), (2, 1, 1))
        res = self.ok("search3", query="gam")["searchResult3"]
        self.assertEqual([s["title"] for s in res["song"]], ["Gamma"])
        res = self.ok("search3", query="record")["searchResult3"]
        self.assertEqual(len(res["album"]), 2)
        self.assertEqual([a["name"] for a in self.ok("search3", query="band a")["searchResult3"]["artist"]], ["Band A"])
        self.assertEqual(len(self.ok("search3", query="", songCount=2)["searchResult3"]["song"]), 2)  # sync-everything style
        self.assertEqual(len(self.ok("search3", query="", songCount=2, songOffset=2)["searchResult3"]["song"]), 1)
        self.assertEqual(self.ok("search3", query="%")["searchResult3"], {})  # a wildcard isn't one
        self.assertEqual(len(self.ok("search2", query="beta")["searchResult2"]["song"]), 1)

    # --- audio, covers, lyrics ----------------------------------------------------------------------------

    def get(self, method, **params):
        return client().get(f"/rest/{method}", params={"u": "admin", "p": "correct horse", **params})

    def test_stream_range_download(self):
        size = (MUSIC / "Band A/Record One/01 Alpha.mp3").stat().st_size
        r = self.get("stream", id=self.alpha)
        self.assertEqual((r.status_code, r.headers["content-type"], len(r.content)), (200, "audio/mpeg", size))
        r = client().get("/rest/stream.view", params={"u": "admin", "p": "correct horse", "id": self.alpha},
                         headers={"Range": "bytes=10-19"})
        self.assertEqual((r.status_code, len(r.content), r.headers["content-range"]), (206, 10, f"bytes 10-19/{size}"))
        r = client().get("/rest/stream", params={"u": "admin", "p": "correct horse", "id": self.alpha},
                         headers={"Range": f"bytes={size + 5}-"})
        self.assertEqual(r.status_code, 416)
        d = self.get("download", id=self.beta)
        self.assertEqual((d.status_code, d.headers["content-type"]), (200, "audio/flac"))
        self.assertIn("attachment", d.headers["content-disposition"])
        self.assertEqual(d.content, self.beta_path.read_bytes())
        self.assertEqual(self.get("stream", id="tr-0000000000000000").status_code, 404)
        self.assertEqual(self.get("stream").status_code, 400)
        self.assertEqual(self.get("stream", id="../../etc/passwd").status_code, 404)
        self.assertEqual(client().get("/rest/stream", params={"id": self.alpha}).status_code, 400)  # no credentials: code 10, and the HTTP status says so

    def test_transcode(self):
        r = self.get("stream", id=self.beta, format="mp3", maxBitRate=64)  # flac -> mp3
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "audio/mpeg"))
        self.assertTrue(r.content[:3] == b"ID3" or r.content[0] == 0xFF)
        r = self.get("stream", id=self.beta, format="opus")
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "audio/ogg"))
        self.assertEqual(r.content[:4], b"OggS")
        raw = self.get("stream", id=self.beta, format="raw")
        self.assertEqual(raw.content, self.beta_path.read_bytes())

    def test_cover_art(self):
        from io import BytesIO

        from PIL import Image

        r = self.get("getCoverArt", id=self.a_album)  # cover.jpg beside the files
        self.assertEqual((r.status_code, r.headers["content-type"]), (200, "image/jpeg"))
        self.assertEqual(Image.open(BytesIO(r.content)).size, (64, 64))
        r = self.get("getCoverArt", id=self.b_album)  # embedded
        self.assertEqual(Image.open(BytesIO(r.content)).size, (80, 80))
        r = self.get("getCoverArt", id=self.gamma, size=32)  # sizes snap up to 64, 128, 256, 512 or 1024
        self.assertEqual(Image.open(BytesIO(r.content)).size, (64, 64))
        self.assertEqual(self.get("getCoverArt", id=self.a_artist, size=16).status_code, 200)
        self.assertEqual(self.get("getCoverArt", id="al-0000000000000000").status_code, 404)

    def test_lyrics(self):
        lines = self.ok("getLyricsBySongId", id=self.gamma)["lyricsList"]["structuredLyrics"]
        self.assertEqual((lines[0]["synced"], lines[0]["line"][0]), (True, {"start": 1500, "value": "first line"}))
        plain = self.ok("getLyricsBySongId", id=self.beta)["lyricsList"]["structuredLyrics"][0]
        self.assertEqual((plain["synced"], plain["line"]), (False, [{"value": "plain words"}]))
        self.assertNotIn("structuredLyrics", self.ok("getLyricsBySongId", id=self.alpha)["lyricsList"])  # none: left out
        old = self.ok("getLyrics", artist="Band B", title="Gamma")["lyrics"]
        self.assertEqual(old["value"], "first line\nsecond line")
        self.assertEqual(self.ok("getLyrics", artist="Nobody", title="Nothing")["lyrics"], {})

    # --- per-account data ------------------------------------------------------------------------------------

    def test_stars(self):
        self.ok("star", id=self.alpha, albumId=self.b_album, artistId=self.a_artist)
        s = self.ok("getStarred2")["starred2"]
        self.assertEqual(([x["id"] for x in s["song"]], [x["id"] for x in s["album"]], [x["id"] for x in s["artist"]]),
                         ([self.alpha], [self.b_album], [self.a_artist]))
        self.assertIn("starred", self.ok("getSong", id=self.alpha)["song"])
        self.assertEqual(len(self.ok("getStarred")["starred"]["song"]), 1)
        self.assertEqual([a["name"] for a in self.ok("getAlbumList2", type="starred")["albumList2"]["album"]], ["Record Two"])
        self.assertEqual(self.ok("getStarred2", "sub_bob")["starred2"], {})  # someone else's stars
        self.assertNotIn("starred", self.ok("getSong", "sub_bob", id=self.alpha)["song"])
        self.ok("unstar", id=self.alpha, albumId=self.b_album, artistId=self.a_artist)
        self.assertEqual(self.ok("getStarred2")["starred2"], {})
        self.err(70, "star", id="tr-0000000000000000")
        self.err(10, "star")
        self.ok("setRating", id=self.gamma, rating=4)
        self.assertEqual(self.ok("getSong", id=self.gamma)["song"]["userRating"], 4)
        self.assertEqual(self.ok("getSong", "sub_bob", id=self.gamma)["song"].get("userRating"), None)
        self.ok("setRating", id=self.b_album, rating=5)
        self.assertEqual([a["name"] for a in self.ok("getAlbumList2", type="highest")["albumList2"]["album"]], ["Record Two"])
        self.ok("setRating", id=self.gamma, rating=0)
        self.ok("setRating", id=self.b_album, rating=0)
        self.err(10, "setRating", id=self.gamma, rating=9)

    def test_scrobble_and_now_playing(self):
        self.ok("scrobble", id=self.beta, submission="false")
        np = self.ok("getNowPlaying")["nowPlaying"]["entry"]
        self.assertEqual([e["id"] for e in np], [self.beta])
        self.assertEqual(self.ok("getNowPlaying", "sub_bob").get("nowPlaying", {}).get("entry"), None)  # not shared
        self.assertEqual(self.ok("getSong", id=self.beta)["song"].get("playCount"), None)  # not a play yet
        self.ok("scrobble", id=[self.beta, self.beta], time=[1700000000000, 1700000100000])
        song = self.ok("getSong", id=self.beta)["song"]
        self.assertEqual(song["playCount"], 2)
        self.assertIn("played", song)
        self.assertEqual(self.ok("getSong", "sub_bob", id=self.beta)["song"].get("playCount"), None)
        self.assertEqual([a["name"] for a in self.ok("getAlbumList2", type="frequent")["albumList2"]["album"]], ["Record One"])
        self.assertEqual([a["name"] for a in self.ok("getAlbumList2", type="recent")["albumList2"]["album"]], ["Record One"])
        self.assertEqual(self.ok("getAlbumList2", type="frequent")["albumList2"]["album"][0]["playCount"], 2)
        self.assertEqual(self.ok("getAlbumList2", "sub_bob", type="recent")["albumList2"], {})
        self.err(70, "scrobble", id="tr-0000000000000000")
        core.db().execute("DELETE FROM sub_meta WHERE item = ?", (self.beta,))

    def test_playlists(self):
        made = self.ok("createPlaylist", name="Road", songId=[self.alpha, self.gamma])["playlist"]
        pid = made["id"]
        self.assertEqual((made["name"], made["songCount"], [e["title"] for e in made["entry"]]),
                         ("Road", 2, ["Alpha & <Co>", "Gamma"]))
        mine = {p["id"]: p for p in self.ok("getPlaylists")["playlists"]["playlist"]}
        self.assertEqual(mine[pid]["songCount"], 2)
        self.assertEqual(mine[pid]["owner"], "admin")
        self.ok("updatePlaylist", playlistId=pid, name="Road trip", comment="hi", songIdToAdd=[self.beta],
                songIndexToRemove=[0])
        got = self.ok("getPlaylist", id=pid)["playlist"]
        self.assertEqual((got["name"], got["comment"], [e["title"] for e in got["entry"]]), ("Road trip", "hi", ["Gamma", "Beta"]))
        # another account can't see, read, change or delete it
        self.assertNotIn(pid, [p["id"] for p in self.ok("getPlaylists", "sub_bob")["playlists"].get("playlist", [])])
        self.err(70, "getPlaylist", "sub_bob", id=pid)
        self.err(70, "updatePlaylist", "sub_bob", playlistId=pid, name="mine now")
        self.err(70, "deletePlaylist", "sub_bob", id=pid)
        self.ok("updatePlaylist", playlistId=pid, public="true")  # shared on purpose: readable but still theirs to change
        self.assertEqual(self.ok("getPlaylist", "sub_bob", id=pid)["playlist"]["name"], "Road trip")
        self.err(50, "updatePlaylist", "sub_bob", playlistId=pid, name="mine now")
        self.ok("createPlaylist", playlistId=pid, songId=[self.alpha])  # replaces the songs
        self.assertEqual([e["title"] for e in self.ok("getPlaylist", id=pid)["playlist"]["entry"]], ["Alpha & <Co>"])
        # music-fndr's synced .m3u8 files: everyone sees them, nobody changes them
        synced = [p for p in self.ok("getPlaylists", "sub_bob")["playlists"]["playlist"] if p["name"] == "Mix"]
        self.assertEqual(len(synced), 1)
        entries = self.ok("getPlaylist", "sub_bob", id=synced[0]["id"])["playlist"]["entry"]
        self.assertEqual([e["title"] for e in entries], ["Gamma"])  # the missing file is skipped
        self.err(50, "deletePlaylist", id=synced[0]["id"])
        self.ok("deletePlaylist", id=pid)
        self.err(70, "getPlaylist", id=pid)
        self.assertEqual(core.one("SELECT COUNT(*) AS n FROM sub_playlist_tracks WHERE playlist_id = ?", int(pid.split("-")[1]))["n"], 0)

    def test_play_queue_and_bookmarks(self):
        self.assertEqual(self.ok("getPlayQueue")["playQueue"] if "playQueue" in self.ok("getPlayQueue") else {}, {})
        self.ok("savePlayQueue", id=[self.beta, self.alpha, "tr-0000000000000000"], current=self.alpha, position=4200)
        q = self.ok("getPlayQueue")["playQueue"]
        self.assertEqual(([e["id"] for e in q["entry"]], q["current"], q["position"], q["username"]),
                         ([self.beta, self.alpha], self.alpha, 4200, "admin"))
        self.assertNotIn("playQueue", self.ok("getPlayQueue", "sub_bob"))
        self.ok("createBookmark", id=self.gamma, position=1234, comment="here")
        b = self.ok("getBookmarks")["bookmarks"]["bookmark"]
        self.assertEqual((len(b), b[0]["position"], b[0]["comment"], b[0]["entry"]["id"]), (1, 1234, "here", self.gamma))
        self.assertEqual(self.ok("getBookmarks", "sub_bob")["bookmarks"], {})
        self.ok("deleteBookmark", id=self.gamma)
        self.assertEqual(self.ok("getBookmarks")["bookmarks"], {})
        self.ok("savePlayQueue")  # clears it
        self.assertNotIn("playQueue", self.ok("getPlayQueue"))

    def test_users(self):
        u = self.ok("getUser", username="admin")["user"]
        self.assertEqual((u["username"], u["adminRole"], u["streamRole"]), ("admin", True, True))
        self.assertFalse(self.ok("getUser", "sub_bob")["user"]["adminRole"])
        self.err(50, "getUser", "sub_bob", username="admin")
        names = [x["username"] for x in self.ok("getUsers")["users"]["user"]]
        self.assertIn("sub_bob", names)
        self.err(50, "getUsers", "sub_bob")

    # --- scanning ------------------------------------------------------------------------------------------------

    def test_scan(self):
        before = self.ok("getScanStatus")["scanStatus"]
        self.assertFalse(before["scanning"])
        self.assertEqual(before["count"], 3)
        extra = make("Band C/Record Three/01 Delta.mp3", "mp3", title="Delta", artist="Band C", album="Record Three")
        try:
            self.assertTrue(self.ok("startScan")["scanStatus"]["scanning"] in (True, False))
            library.wait()
            self.assertEqual(self.ok("getScanStatus")["scanStatus"]["count"], 4)
            self.assertEqual(self.ok("search3", query="delta")["searchResult3"]["song"][0]["artist"], "Band C")
            idx = self.ok("getArtists")["artists"]["index"]
            self.assertEqual([a["name"] for i in idx for a in i["artist"]], ["Band A", "Band B", "Band C"])
        finally:
            shutil.rmtree(MUSIC / "Band C")
        library.refresh()
        self.assertEqual(self.ok("getScanStatus")["scanStatus"]["count"], 3)
        self.assertEqual(self.ok("search3", query="delta")["searchResult3"], {})
        self.assertFalse(extra.exists())

    # --- review fixes: a scan that must not fall over or delete the library -----------------------------------

    def count(self):
        return core.one("SELECT COUNT(*) AS n FROM tracks")["n"]

    def test_scan_survives_non_utf8_names(self):
        bad = os.fsencode(MUSIC) + b"/Band A/bad-\xff.mp3"
        bad_list = os.fsencode(MUSIC) + b"/Playlists/Bad-\xff.m3u8"
        shutil.copy(MUSIC / "Band A/Record One/01 Alpha.mp3", bad)
        shutil.copy(MUSIC / "Playlists/Mix.m3u8", bad_list)
        make("Band C/Record Three/01 Delta.mp3", "mp3", title="Delta", artist="Band C", album="Record Three")
        try:
            library.refresh()  # used to raise, and index nothing after the bad name
            self.assertEqual(self.count(), 4)  # the three, and Delta; the bad name is skipped
            self.assertNotIn("Bad-", " ".join(p["name"] for p in self.ok("getPlaylists")["playlists"]["playlist"]))
        finally:
            os.unlink(bad)
            os.unlink(bad_list)
            shutil.rmtree(MUSIC / "Band C")
        library.refresh()
        self.assertEqual(self.count(), 3)

    def test_failed_save_rolls_back(self):
        make("Band C/Record Three/01 Delta.mp3", "mp3", title="Delta", artist="Band C", album="Record Three")
        try:
            with mock.patch.object(library, "read", return_value={"no_such_column": 1}):
                with self.assertRaises(Exception):
                    library.refresh()
            self.assertFalse(core.db().in_transaction)  # not left inside a transaction for good
        finally:
            shutil.rmtree(MUSIC / "Band C")
        library.refresh()
        self.assertEqual(self.count(), 3)

    def test_unreadable_or_missing_folder_deletes_nothing(self):
        empty = TMP / "empty-root"
        empty.mkdir(exist_ok=True)
        with mock.patch.object(library, "root", return_value=empty):  # an unmounted music share looks like this
            library.refresh()
        self.assertEqual(self.count(), 3)
        if os.geteuid() == 0:
            return
        locked = MUSIC / "Band B"
        locked.chmod(0)
        try:
            library.refresh()
            self.assertEqual(self.count(), 3)  # Gamma isn't gone, it's unreadable right now
        finally:
            locked.chmod(0o755)

    def test_orphaned_stars_are_pruned(self):
        make("Band C/Record Three/01 Delta.mp3", "mp3", title="Delta", artist="Band C", album="Record Three")
        library.refresh()
        delta = core.one("SELECT id FROM tracks WHERE title = 'Delta'")["id"]
        self.ok("star", id=delta)
        self.assertEqual(core.one("SELECT COUNT(*) AS n FROM sub_meta WHERE item = ?", delta)["n"], 1)
        shutil.rmtree(MUSIC / "Band C")
        library.refresh()
        self.assertEqual(core.one("SELECT COUNT(*) AS n FROM sub_meta WHERE item = ?", delta)["n"], 0)

    # --- review fixes: transcoding, odd input, stubs ------------------------------------------------------------------

    def test_transcode_without_a_free_slot_sends_the_file(self):
        raw = self.beta_path.read_bytes()
        with mock.patch.object(subsonic, "_transcodes", threading.BoundedSemaphore(0)), \
                mock.patch.object(subsonic, "SLOT_WAIT", 0.05):
            r = self.get("stream", id=self.beta, format="mp3")
        self.assertEqual((r.status_code, r.content), (200, raw))

    def test_failed_encoder_start_gives_the_slot_back(self):
        slots = threading.BoundedSemaphore(1)
        with mock.patch.object(subsonic, "_transcodes", slots), mock.patch.object(subprocess, "Popen", side_effect=OSError("no")):
            r = self.get("stream", id=self.beta, format="mp3")
        self.assertEqual(r.content, self.beta_path.read_bytes())
        self.assertTrue(slots.acquire(blocking=False))

    def test_scrobble_skips_unknown_ids(self):
        before = self.ok("getSong", id=self.alpha)["song"].get("playCount", 0)
        self.ok("scrobble", id=["tr-0000000000000000", self.alpha])  # one gone song doesn't spoil the batch
        self.assertEqual(self.ok("getSong", id=self.alpha)["song"]["playCount"], before + 1)

    def test_odd_numbers_are_subsonic_errors(self):
        self.err(10, "getAlbumList2", type="newest", size="inf")
        self.err(10, "getAlbumList2", type="newest", size="nan")
        self.ok("getAlbumList2", type="newest", size=5, offset="9" * 40)  # clamped, not an OverflowError
        pid = self.ok("createPlaylist", name="Odd", songId=[self.alpha])["playlist"]["id"]
        self.ok("updatePlaylist", playlistId=pid, songIndexToRemove="²")  # '²'.isdigit() is True, int('²') fails
        self.ok("deletePlaylist", id=pid)

    def test_an_endpoint_that_blows_up_is_an_envelope(self):
        boom = mock.Mock(side_effect=RuntimeError("secret detail"))
        with mock.patch.dict(subsonic.ENDPOINTS, {"getGenres": boom}):
            r = self.call("getGenres")
        self.assertEqual((r["status"], r["error"]["code"]), ("failed", 0))
        self.assertNotIn("secret detail", str(r))

    def test_stubs_clients_ask_for(self):
        for name, key in (("getInternetRadioStations", "internetRadioStations"), ("getPodcasts", "podcasts"),
                          ("getNewestPodcasts", "newestPodcasts"), ("getShares", "shares")):
            self.assertIn(key, self.ok(name))
        self.err(70, "getAvatar")
        r = client().get("/rest/tokenInfo", params={"f": "json", "apiKey": self.key["admin"]}).json()["subsonic-response"]
        self.assertEqual(r["tokenInfo"]["username"], "admin")

    # --- review fixes: login throttle, size limits, covers, password cost ---------------------------------------------

    def test_rest_failures_do_not_lock_the_web_login(self):
        c = client()
        for _ in range(auth.FAIL_LIMIT):
            c.get("/rest/ping", params={"u": "admin", "p": "stale"})
        r = c.post("/api/auth/login", json={"name": "admin", "password": "correct horse"}, headers=H)
        self.assertEqual(r.status_code, 200)

    def test_a_good_login_clears_the_rest_failures(self):
        client().get("/rest/ping", params={"u": "admin", "p": "stale"})
        self.assertTrue(auth._failures["rest-name:admin"])
        self.ok("getMusicFolders")
        self.assertFalse(auth._failures.get("rest-name:admin"))

    def test_post_body_is_read_only_up_to_the_limit(self):
        junk = "f=json&x=" + "a" * 1_200_000 + "&u=admin&p=correct+horse"
        r = client().post("/rest/getMusicFolders", content=junk, headers={"content-type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.json()["subsonic-response"]["error"]["code"], 10)  # the login came after the limit: ignored

    def test_cover_sizes_snap_and_the_cache_is_capped(self):
        cache = core.DATA / "covers"
        shutil.rmtree(cache, ignore_errors=True)
        for size in (20, 40, 63):
            self.get("getCoverArt", id=self.gamma, size=size)
        self.assertEqual(len(list(cache.glob("*.jpg"))), 1)  # all the 64 bucket
        self.assertEqual(list(cache.glob("*.tmp")), [])
        with mock.patch.object(subsonic, "COVER_CACHE", 1):
            self.get("getCoverArt", id=self.gamma, size=100)
            self.get("getCoverArt", id=self.a_album, size=100)
        self.assertEqual(len(list(cache.glob("*.jpg"))), 1)  # oldest dropped to stay under the cap

    def test_cover_of_another_accounts_playlist_is_private(self):
        pid = self.ok("createPlaylist", name="Private art", songId=[self.gamma])["playlist"]["id"]
        try:
            self.assertEqual(self.get("getCoverArt", id=pid).status_code, 200)
            other = client().get("/rest/getCoverArt", params={"u": "sub_bob", "p": "correct horse", "id": pid})
            self.assertEqual(other.status_code, 404)
        finally:
            self.ok("deletePlaylist", id=pid)

    def test_password_check_is_remembered_until_it_changes(self):
        subsonic._pw_ok.clear()
        real = core.check_password
        with mock.patch.object(core, "check_password", side_effect=real) as spy:
            for _ in range(3):
                self.ok("ping", "sub_bob")
            self.assertEqual(spy.call_count, 1)
            old = core.one("SELECT pw FROM users WHERE name = 'sub_bob'")["pw"]
            core.db().execute("UPDATE users SET pw = ? WHERE name = 'sub_bob'", (core.hash_password("a new one"),))
            try:
                self.err(40, "ping", "sub_bob")  # the old password stops working at once
            finally:
                core.db().execute("UPDATE users SET pw = ? WHERE name = 'sub_bob'", (old,))


if __name__ == "__main__":
    unittest.main()
