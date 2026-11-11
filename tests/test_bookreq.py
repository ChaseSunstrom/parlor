"""Books you ask for, inside Parlor (app/bookreq.py): rows to browse and a search answered from a stand-in Open
Library; a request matched to one release on a stand-in Prowlarr, handed to a stand-in qBittorrent, and when it has
finished only the book files linked into the Books library. No link of Prowlarr's (they carry its key) reaches a
browser, and nothing but a book reaches the library."""
import json
import shutil
import unittest

import requests
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from yeerr_test_env import H, TMP, client, signed_in

import arr
import bookreq
import core

NOW = 1_800_000_000
DL = "http://prowlarr:9696/3/download?apikey=PROWLARR-KEY&link="  # pragma: allowlist secret (a made-up key)
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
WORKS = {
    "OL1W": {"title": "Pride and Prejudice", "author_name": ["Jane Austen"], "cover_i": 11, "first_publish_year": 1813, "edition_count": 3000, "ratings_average": 4.26},
    "OL2W": {"title": "Dune", "author_name": ["Frank Herbert"], "cover_i": 22, "first_publish_year": 1965},
    "OL3W": {"title": "Dune Messiah", "author_name": ["Frank Herbert"], "first_publish_year": 1969},
    "OL4W": {"title": "Frankenstein; or, The Modern Prometheus", "author_name": ["Mary Wollstonecraft Shelley"], "cover_i": 44, "first_publish_year": 1818},
}


class OpenLibrary:
    """Open Library, as far as Parlor asks it."""

    def __init__(self):
        self.calls, self.down = [], False

    def __call__(self, path, **params):
        self.calls.append((path, params))
        if self.down:
            raise bookreq.HTTPException(502, "Open Library didn't answer")
        doc = lambda k: {"key": f"/works/{k}", **WORKS[k]}  # noqa: E731
        if path == "/search.json":
            q = params["q"]
            if q.startswith("key:/works/"):
                return {"docs": [doc(q[11:])] if q[11:] in WORKS else []}
            return {"docs": [doc(k) for k, w in WORKS.items() if all(word in f"{w['title']} {w['author_name'][0]}".lower() for word in q.lower().split())]}
        if path.startswith("/trending/"):
            return {"works": [doc("OL1W"), doc("OL2W"), {"key": "/authors/OL9A", "title": "Not a work"}]}
        if path.startswith("/subjects/"):
            return {"works": [{"key": "/works/OL2W", "title": "Dune", "cover_id": 22, "authors": [{"name": "Frank Herbert"}], "first_publish_year": 1965}]}
        if path.startswith("/works/") and path[7:-5] in WORKS:
            return {"title": WORKS[path[7:-5]]["title"], "description": {"type": "/type/text", "value": "About it."}, "subjects": ["Fiction", "Love", "England"]}
        raise bookreq.HTTPException(404, "Open Library has no such page")


def hit(title, seeders=10, size=2_000_000, cats=(7020,), **more):
    """One search result, in Prowlarr's shape."""
    return {"title": title, "seeders": seeders, "leechers": 1, "size": size, "indexer": "Harbour Index", "categories": [{"id": c} for c in cats], "protocol": "torrent",
            "downloadUrl": DL + quote(title), "guid": "https://indexer.example/t/1", "infoHash": None, **more}


