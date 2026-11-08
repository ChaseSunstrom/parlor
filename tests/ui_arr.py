"""The *arr views in a real browser: Parlor runs here with faked Sonarr, Radarr, Lidarr, Prowlarr and Seerr
(the fakes from test_activity.py), so the queue, calendar and Lidarr search have something to show.
Checks the Downloads drawer lists the *arr queue, Movies & TV shows "Downloading now" and "Coming up",
and Music → Request finds and requests an album; no page errors. Screenshots at desktop and phone width:
   python tests/ui_arr.py [OUT_DIR]   (needs Playwright and its Chromium beside the app's packages)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import activity
import arr
import auth
import core
import uvicorn
from playwright.sync_api import sync_playwright
from test_activity import KEYS, FakeArrs

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-arr-"))
OUT.mkdir(parents=True, exist_ok=True)
NAME, PASSWORD = "uiadmin", "Ui-arr-pass-42!"  # pragma: allowlist secret (a made-up password for a throwaway server)
POSTER = ("data:image/svg+xml," + "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 2 3'%3E%3Cdefs%3E%3ClinearGradient id='g' x2='1' y2='1'%3E"
          "%3Cstop stop-color='%236b5cff'/%3E%3Cstop offset='1' stop-color='%23ff8a5c'/%3E%3C/linearGradient%3E%3C/defs%3E"
          "%3Crect width='2' height='3' fill='url(%23g)'/%3E%3C/svg%3E")


class Seerr:
    """Just enough of Seerr for Movies & TV to open."""

    def __call__(self, method, url, headers=None, params=None, json=None, timeout=None, **kw):
        path = url.split("/api/v1/", 1)[1]
        if path == "status":
            body = {"version": "2.0.0"}
        elif path.startswith("movie/"):
            body = {"id": 550, "title": "A Film", "releaseDate": "2026-11-10", "overview": "A film the fakes made up."}
        else:
            body = {"results": [], "totalPages": 1, "pageInfo": {"pages": 0, "results": 0}}
        return mock.Mock(ok=True, status_code=200, content=b"x", json=lambda: body, headers={})


arrs, seerr_fake = FakeArrs(()), Seerr()


def requests(method, url, **kw):
    return (seerr_fake if "seerr.test" in url else arrs)(method, url, **kw)


root = env.TMP / "ui-stack"
for app, k in KEYS.items():
    (root / app).mkdir(parents=True, exist_ok=True)
    (root / app / "config.xml").write_text(f"<Config><ApiKey>{k}</ApiKey></Config>")
for p in (mock.patch.object(arr, "STACK", root), mock.patch.object(arr.requests, "request", requests),
          mock.patch.object(activity, "image", lambda images, *kinds: POSTER if images else None)):
    p.start()
arr._keys.clear()
core.db()
core.set_setting("seerr_url", "http://seerr.test")
core.set_setting("seerr_key", "fake")
auth.create_user(NAME, PASSWORD, True)

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
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    for label, size in (("desk", {"width": 1280, "height": 900}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=size, color_scheme="dark")
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/")
        page.wait_for_selector(".gate form")
        page.fill("#gName", NAME)
        page.fill("#gPw", PASSWORD)
        page.click(".gate .primary")
        page.wait_for_selector("#openQueue", timeout=30000)
        page.goto(BASE + "/#/media")
        page.wait_for_selector("#arrShelves .pcard", timeout=30000)
        shelves = page.inner_text("#arrShelves")
        checks[f"{label}: Movies & TV shows what's downloading and coming up"] = "Downloading now" in shelves and "Coming up" in shelves and "A Film" in shelves
        checks[f"{label}: a film opens its page"] = page.get_attribute("#arrShelves a.t", "href") in ("#/tv/1399", "#/movie/550")
        page.screenshot(path=str(OUT / f"media-{label}.png"))
        page.click("#openQueue")
        page.wait_for_selector("#arrJobs .job", timeout=30000)
        time.sleep(0.6)  # the drawer slides in
        drawer = page.inner_text("#arrJobs")
        checks[f"{label}: Downloads lists the *arr queue, stuck first"] = drawer.index("No files found are eligible") < drawer.index("75%")
        page.screenshot(path=str(OUT / f"drawer-{label}.png"))
        page.click("#arrJobs a[href^='#/movie/']")
        time.sleep(0.6)  # and slides out
        checks[f"{label}: a title in Downloads opens its page and closes the drawer"] = (
            page.evaluate("location.hash") == "#/movie/550" and not page.evaluate("document.querySelector('#queue').classList.contains('open')"))
        page.goto(BASE + "/#/lidarr/band%20record")
        page.wait_for_selector("#lidarrOut .lreq", timeout=30000)
        checks[f"{label}: Music → Request finds artists and albums"] = page.locator("#lidarrOut .lreq").count() == 2
        page.screenshot(path=str(OUT / f"request-{label}.png"))
        if label == "desk":
            page.locator("#lidarrOut .tile.album .lreq").click()
            page.wait_for_selector("#lidarrOut .tile.album .lreq:has-text('Requested')", timeout=15000)
            checks["requesting an album sends it to Lidarr"] = any(c[:3] == ("POST", "lidarr", "/album") for c in arrs.calls)
        ctx.close()
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = all(checks.values()) and not errors and len(shots) == 6
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
