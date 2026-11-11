"""The menu in a real browser, against the Seerr stand-in: six sections; on a computer the sidebar lists only the open
section's pages; on a phone every section is a tab; Request holds every way of asking and one list of what was asked
for (a film and an album here); Games is one page under Library; and every address the old menu had still opens its
page. Screenshots at computer, tablet and phone width:
   python tests/ui_nav.py [OUT_DIR]   (needs Playwright and its Chromium beside the app's packages)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import bulk
import core
import uvicorn
from playwright.sync_api import sync_playwright
from seerr_standin import Seerr

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-nav-"))
OUT.mkdir(parents=True, exist_ok=True)
NAME, PASSWORD = "uiadmin", "Ui-nav-pass-42!"  # pragma: allowlist secret (a made-up password for a throwaway server)
ART = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 2 3'><rect width='2' height='3' fill='#6b5cff'/></svg>"
OLD = ["#/games/steam", "#/games/retro", "#/games/pc", "#/sets", "#/playlists", "#/library", "#/lidarr", "#/yt/rec", "#/requests/mine/pending", "#/media"]
fake = Seerr()


def answer(method, url, **kw):
    """the stand-in, with one request in its list and empty shelves where it has none"""
    r = Seerr.__call__(fake, method, url, **kw)
    path = url.split("/api/v1/", 1)[1]
    if path == "request" and method == "GET":
        body = {"results": [{"id": 1, "type": "movie", "status": 2, "createdAt": "2026-11-01T10:00:00.000Z", "seasons": [],
                             "media": {"mediaType": "movie", "tmdbId": fake.collection[0]["id"], "status": 3}, "requestedBy": {"id": 7, "displayName": NAME}}]}
        return mock.Mock(ok=True, status_code=200, content=b"x", text="", json=lambda: body)
    if r.status_code == 404 and path.startswith("discover/"):
        return mock.Mock(ok=True, status_code=200, content=b"x", text="", json=lambda: {"results": []})
    return r


mock.patch.object(bulk.seerr.requests, "request", answer).start()
bulk.STUDIOS, bulk.NETWORKS, bulk.FRANCHISES = (), (), ()
core.db()
core.set_setting("seerr_url", "http://seerr.test")
core.set_setting("seerr_key", "fake")
auth.create_user(NAME, PASSWORD, True)
core.db().execute("UPDATE users SET seerr_id = 7 WHERE name = ?", (NAME,))
uid = core.one("SELECT id FROM users WHERE name = ?", NAME)["id"]
core.db().execute("INSERT INTO music_requests(user_id, album, title, artist_name, state, created, updated) VALUES (?, 5, 'Paper Boats', 'The Harbour Lights', 'searching', ?, ?)",
                  (uid, time.time(), time.time()))
FILM = fake.collection[0]["title"]

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks = [], {}


def opened(page, hash_, wait="main h1"):
    page.goto(BASE + "/" + hash_)
    page.wait_for_selector(wait, timeout=30000)
    page.wait_for_function("h => location.hash === h", arg=hash_)
    time.sleep(0.3)
    return page.inner_text("main")


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    for label, size in (("desk", {"width": 1280, "height": 900}), ("tablet", {"width": 820, "height": 1100}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=size, color_scheme="dark" if label == "desk" else "light")
        ctx.route("https://image.tmdb.org/**", lambda route: route.fulfill(content_type="image/svg+xml", body=ART))
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(f"{label}: {e}"))
        page.goto(BASE + "/")
        page.wait_for_selector(".gate form")
        page.fill("#gName", NAME)
        page.fill("#gPw", PASSWORD)
        page.click(".gate .primary")
        page.wait_for_selector("#openQueue", timeout=30000)
        side = lambda: [t.strip() for t in page.locator("#side a:visible").all_inner_texts()]  # noqa: E731

        if label == "desk":
            page.wait_for_selector("#side a")
            checks["desk: on Home the sidebar is the six sections and nothing else"] = side() == ["Home", "Library", "Watch", "Music", "Request", "You"]
            page.click("#side a.side-head:has-text('Request')")
            page.wait_for_function("location.hash === '#/media'")
            page.wait_for_selector("main a[href='#/sets']")
            page.wait_for_selector("#side a[href='#/requests']")
            checks["desk: Request opens with its four pages listed under it, and only its own"] = side() == [
                "Home", "Library", "Watch", "Music", "Request", "Films & shows", "Music", "Games", "Your requests", "You"]
            checks["desk: Films & shows links Collections"] = page.locator("main a[href='#/sets']").count() >= 1
            page.screenshot(path=str(OUT / "request-desk.png"))
            page.click("#side a[href='#/lidarr']")
            page.wait_for_selector("#lidarrForm")
            checks["desk: asking for music is a page of Request"] = "Request music" in page.inner_text("main h1") and page.get_attribute("#side a[href='#/lidarr']", "aria-current") == "page"
            page.click("#side a.side-head:has-text('Library')")
            page.wait_for_function("location.hash === '#/jf'")
            page.wait_for_selector("#side a[href='#/games']")
            checks["desk: Library lists Games with its other pages"] = side()[1:8] == ["Library", "Home", "Films", "Shows", "Music", "Books", "Games"]
            page.click("#side a[href='#/games']")
            page.wait_for_selector("#g-pc")
            checks["desk: Games is one page with its three parts"] = page.locator("#g-steam, #g-retro, #g-pc").count() == 3 and page.get_attribute("#side a[href='#/games']", "aria-current") == "page"
            page.screenshot(path=str(OUT / "games-desk.png"))
        else:
            page.wait_for_selector(".nav a")
            tabs = [a.get_attribute("aria-label") for a in page.locator(".nav a:visible").all()]  # the labels are hidden at this width
            checks[f"{label}: every section is a tab"] = tabs == ["Home", "Library", "Watch", "Music", "Request", "You"]
            page.click(".nav a[data-nav='media']")
            page.wait_for_selector("main .subnav")
            checks[f"{label}: Request's pages are a row on its pages"] = [t.strip() for t in page.locator("main .subnav a").all_inner_texts()] == ["Films & shows", "Music", "Games", "Your requests"]
            page.screenshot(path=str(OUT / f"request-{label}.png"))
            opened(page, "#/games", "#g-pc")
            checks[f"{label}: Games shows the Library's row, with Games lit"] = page.get_attribute("main .chips a[href='#/games']", "aria-current") == "page"
            page.screenshot(path=str(OUT / f"games-{label}.png"))

        # one list of everything asked for; each kind alone; the old addresses
        text = opened(page, "#/requests", ".reqs li")
        page.wait_for_selector(".mreqs li")
        text = page.inner_text("main")
        checks[f"{label}: Your requests lists the film and the album together"] = FILM in text and "Paper Boats" in text
        page.screenshot(path=str(OUT / f"requests-{label}.png"))
        text = opened(page, "#/requests/music", ".mreqs li")
        checks[f"{label}: Music alone"] = "Paper Boats" in text and FILM not in text
        text = opened(page, "#/requests/mine/all", ".reqs li")
        checks[f"{label}: Films & shows alone, with its filters"] = FILM in text and "Paper Boats" not in text and "Waiting for approval" in text
        for old in OLD:
            text = opened(page, old, "main a[href='#/sets']" if old == "#/media" else "main h1")
            checks[f"{label}: {old} still opens"] = "Couldn't load this" not in text and len(text.strip()) > 20
            if label != "desk":
                checks[f"{label}: {old} doesn't scroll sideways"] = page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        ctx.close()
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots), "in", OUT)
ok = all(checks.values()) and not errors and len(checks) == 6 + 3 + 10 + 2 * (3 + 3 + 20)
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
