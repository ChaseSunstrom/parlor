"""Artwork fetched by Parlor: /api/art?u=<address> answers only for a few image hosts, only to people signed in, with
a size cap, and keeps what it fetched on disk so a screen with no internet still shows its pictures. Never an open
proxy: any other host, scheme, port or a redirect elsewhere is refused. The network is a fake."""
import unittest
from unittest import mock

import yeerr_test_env as env
from yeerr_test_env import signed_in

import art
import core

GOOD = "https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 100


class Fake:
    """what requests.get returns: a status, headers and a body read in chunks"""

    def __init__(self, body=JPEG, status=200, kind="image/jpeg", length=True):
        self.status_code, self.body, self.closed = status, body, False
        self.headers = {"Content-Type": kind, **({"Content-Length": str(len(body))} if length else {})}
        self.read = 0

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), chunk_size):
            self.read += min(chunk_size, len(self.body) - i)
            yield self.body[i:i + chunk_size]

    def close(self):
        self.closed = True


class Art(unittest.TestCase):
    def setUp(self):
        self.me = signed_in("art-person")
        self.dir = core.DATA / "cache" / "art"
        self.addCleanup(lambda: [p.unlink() for p in self.dir.glob("*")] if self.dir.exists() else None)
        self.get = mock.patch.object(art.requests, "get", side_effect=lambda *a, **k: Fake())
        self.fetch = self.get.start()
        self.addCleanup(self.get.stop)

    def ask(self, url, client=None):
        return (client or self.me).get("/api/art", params={"u": url})

    def test_an_image_from_a_listed_host_is_fetched_and_served(self):
        r = self.ask(GOOD)
        self.assertEqual((r.status_code, r.content, r.headers["content-type"]), (200, JPEG, "image/jpeg"))
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertIn("private", r.headers["cache-control"], "a shared cache never keeps it")
        for host in ("yt3.ggpht.com", "image.tmdb.org"):
            self.assertEqual(self.ask(f"https://{host}/x/y.jpg").status_code, 200, host)

    def test_only_people_signed_in_may_ask(self):
        r = self.ask(GOOD, env.client())
        self.assertEqual(r.status_code, 401)
        self.fetch.assert_not_called()

    def test_any_other_address_is_refused_and_never_fetched(self):
        for url in ("https://evil.test/a.jpg", "http://i.ytimg.com/a.jpg", "https://i.ytimg.com.evil.test/a.jpg",
                    "https://i.ytimg.com@evil.test/a.jpg", "https://evil.test@i.ytimg.com/a.jpg", "https://i.ytimg.com:8443/a.jpg",
                    "https://i.ytimg.com:80@evil.test/a.jpg", "file:///etc/passwd", "//i.ytimg.com/a.jpg", "https://127.0.0.1/a.jpg",
                    "https://I.YTIMG.COM.evil.test/a.jpg", "", "https://i.ytimg.com/" + "a" * 600, "https://i.ytimg.com/a\n.jpg"):
            self.assertEqual(self.ask(url).status_code, 422, url)
        self.fetch.assert_not_called()

    def test_a_redirect_is_not_followed(self):
        self.fetch.side_effect = lambda *a, **k: Fake(b"", 302)
        self.assertEqual(self.ask(GOOD).status_code, 502)
        self.assertIs(self.fetch.call_args.kwargs["allow_redirects"], False)

    def test_too_big_is_refused_by_its_header_or_while_reading_and_nothing_is_kept(self):
        with mock.patch.object(art, "MAX_BYTES", 50):
            self.assertEqual(self.ask(GOOD).status_code, 413, "its header says it's big")
            big = Fake(length=False)  # no honest header: stop reading at the cap
            self.fetch.side_effect = lambda *a, **k: big
            self.assertEqual(self.ask(GOOD).status_code, 413)
            self.assertLess(big.read, 50 + art.CHUNK + 1, "it stopped reading")
        self.assertEqual(list(self.dir.glob("*")), [], "no half file")

    def test_only_pictures_are_taken(self):
        for kind in ("text/html", "image/svg+xml", "application/octet-stream"):
            self.fetch.side_effect = lambda *a, k=kind, **kw: Fake(b"<svg onload=alert(1)>", kind=k)
            self.assertEqual(self.ask(GOOD).status_code, 415, kind)
        self.assertEqual(list(self.dir.glob("*")), [])

    def test_the_other_side_failing_is_a_plain_error_and_not_kept(self):
        self.fetch.side_effect = lambda *a, **k: Fake(b"", 404)
        self.assertEqual(self.ask(GOOD).status_code, 404)
        self.fetch.side_effect = art.requests.ConnectionError("no route")
        r = self.ask(GOOD)
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("no route", r.text)
        self.assertEqual(list(self.dir.glob("*")), [])

    def test_a_kept_picture_is_served_with_no_internet(self):
        self.ask(GOOD)
        self.fetch.side_effect = art.requests.ConnectionError("offline")
        r = self.ask(GOOD)
        self.assertEqual((r.status_code, r.content), (200, JPEG))
        self.assertEqual(self.fetch.call_count, 1, "one fetch, then the disk")

    def test_the_folder_stays_under_its_cap_oldest_first(self):
        with mock.patch.object(art, "CACHE_BYTES", len(JPEG) * 2 + 4):
            for n in range(4):
                self.fetch.side_effect = lambda *a, **k: Fake(JPEG + bytes([n]))
                self.ask(f"https://i.ytimg.com/vi/{n}/a.jpg")
        left = list(self.dir.glob("*"))
        self.assertEqual(len(left), 2)
        self.assertLessEqual(sum(p.stat().st_size for p in left), len(JPEG) * 2 + 4)
        self.fetch.side_effect = lambda *a, **k: Fake(JPEG)
        before = self.fetch.call_count
        self.ask("https://i.ytimg.com/vi/3/a.jpg")
        self.assertEqual(self.fetch.call_count, before, "the newest is still there")

    def test_a_cache_file_is_private(self):
        self.ask(GOOD)
        (kept,) = self.dir.glob("*")
        self.assertEqual(kept.stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