class Stack:
    """Prowlarr's search and download links, and qBittorrent, as far as Parlor uses them."""

    def __init__(self, downloads: Path):
        self.hits, self.searches, self.fetched, self.torrents, self.deleted, self.down, self.broken = [], [], [], {}, [], False, False
        self.downloads = downloads

    def answer(self, status=200, text="", body=None, **more):
        return mock.Mock(status_code=status, ok=status < 400, text=text or json.dumps(body), json=lambda: body, is_redirect=status in (301, 302), **more)

    def prowlarr(self, app, method, path, **kw):
        assert (app, method, path) == ("prowlarr", "GET", "/search"), (app, method, path)
        if self.down:
            raise arr.ArrError("Prowlarr didn't answer at http://prowlarr:9696 (ConnectionError)")
        self.searches.append(kw["params"])
        return self.hits

    def get(self, url, **kw):
        """A download link of Prowlarr's: a magnet behind a redirect, the .torrent itself, or a redirect somewhere else."""
        assert url.startswith("http://prowlarr:9696/") and kw.get("allow_redirects") is False, url
        self.fetched.append(url)
        if self.broken:
            raise requests.ConnectionError(f"HTTPConnectionPool(host='prowlarr', port=9696): Max retries exceeded with url: {url[20:]}")
        if "as-file" in url:
            body = b"d8:announce3:url4:infod4:name4:booke" + (b"0" * 9_000_000 if "huge" in url else b"")
            return self.answer(200, "torrent", raw=mock.Mock(read=lambda n, **kw: body[:n]), headers={})
        if "elsewhere" in url:
            return self.answer(302, "moved", content=b"", headers={"Location": "http://169.254.169.254/latest/meta-data"})
        return self.answer(301, "moved", content=b"", headers={"Location": MAGNET})

    def qbit(self, method, path, **kw):
        if path == "/torrents/add":
            d = kw["data"]
            self.torrents[d["tags"]] = {"hash": f"{len(self.torrents) + 1:040x}", "tags": d["tags"], "progress": 0, "state": "downloading", "content_path": "",
                                         "category": d["category"], "save_path": d["savepath"], "urls": d.get("urls"), "file": (kw.get("files") or {}).get("torrents")}
            return self.answer(200, "Ok.")
        if path == "/torrents/info":
            return self.answer(200, body=[t for tag, t in self.torrents.items() if tag == kw["params"]["tag"]])
        if path == "/torrents/delete":
            self.deleted.append((kw["data"]["hashes"], kw["data"]["deleteFiles"]))
            self.torrents = {tag: t for tag, t in self.torrents.items() if t["hash"] != kw["data"]["hashes"]}
            return self.answer(200, "")
        raise AssertionError(f"Parlor asked qBittorrent for something this test doesn't know: {method} {path}")

    def finish(self, files: dict, name="The Download"):
        """The (one) torrent is done, with these files in its folder."""
        (t,) = self.torrents.values()
        folder = self.downloads / name
        for rel, body in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes(body)
        t.update(progress=1, state="stalledUP", content_path=str(folder))
        return t


class Base(unittest.TestCase):
    def setUp(self):
        self.root = TMP / "bookreq"
        shutil.rmtree(self.root, ignore_errors=True)
        self.downloads, self.books = self.root / "downloads" / "books", self.root / "books"
        self.downloads.mkdir(parents=True)
        self.books.mkdir()
        self.ol, self.s, self.scans = OpenLibrary(), Stack(self.downloads), []
        for obj, name, value in ((bookreq, "ol", self.ol), (bookreq.arr, "call", self.s.prowlarr), (bookreq.requests, "get", self.s.get), (bookreq.qbit, "api", self.s.qbit),
                                 (bookreq, "rescan", lambda: self.scans.append(1)), (bookreq, "DOWNLOADS", self.downloads), (bookreq, "BOOKS", self.books)):
            p = mock.patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.admin, self.member = signed_in("admin"), signed_in("reader")
        core.db().execute("DELETE FROM book_requests")
        core.db().execute("UPDATE users SET blocked = '' WHERE name = 'reader'")
        bookreq._cache.data.clear()
        bookreq._asked.clear()

    def ask(self, who, work="OL1W", status=200):
        r = who.post(f"/api/books/requests/book/{work}", headers=H)
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def listed(self, who):
        return who.get("/api/books/requests").json()["items"]

    def state(self, who=None):
        return [(x["title"], x["state"]) for x in self.listed(who or self.admin)]

    def downloading(self, hits, work="OL1W", who=None):
        self.s.hits = hits
        rid = self.ask(who or self.member, work)["id"]
        bookreq.tick(NOW)
        return rid


