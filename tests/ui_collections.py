"""Whole sets in a real browser: Parlor runs here against the Seerr stand-in (tests/seerr_standin.py). Checks the
Collections page (your own franchise first, studios, networks, finding a studio or a theme), a film's "Part of" link
and its studio and keyword chips, a set's page with its filters, Request all missing (counted and confirmed first;
Cancel sends nothing), the job in Downloads with its progress, Stop and Carry on, and that the job is still listed
in a new browser; no page errors. Screenshots at desktop and phone width:
   python tests/ui_collections.py [OUT_DIR]   (needs Playwright and its Chromium beside the app's packages)"""
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

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-collections-"))
OUT.mkdir(parents=True, exist_ok=True)
NAME, PASSWORD = "uiadmin", "Ui-sets-pass-42!"  # pragma: allowlist secret (a made-up password for a throwaway server)
ART = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 2 3'><defs><linearGradient id='g' x2='1' y2='1'><stop stop-color='#6b5cff'/>"
       "<stop offset='1' stop-color='#ff8a5c'/></linearGradient></defs><rect width='2' height='3' fill='url(#g)'/></svg>")
LOGO = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 60'><text x='100' y='42' text-anchor='middle' font-family='sans-serif' font-weight='800' "
        "font-size='34' fill='{}'>{}</text></svg>")

fake = Seerr()
for film in fake.collection + fake.studio + fake.keyword_films:
    film["posterPath"] = f"/poster-{film['id']}.jpg"
fake.status[("movie", 1001)] = fake.status[("movie", 1002)] = 5  # two Lantern films are in the library


def answer(method, url, **kw):
    """the stand-in, with pictures: a poster for the collection and logos for the studio and the network"""
    r = Seerr.__call__(fake, method, url, **kw)
    body = r.json()
    path = url.split("/api/v1/", 1)[1]
    if path == "collection/100":
        body.update(posterPath="/poster-100.jpg", backdropPath="/backdrop-100.jpg")
    if path in ("studio/7", "network/9"):
        body["logoPath"] = f"/logo-{path.replace('/', '-')}.png"
    return r


mock.patch.object(bulk.seerr.requests, "request", answer).start()
bulk.STUDIOS, bulk.NETWORKS, bulk.FRANCHISES = (7, 8), (9,), ()
bulk.PACE = 0.5  # slow enough to see a job going
core.db()
core.set_setting("seerr_url", "http://seerr.test")
core.set_setting("seerr_key", "fake")
auth.create_user(NAME, PASSWORD, True)
core.db().execute("UPDATE users SET seerr_id = 7 WHERE name = ?", (NAME,))

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"


def picture(route):
    url = route.request.url
    if "/logo-" in url:
        name = "HARBOUR" if "studio" in url else "TIN"
        return route.fulfill(content_type="image/svg+xml", body=LOGO.format("#1c1a24" if "1c1a24" in url else "#ffffff", name))
    route.fulfill(content_type="image/svg+xml", body=ART)


def posted():
    return [x["mediaId"] for x in fake.posted]


SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks = [], {}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    for label, size in (("desk", {"width": 1280, "height": 900}), ("phone", {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=size, color_scheme="dark")
        ctx.route("https://image.tmdb.org/**", picture)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/")
        page.wait_for_selector(".gate form")
        page.fill("#gName", NAME)
        page.fill("#gPw", PASSWORD)
        page.click(".gate .primary")
        page.wait_for_selector("#openQueue", timeout=30000)

        if label == "phone":  # a new browser: what desktop asked for is still listed
            page.click("#openQueue")
            page.wait_for_selector("#setJobs .job", timeout=30000)
            checks["phone: the jobs are still listed in a new browser"] = page.locator("#setJobs .job").count() == 2
            page.click("#closeQueue")
            time.sleep(0.5)

        # Collections: your own franchise, studios and networks, and finding a studio or a theme
        page.goto(BASE + "/#/sets")
        page.wait_for_selector(".setcard", timeout=30000)
        text = page.inner_text("main")
        checks[f"{label}: Collections shows your franchise with how much of it you have"] = "The Lantern Saga" in text and "2 in your library" in text
        checks[f"{label}: studios and networks show their logos"] = page.locator(".logotile img").count() == 2 and page.locator(".logotile").count() == 3
        page.fill("#setQ", "harbour")
        page.press("#setQ", "Enter")
        page.wait_for_selector("#setFound a", timeout=15000)
        checks[f"{label}: finding a studio or a theme"] = page.locator("#setFound a").all_inner_texts() == ["Harbour Pictures", "clockwork (universe)"]
        time.sleep(0.5)
        page.screenshot(path=str(OUT / f"collections-{label}.png"))

        # a film names its collection, studio and keywords
        page.goto(BASE + "/#/movie/1003")
        page.wait_for_selector(".partof", timeout=30000)
        checks[f"{label}: a film says which collection it's part of"] = (
            "Part of The Lantern Saga" in page.inner_text(".partof").replace("\n", " ") and page.get_attribute(".partof", "href") == "#/set/collection/100")
        checks[f"{label}: a film links its studio and keywords"] = page.locator(".chips.wrapped a").all_inner_texts() == ["Harbour Pictures", "clockwork (universe)"]
        page.locator(".partof").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / f"film-{label}.png"))

        # the collection: what you have, filters, and Request all missing
        page.click(".partof")
        page.wait_for_selector("#setGrid .pcard", timeout=30000)
        checks[f"{label}: a collection lists its films with what you have"] = (
            page.inner_text("#setCount") == "4 films" and page.locator("#setGrid .pill.available").count() == 2)
        page.uncheck("#setBar [name=released]")
        page.wait_for_function("document.querySelector('#setCount').textContent === '5 films'", timeout=15000)
        page.check("#setBar [name=released]")
        page.wait_for_function("document.querySelector('#setCount').textContent === '4 films'", timeout=15000)
        checks[f"{label}: Only what's out hides the unreleased one"] = page.locator("#setGrid .pcard").count() == 4
        page.screenshot(path=str(OUT / f"set-{label}.png"))

        if label == "desk":
            before = len(fake.posted)
            page.click("#askAll")
            page.wait_for_selector("#setAsk[open]", timeout=15000)
            ask = page.inner_text("#setAsk")
            checks["Request all missing counts first and says what's left out"] = (
                "Request 2 films?" in ask and "Lantern 3" in ask and "Lantern 4" in ask and "Lantern 1" not in ask and "2 already in your library" in ask)
            time.sleep(0.4)
            page.screenshot(path=str(OUT / "confirm-desk.png"))
            page.click("#setAsk button.ghost")
            time.sleep(0.3)
            checks["Cancel sends nothing"] = len(fake.posted) == before and not page.locator("#setJobs .job").count()
            page.click("#askAll")
            page.wait_for_selector("#setAsk[open]")
            page.go_back()  # the browser's Back with the question open: it goes with its page, and asks for nothing
            page.wait_for_function("!document.querySelector('#setAsk').open && !location.hash.startsWith('#/set/')", timeout=15000)
            checks["leaving the page closes the question and sends nothing"] = len(fake.posted) == before
            page.go_forward()
            page.wait_for_selector("#askAll")
            time.sleep(0.4)
            page.click("#askAll")
            page.wait_for_selector("#setAsk[open]")
            page.click("#setAsk button.primary")
            page.wait_for_selector("#setJobs .job", state="attached", timeout=15000)
            page.wait_for_function("document.querySelector('#setJobs .state').textContent.startsWith('2 requested')", timeout=30000)
            checks["confirming requests exactly the missing films"] = posted() == [1003, 1004]

            # a studio, narrowed by year: a longer job to watch, stop and carry on
            page.goto(BASE + "/#/set/studio/7")
            page.wait_for_selector("#setGrid .pcard", timeout=30000)
            checks["a studio shows its logo and all its films"] = page.locator(".setlogo img").count() == 1 and page.inner_text("#setCount") == "45 films"
            page.fill("#setBar [name=year_from]", "2005")
            page.press("#setBar [name=year_from]", "Tab")
            page.wait_for_function("document.querySelector('#setCount').textContent === '20 films'", timeout=15000)
            time.sleep(4.5)  # the cards have risen and the last toast has gone
            page.screenshot(path=str(OUT / "studio-desk.png"))
            page.click("#askAll")
            page.wait_for_selector("#setAsk[open]")
            checks["the filters decide what's asked for"] = "Request 20 films?" in page.inner_text("#setAsk")
            page.click("#setAsk button.primary")
            page.click("#toast button")  # View: opens Downloads
            page.wait_for_selector("#setJobs .job .meter", timeout=15000)
            page.wait_for_function("/Requested [1-9]\\d* of 20/.test(document.querySelector('#setJobs .state').textContent)", timeout=30000)
            time.sleep(0.5)
            page.screenshot(path=str(OUT / "drawer-desk.png"))
            page.click("#setJobs [data-setstop]")
            page.wait_for_selector("#setJobs [data-setretry]", timeout=15000)
            time.sleep(bulk.PACE + 0.5)
            stopped_at = len(fake.posted)
            state = page.inner_text("#setJobs .job >> nth=0")
            checks["Stop stops a job and says how far it got"] = "Stopped" in state and "not asked for yet" in state and 2 < stopped_at < 22
            time.sleep(1.2)
            checks["a stopped job requests nothing more"] = len(fake.posted) == stopped_at
            page.click("#setJobs [data-setretry]")
            page.wait_for_function("document.querySelector('#setJobs .state').textContent.startsWith('20 requested')", timeout=60000)
            checks["Carry on finishes it without asking twice"] = len(fake.posted) == 22 and len(set(posted())) == 22
            page.click("#closeQueue")
        else:
            # a keyword has films and shows; on a phone the dialog fits the screen
            page.goto(BASE + "/#/set/keyword/55")
            page.wait_for_selector("#setGrid .pcard", timeout=30000)
            page.select_option("#setBar [name=media]", "tv")
            page.wait_for_function("document.querySelector('#setCount').textContent === '2 shows'", timeout=15000)
            page.click("#askAll")
            page.wait_for_selector("#setAsk[open]")
            box = page.locator("#setAsk").bounding_box()
            checks["phone: the confirm fits the screen"] = "Request 2 shows?" in page.inner_text("#setAsk") and box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["height"] <= 844
            time.sleep(0.4)
            page.screenshot(path=str(OUT / "confirm-phone.png"))
            page.click("#setAsk button.primary")
            page.click("#openQueue")
            page.wait_for_function("document.querySelectorAll('#setJobs .state.done').length === 3", timeout=30000)
            checks["phone: shows are requested with every season"] = [x["seasons"] for x in fake.posted if x["mediaType"] == "tv"] == [[1, 2], [1, 2]]
            time.sleep(0.5)
            page.screenshot(path=str(OUT / "drawer-phone.png"))
            page.click("#clearDone")
            page.wait_for_selector("#setHead", state="hidden", timeout=15000)
            checks["Clear finished clears finished sets too"] = page.locator("#setJobs .job").count() == 0
            checks["phone: nothing scrolls sideways"] = page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        ctx.close()
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = all(checks.values()) and not errors and len(shots) == 11
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
