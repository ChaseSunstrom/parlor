"""Asking for books in a real browser: Request → Books shows rows from a stand-in Open Library, the search finds a
book, its page has one Request button; Parlor finds a release, downloads it and brings the book into the library, and
the page says so at each step with no link or key in it; Your requests lists it; a book nothing fits says when it
looks again; with the Library closed there are no books to ask for. Open Library, Prowlarr and qBittorrent are
stand-ins and the covers are painted here, so nothing leaves this machine. Three widths:
   python tests/ui_bookreq.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import bookreq
import core
import gamelookup
import uvicorn
from playwright.sync_api import sync_playwright
from test_bookreq import OpenLibrary, Stack, hit
from test_gamelookup import NOW, Steam

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-bookreq-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-book-req-pass-42!"  # pragma: allowlist secret (made up)
COVER = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 2 3'><defs><linearGradient id='g' x2='1' y2='1'><stop stop-color='#2f6f5e'/>"
         "<stop offset='1' stop-color='#e9c46a'/></linearGradient></defs><rect width='2' height='3' fill='url(#g)'/></svg>")
ROOT = env.TMP / "ui-bookreq"
shutil.rmtree(ROOT, ignore_errors=True)
DOWNLOADS, BOOKS = ROOT / "downloads" / "books", ROOT / "books"
DOWNLOADS.mkdir(parents=True)
BOOKS.mkdir()
s, scans = Stack(DOWNLOADS), []
for obj, name, value in ((bookreq, "ol", OpenLibrary()), (gamelookup, "steam", Steam()), (gamelookup, "_now", lambda: NOW),  # the one search looks at games too
                         (bookreq.arr, "call", s.prowlarr), (bookreq.requests, "get", s.get), (bookreq.qbit, "api", s.qbit),
                         (bookreq, "rescan", lambda: scans.append(1)), (bookreq, "DOWNLOADS", DOWNLOADS), (bookreq, "BOOKS", BOOKS)):
    mock.patch.object(obj, name, value).start()
core.db()
auth.create_user("host", PASSWORD, True)
auth.create_user("reader", PASSWORD, False)
auth.create_user("no-library", PASSWORD, False)
core.db().execute("UPDATE users SET blocked = 'library' WHERE name = 'no-library'")

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    PORT = sock.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks, outside = [], {}, []


def someone(b, name, width=1366, height=1000, **kw):
    ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme="dark", **kw)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.route("https://**/*", lambda r: (r.fulfill(content_type="image/svg+xml", body=COVER) if "covers.openlibrary.org" in r.request.url
                                          else r.continue_() if "//fonts.g" in r.request.url  # the page's typeface
                                          else (outside.append(r.request.url) if "#/bookreq" in r.request.frame.url or "#/requests" in r.request.frame.url else None, r.abort())))  # Home has shelves of its own
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", name)
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return page


def opened(page, where, sel):
    page.goto("about:blank")  # a whole load each time: the same address again would leave the old page standing
    page.goto(BASE + "/" + where)
    page.wait_for_selector(sel, timeout=30000)
    page.wait_for_timeout(400)
    return page.inner_text("main")


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    reader = someone(b, "reader")
    text = opened(reader, "#/bookreq", ".pcard[data-book]")
    checks["Request → Books shows Open Library's rows, each book with its author"] = all(w in text for w in ("Read a lot this week", "Science fiction", "Mystery", "Pride and Prejudice", "Jane Austen", "Dune"))
    checks["Books is under Request → Browse, with Back"] = reader.get_attribute("#side a:not(.side-head)[href='#/request']", "aria-current") == "page" and reader.locator("#back").is_visible()
    shot(reader, "browse-desk")
    reader.fill("#q", "dune")
    reader.press("#q", "Enter")
    reader.wait_for_selector("#ask-books .pcard[data-book]", timeout=30000)
    found = sorted(reader.locator("#ask-books .pcard .t").all_inner_texts())
    reader.click("#ask-books a.more")
    reader.wait_for_selector(".pgrid .pcard[data-book]", timeout=30000)
    checks["the search box finds books by title: a row of them, and More opens the books' own list"] = (
        found == ["Dune", "Dune Messiah"] and "#/bookreq/search/dune" in reader.url and sorted(reader.locator(".pgrid .pcard .t").all_inner_texts()) == ["Dune", "Dune Messiah"])
    text = opened(reader, "#/bookreq/book/OL1W", ".hero [data-ask=book]")
    checks["a book's page: its author, when it came out, what it's about, a link to Open Library"] = all(
        w in text for w in ("Pride and Prejudice", "Jane Austen", "First published 1813", "3,000 editions", "About it.", "Fiction · Love · England", "On Open Library"))
    shot(reader, "book-desk")
    reader.click(".hero [data-ask=book]")
    reader.wait_for_selector(".greqs li.searching", timeout=30000)
    checks["a member's request starts at once: no admin's yes, and no second Request button"] = "Searching" in reader.inner_text(".greqs") and reader.locator("[data-ask=book]").count() == 0 and not s.searches

    s.hits = [hit("Pride and Prejudice and Zombies - Seth Grahame-Smith", seeders=900), hit("Jane Austen - Pride and Prejudice (Penguin Classics) (epub,mobi)", seeders=40)]
    bookreq.tick()
    text = opened(reader, "#/bookreq/book/OL1W", ".greqs li.downloading")
    html = reader.content().lower()
    checks["Parlor takes the release that is this book, and says which"] = "Downloading" in text and "Release: Jane Austen - Pride and Prejudice (Penguin Classics) (epub,mobi)" in text and len(s.torrents) == 1
    checks["no download link and no key reaches the page"] = "apikey" not in html and "prowlarr-key" not in html and "magnet:" not in html
    checks["a download can be swapped for another release"] = reader.locator(".greqs [data-greq=retry]").inner_text().strip() == "Try another"
    shot(reader, "downloading-desk")
    s.finish({"Pride and Prejudice.epub": b"EPUB", "Pride and Prejudice.mobi": b"MOBI", "Read me first.exe": b"MZ"})
    bookreq.tick(time.time() + 120)
    text = opened(reader, "#/bookreq/book/OL1W", ".greqs li.available")
    checks["when it's done the page says it's in the library, with a way to it"] = ("In your library" in text and reader.get_attribute(".hero .actions a.primary", "href") == "#/jf/books"
                                                                                  and reader.locator("[data-ask=book]").count() == 0)
    checks["only the book reached the library, and Jellyfin was asked to look"] = [str(f.relative_to(BOOKS)) for f in BOOKS.rglob("*") if f.is_file()] == [
        "Jane Austen/Pride and Prejudice/Pride and Prejudice.epub"] and scans == [1]

    opened(reader, "#/bookreq/book/OL2W", ".hero [data-ask=book]")
    s.hits = []
    reader.click(".hero [data-ask=book]")
    reader.wait_for_selector(".greqs li.searching", timeout=30000)
    bookreq.tick()
    text = opened(reader, "#/bookreq/book/OL2W", ".greqs li.searching .why")
    checks["a book nothing fits says when Parlor looks again"] = "looking again in 6 hours" in text and reader.locator(".greqs [data-greq=retry]").inner_text().strip() == "Search again"
    text = opened(reader, "#/bookreq", ".pcard[data-book='OL1W'] .pill")
    checks["the rows show what's asked for and what's here"] = (reader.inner_text(".pcard[data-book='OL1W'] .pill").strip() == "In library" and reader.inner_text(".pcard[data-book='OL2W'] .pill").strip() == "Searching"
                                                                and reader.locator(".pcard[data-book='OL1W'] [data-ask]").count() == 0)
    text = opened(reader, "#/requests", "#bookReqs .greqs li")
    checks["Your requests lists both under Books, with Open Books on the one that's here"] = ("Books" in reader.inner_text("#bookReqs h2") and reader.locator("#bookReqs .greqs li").count() == 2
                                                                                              and reader.locator("#bookReqs a[href='#/jf/books']").count() == 1)
    shot(reader, "requests-desk")
    opened(reader, "#/requests/books", "#bookReqs .greqs li.searching")
    checks["and the books' own address lists them alone"] = reader.locator("#bookReqs .greqs li").count() == 2 and reader.locator("#gameReqs, #musicReqs, .reqs:not(.greqs)").count() == 0
    reader.click("#bookReqs li.searching [data-greq=cancel]")
    reader.wait_for_function("document.querySelectorAll('#bookReqs .greqs li').length === 1", timeout=30000)
    checks["cancelling takes the request off the list"] = [r["work"] for r in core.rows("SELECT work FROM book_requests")] == ["OL1W"]

    host = someone(b, "host")
    text = opened(host, "#/requests/books", "#bookReqs .greqs li")
    checks["an admin sees whose request it is"] = "reader" in host.inner_text("#bookReqs .greqs li .s")
    shut = someone(b, "no-library")
    opened(shut, "#/media", "main h1")
    shut.goto(BASE + "/#/bookreq")
    shut.wait_for_timeout(1500)
    checks["Library closed to someone: no Books under Request, and the page doesn't open"] = (shut.locator("#side a[href='#/bookreq']").count() == 0 and shut.locator(".pcard[data-book]").count() == 0
                                                                                              and "Books" not in [t.strip() for t in shut.locator("main .subnav a").all_inner_texts()])

    for label, width, height, mobile in (("tablet", 820, 1180, False), ("phone", 390, 844, True)):
        pg = someone(b, "reader", width, height, is_mobile=mobile)
        for name, where, sel in (("browse", "#/bookreq", ".pcard[data-book]"), ("book", "#/bookreq/book/OL1W", ".greqs li"), ("requests", "#/requests", "#bookReqs .greqs li")):
            opened(pg, where, sel)
            shot(pg, f"{name}-{label}")
            checks[f"{label}: {name} has no sideways scroll"] = pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    b.close()
server.should_exit = True
checks["the book pages ask the internet for nothing but covers and the typeface"] = not outside
print("asked outside:", outside or "nothing")
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 24 and all(checks.values()) and not errors and len(shots) == 10
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