class Browse(Base):
    def test_rows_come_from_open_library_and_are_kept_a_while(self):
        d = self.member.get("/api/books/requests/discover").json()
        self.assertEqual([b["title"] for b in d["trending"]], ["Pride and Prejudice", "Dune"], "works only")
        self.assertEqual([b["title"] for row in ("scifi", "mystery") for b in d[row]], ["Dune", "Dune"])
        austen = d["trending"][0]
        self.assertEqual((austen["id"], austen["author"], austen["year"], austen["status"]), ("OL1W", "Jane Austen", 1813, None))
        self.assertEqual(austen["poster"], "https://covers.openlibrary.org/b/id/11-M.jpg")
        self.assertEqual(d["scifi"][0]["author"], "Frank Herbert", "a subject's works name their authors another way")
        asked = len(self.ol.calls)
        self.member.get("/api/books/requests/discover")
        self.assertEqual(len(self.ol.calls), asked, "Open Library asks to be asked gently: the rows are kept for hours")

    def test_search_and_a_books_page(self):
        found = self.member.get("/api/books/requests/search", params={"q": "dune herbert"}).json()["items"]
        self.assertEqual([b["title"] for b in found], ["Dune", "Dune Messiah"])
        self.assertEqual(self.member.get("/api/books/requests/search", params={"q": " "}).json()["items"], [])
        b = self.member.get("/api/books/requests/book/OL1W").json()
        self.assertEqual((b["title"], b["author"], b["summary"], b["subjects"], b["request"]), ("Pride and Prejudice", "Jane Austen", "About it.", ["Fiction", "Love", "England"], None))
        self.assertEqual((b["rating"], b["editions"], b["store"]), (4.3, 3000, "https://openlibrary.org/works/OL1W"))
        for bad in ("OL99W", "OL1A", "works", "OL1W.json"):
            self.assertEqual(self.member.get(f"/api/books/requests/book/{bad}").status_code, 404, bad)
            self.ask(self.member, bad, status=404)

    def test_open_library_down_says_so(self):
        self.ol.down = True
        r = self.member.get("/api/books/requests/discover")
        self.assertEqual((r.status_code, r.json()["detail"]), (502, "Open Library didn't answer"))
        self.ask(self.member, status=502)

    def test_closed_with_the_library_section_and_to_nobody_signed_in(self):
        core.db().execute("UPDATE users SET blocked = 'library' WHERE name = 'reader'")
        for r in (self.member.get("/api/books/requests/discover"), self.member.post("/api/books/requests/book/OL1W", headers=H), self.member.get("/api/books/requests")):
            self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(client().get("/api/books/requests/discover").status_code, 401)


class Matching(Base):
    def best(self, hits, title="Dune", author="Frank Herbert", tried=()):
        got = bookreq.best(hits, title, author, tried)
        return got and got["title"]

    def test_the_book_itself_wins_over_its_sequel_and_its_box_set(self):
        hits = [hit("Dune Messiah by Frank Herbert EPUB", seeders=300), hit("Frank Herbert - Dune (Books 1-6)", seeders=500, size=3_300_000_000),
                hit("Dune by Frank Herbert EPUB", seeders=40), hit("Dune - Frank Herbert [pdf]", seeders=45)]
        self.assertEqual(self.best(hits), "Dune by Frank Herbert EPUB", "an EPUB (Parlor's reader opens those) with nothing else in its name")
        self.assertEqual(self.best(hits, tried=["Dune by Frank Herbert EPUB"]), "Dune - Frank Herbert [pdf]", "one that was tried is not taken again")

    def test_what_is_never_taken(self):
        for why, bad in {
            "another author's book with the title in its name": hit("Dune and Philosophy - Jeffery Nicholas epub"),
            "nobody seeding": hit("Dune by Frank Herbert EPUB", seeders=0),
            "an audiobook": hit("Dune by Frank Herbert", cats=(3030,)),
            "usenet": hit("Dune by Frank Herbert EPUB", protocol="usenet"),
            "far too big for a book": hit("Dune by Frank Herbert EPUB", size=900_000_000),
            "a pile of other things": hit("Frank Herbert Collection - Dune, Whipping Star, Dosadi, Santaroga, Hellstrom epub"),
            "no link": {**hit("Dune by Frank Herbert EPUB"), "downloadUrl": None, "magnetUrl": None},
            "not a result": {"title": None},
        }.items():
            self.assertIsNone(self.best([bad]), why)
        self.assertIsNone(self.best([]))

    def test_an_apostrophe_in_the_authors_name_however_the_release_writes_it(self):
        for name in ("A Wrinkle in Time - Madeleine L'Engle (epub)", "A Wrinkle in Time by Madeleine LEngle", "Madeleine L Engle - A Wrinkle in Time"):
            self.assertEqual(self.best([hit(name)], "A Wrinkle in Time", "Madeleine L'Engle"), name)
        self.assertIsNone(self.best([hit("A Wrinkle in Time - Somebody Else")], "A Wrinkle in Time", "Madeleine L'Engle"))

    def test_with_no_author_to_go_by_the_name_is_the_title_and_nothing_more(self):
        self.assertIsNone(self.best([hit("Beowulf Illustrated Retelling epub")], "Beowulf", ""))

    def test_a_link_with_more_than_a_magnet_in_it_is_not_a_link(self):
        for bad in (MAGNET + "\nhttp://169.254.169.254/latest", MAGNET + " http://gluetun:8000/v1/openvpn/status", "magnet:?" + "x" * 9000):
            self.assertIsNone(self.best([{**hit("Dune by Frank Herbert EPUB"), "downloadUrl": None, "magnetUrl": bad}]), bad[:70])

    def test_subtitles_middle_names_and_accents(self):
        self.assertEqual(self.best([hit("Frankenstein by Mary Shelley EPUB")], "Frankenstein; or, The Modern Prometheus", "Mary Wollstonecraft Shelley"), "Frankenstein by Mary Shelley EPUB")
        self.assertEqual(self.best([hit("Victor Hugo - Les Miserables (epub)")], "Les Misérables", "Victor Hugo"), "Victor Hugo - Les Miserables (epub)")
        self.assertEqual(self.best([hit("Beowulf (epub)"), hit("Beowulf and Grendel and Other Old Stories Retold")], "Beowulf", ""), "Beowulf (epub)", "no author known: the title, and little else")


class Asking(Base):
    def test_a_request_is_found_downloaded_and_only_the_book_reaches_the_library(self):
        self.s.hits = [hit("Pride and Prejudice and Zombies - Seth Grahame-Smith", seeders=900), hit("Jane Austen - Pride and Prejudice (Penguin Classics) (epub,mobi)", seeders=40),
                       hit("Pride And Prejudice By Jane Austen", seeders=12)]
        got = self.ask(self.member)
        self.assertEqual((got["state"], got["label"], got["title"], got["author"], got["by"], got["mine"]), ("searching", "Searching", "Pride and Prejudice", "Jane Austen", "reader", True))
        self.assertEqual(self.s.searches, [], "the page doesn't wait for the indexers: the worker asks them")
        bookreq.tick(NOW)
        self.assertEqual((self.s.searches[0]["query"], self.s.searches[0]["categories"]), ("Pride and Prejudice Austen", [7000, 7020]))
        (tag, t), = self.s.torrents.items()
        self.assertEqual((t["urls"], t["category"], t["save_path"], t["file"]), (MAGNET, "books", str(self.downloads), None), "the magnet behind Prowlarr's link, fetched by Parlor")
        self.assertRegex(tag, r"^parlor-book-\d+$")
        row = self.listed(self.member)[0]
        self.assertEqual((row["state"], row["release"]), ("downloading", "Jane Austen - Pride and Prejudice (Penguin Classics) (epub,mobi)"))
        bookreq.tick(NOW + 400)
        self.assertEqual(self.state(), [("Pride and Prejudice", "downloading")], "not done yet")
        self.s.finish({"Pride and Prejudice - Jane Austen.epub": b"EPUB", "Pride and Prejudice - Jane Austen.mobi": b"MOBI", "Read me first.exe": b"MZ", "cover.jpg": b"JPG",
                       "extras/notes.txt": b"hi"})
        bookreq.tick(NOW + 800)
        row = self.listed(self.member)[0]
        self.assertEqual((row["state"], row["label"], row["why"]), ("available", "In your library", ""))
        self.assertEqual(sorted(str(p.relative_to(self.books)) for p in self.books.rglob("*") if p.is_file()), ["Jane Austen/Pride and Prejudice/Pride and Prejudice.epub"],
                         "one format (the one the reader opens), named by Parlor; the .exe and the rest stay behind")
        self.assertEqual((self.books / "Jane Austen/Pride and Prejudice/Pride and Prejudice.epub").read_bytes(), b"EPUB")
        self.assertEqual(self.scans, [1], "Jellyfin is asked to look")
        self.assertEqual(self.member.get("/api/books/requests/search", params={"q": "pride"}).json()["items"][0]["status"], "available")
        bookreq.tick(NOW + 90000)
        self.assertEqual((len(self.s.searches), self.scans), (1, [1]), "a finished request is left alone")
        for who in (self.admin, self.member):
            for r in (who.get("/api/books/requests"), who.get("/api/books/requests/book/OL1W")):
                self.assertNotIn("apikey", r.text)
                self.assertNotIn("prowlarr", r.text.lower())

    def test_asking_twice_is_one_request_and_others_only_see_that_its_asked_for(self):
        first = self.ask(self.member)
        again = self.ask(self.admin)
        self.assertEqual((again["id"], again["mine"], again["by"]), (first["id"], False, "reader"), "an admin sees whose it is")
        other = signed_in("another-reader")
        seen = self.ask(other)
        self.assertEqual((seen["id"], seen["state"], seen["by"], seen["why"], seen["mine"]), (first["id"], "searching", None, "", False))
        self.assertEqual((len(self.listed(self.admin)), self.listed(other)), (1, []))

    def test_a_member_has_so_many_open_requests_and_no_more(self):
        with mock.patch.object(bookreq, "MOST", 2):
            self.ask(self.member, "OL1W")
            self.ask(self.member, "OL2W")
            r = self.member.post("/api/books/requests/book/OL3W", headers=H)
            self.assertEqual(r.status_code, 429, r.text)
            self.ask(self.admin, "OL3W")

    def test_asking_and_cancelling_over_and_over_runs_out(self):
        with mock.patch.object(bookreq, "PER_HOUR", 3):
            for _ in range(3):
                self.member.delete(f"/api/books/requests/{self.ask(self.member)['id']}", headers=H)
            self.assertEqual(self.member.post("/api/books/requests/book/OL1W", headers=H).status_code, 429, "each one would search every indexer")
            self.ask(self.admin)
            self.ask(self.member)  # one that's already asked for costs nothing

    def test_cancel_takes_the_download_with_it_and_only_your_own(self):
        rid = self.downloading([hit("Pride and Prejudice - Jane Austen epub")])
        other = signed_in("another-reader")
        self.assertEqual(other.delete(f"/api/books/requests/{rid}", headers=H).status_code, 404)
        self.assertEqual(other.post(f"/api/books/requests/{rid}/retry", headers=H).status_code, 404)
        (t,) = self.s.torrents.values()
        self.assertEqual(self.member.delete(f"/api/books/requests/{rid}", headers=H).status_code, 200)
        self.assertEqual((self.s.deleted, self.s.torrents, self.listed(self.admin)), ([(t["hash"], "true")], {}, []))


class Trouble(Base):
    def test_nothing_found_looks_again_later(self):
        self.downloading([hit("Pride and Prejudice and Zombies - Seth Grahame-Smith")])
        row = self.listed(self.member)[0]
        self.assertEqual(row["state"], "searching")
        self.assertIn("looking again", row["why"])
        self.s.hits = [hit("Pride and Prejudice - Jane Austen epub")]
        bookreq.tick(NOW + 600)
        self.assertEqual(len(self.s.searches), 1, "not before its time")
        bookreq.tick(NOW + bookreq.LOOK_AGAIN + 1)
        self.assertEqual(self.state(), [("Pride and Prejudice", "downloading")])

    def test_a_torrent_file_is_handed_over_as_a_file_and_a_link_that_leaves_prowlarr_is_refused(self):
        self.downloading([hit("Pride and Prejudice - Jane Austen epub elsewhere", seeders=90), hit("Pride and Prejudice - Jane Austen epub as-file", seeders=5)])
        (t,) = self.s.torrents.values()
        self.assertEqual((t["urls"], t["file"][1][:11]), (None, b"d8:announce"), "the .torrent itself, uploaded")
        self.assertEqual(len(self.s.fetched), 2, "nothing was asked of the address the first link pointed away to")
        self.assertEqual(self.listed(self.member)[0]["release"], "Pride and Prejudice - Jane Austen epub as-file")

    def test_a_book_already_on_the_shelf_is_left_as_it_is(self):
        mine = self.books / "Jane Austen" / "Pride and Prejudice" / "Pride and Prejudice.epub"
        mine.parent.mkdir(parents=True)
        mine.write_bytes(b"MINE")
        self.downloading([hit("Pride and Prejudice - Jane Austen epub")])
        self.s.finish({"book.epub": b"THEIRS"})
        bookreq.tick(NOW + 400)
        self.assertEqual((self.listed(self.member)[0]["state"], mine.read_bytes()), ("available", b"MINE"), "your own file is never replaced")

    def test_a_download_far_bigger_than_it_said_is_dropped(self):
        self.downloading([hit("Pride and Prejudice - Jane Austen epub", seeders=50), hit("Pride And Prejudice By Jane Austen", seeders=12)])
        (t,) = self.s.torrents.values()
        t.update(progress=0.01, size=40 * 2 ** 30)  # the indexer said 2 MB
        bookreq.tick(NOW + 400)
        row = self.listed(self.member)[0]
        self.assertEqual((row["state"], self.s.deleted), ("searching", [(t["hash"], "true")]))
        self.assertIn("bigger than a book", row["why"])
        bookreq.tick(NOW + 401)
        self.assertEqual(self.listed(self.member)[0]["release"], "Pride And Prejudice By Jane Austen")

    def test_a_torrent_file_too_big_to_be_one_is_not_read_whole(self):
        self.downloading([hit("Pride and Prejudice - Jane Austen epub as-file huge")])
        self.assertEqual((self.s.torrents, self.listed(self.member)[0]["state"]), ({}, "searching"))

    def test_a_download_with_no_book_in_it_is_stuck_and_nothing_reaches_the_library(self):
        self.downloading([hit("Pride and Prejudice - Jane Austen epub")])
        self.s.finish({"Pride and Prejudice.epub.exe": b"MZ", "pride.scr": b"MZ", "README.txt": b"run the exe"})
        bookreq.tick(NOW + 400)
        row = self.listed(self.member)[0]
        self.assertEqual(row["state"], "failed")
        self.assertIn("no book in it", row["why"])
        self.assertEqual([p for p in self.books.rglob("*") if p.is_file()], [])
        self.assertEqual(self.scans, [])

    def test_a_download_that_says_its_somewhere_else_is_not_followed(self):
        self.downloading([hit("Pride and Prejudice - Jane Austen epub")])
        outside = self.root / "elsewhere"
        outside.mkdir()
        (outside / "passwd.epub").write_bytes(b"secret")
        (t,) = self.s.torrents.values()
        t.update(progress=1, state="stalledUP", content_path=str(self.downloads / ".." / ".." / "elsewhere"))
        bookreq.tick(NOW + 400)
        self.assertEqual(self.listed(self.member)[0]["state"], "failed")
        self.assertEqual([p for p in self.books.rglob("*") if p.is_file()], [])
        # and a link inside the folder that points out of it
        self.s.torrents.clear()
        core.db().execute("DELETE FROM book_requests")
        self.downloading([hit("Pride and Prejudice - Jane Austen epub")])
        t = self.s.finish({"real.txt": b"x"})
        (Path(t["content_path"]) / "book.epub").symlink_to(outside / "passwd.epub")
        bookreq.tick(NOW + 400)
        self.assertEqual([p for p in self.books.rglob("*") if p.is_file()], [], "a link in the download is not a book")

    def test_search_again_drops_a_dead_download_and_takes_the_next_one(self):
        rid = self.downloading([hit("Pride and Prejudice - Jane Austen epub", seeders=50), hit("Pride And Prejudice By Jane Austen", seeders=12)], who=self.admin)
        (dead,) = self.s.torrents.values()
        self.assertEqual(self.admin.post(f"/api/books/requests/{rid}/retry", headers=H).json()["state"], "searching")
        self.assertEqual(self.s.deleted, [(dead["hash"], "true")])
        bookreq.tick(NOW + 5)
        self.assertEqual(self.listed(self.admin)[0]["release"], "Pride And Prejudice By Jane Austen", "not the one that was dropped")

    def test_a_member_cant_lean_on_search_again(self):
        rid = self.downloading([])
        self.assertEqual(self.member.post(f"/api/books/requests/{rid}/retry", headers=H).status_code, 429)
        core.db().execute("UPDATE book_requests SET updated = updated - ?", (bookreq.EVERY + 1,))
        self.assertEqual(self.member.post(f"/api/books/requests/{rid}/retry", headers=H).status_code, 200)

    def test_a_download_removed_in_qbittorrent_is_stuck_with_words_and_can_be_looked_for_again(self):
        rid = self.downloading([hit("Pride and Prejudice - Jane Austen epub")])
        self.s.torrents.clear()
        bookreq.tick(NOW + 400)
        self.assertEqual(self.state(), [("Pride and Prejudice", "downloading")], "just added: qBittorrent may not list it yet")
        core.db().execute("UPDATE book_requests SET updated = updated - 3600")
        bookreq.tick(NOW + 800)
        row = self.listed(self.member)[0]
        self.assertEqual(row["state"], "failed")
        self.assertIn("qBittorrent no longer has", row["why"])
        core.db().execute("UPDATE book_requests SET updated = updated - 3600")
        self.assertEqual(self.member.post(f"/api/books/requests/{rid}/retry", headers=H).json()["state"], "searching")

    def test_a_download_that_gets_nowhere_is_dropped_for_the_next_release(self):
        self.downloading([hit("Pride and Prejudice - Jane Austen epub", seeders=50), hit("Pride And Prejudice By Jane Austen", seeders=12)])
        (dead,) = self.s.torrents.values()
        core.db().execute("UPDATE book_requests SET updated = updated - ?", (bookreq.GIVE_UP + 1,))
        bookreq.tick(NOW + 400)
        row = self.listed(self.member)[0]
        self.assertEqual((row["state"], self.s.deleted), ("searching", [(dead["hash"], "true")]))
        self.assertIn("looking for another", row["why"])
        bookreq.tick(NOW + 401)
        self.assertEqual(self.listed(self.member)[0]["release"], "Pride And Prejudice By Jane Austen")

    def test_prowlarr_failing_on_the_link_says_nothing_of_the_link(self):
        self.s.broken = True
        self.downloading([hit("Pride and Prejudice - Jane Austen epub")])
        row = self.listed(self.admin)[0]
        self.assertEqual((row["state"], self.s.torrents), ("searching", {}))
        self.assertIn("trying again", row["why"])
        for secret in ("apikey", "PROWLARR-KEY", "download?"):
            self.assertNotIn(secret, json.dumps(row))

    def test_prowlarr_down_is_tried_again_later(self):
        self.ask(self.member)
        self.s.down = True
        bookreq.tick(NOW)
        row = self.listed(self.member)[0]
        self.assertEqual(row["state"], "searching")
        self.assertIn("trying again", row["why"])
        self.assertNotIn("9696", row["why"], "no addresses in what a member reads")
        self.s.down, self.s.hits = False, [hit("Pride and Prejudice - Jane Austen epub")]
        bookreq.tick(NOW + 1)
        self.assertEqual(self.s.torrents, {}, "after a wait, not at once")
        bookreq.tick(NOW + 3 * bookreq.EVERY)
        self.assertEqual((self.state(), self.listed(self.member)[0]["why"]), ([("Pride and Prejudice", "downloading")], ""))


if __name__ == "__main__":
    unittest.main()
