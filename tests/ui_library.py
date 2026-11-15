"""The Library in a real browser: Parlor runs here against a Jellyfin stand-in (a small HTTP server speaking the
Jellyfin routes Parlor uses) that serves real media made with ffmpeg: a film, two episodes, an album and an EPUB.
A member plays the film with seeking and its place is saved (Continue watching then shows it), opens the show,
its season and plays an episode, plays the album in the dock and keeps it playing across pages, reads the book and
turns a page, and plays a song from the music folder; a member without Library is refused. No page errors;
screenshots at desktop and phone width:
   python tests/ui_library.py [OUT_DIR]   (needs Playwright, its Chromium and ffmpeg)"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import uvicorn
from jf_standin import ALBUM, BOOK, FILM, JF, SAVES, SERIES, T, ff, point, port
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-library-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-library-pass-42!"  # pragma: allowlist secret (made up)
core.db()
ids = {n: auth.create_user(n, PASSWORD, n == "uiadmin") for n in ("uiadmin", "viewer", "closed")}
core.db().execute("UPDATE users SET blocked = 'library' WHERE id = ?", (ids["closed"],))
point(core)
(Path.cwd() / "Lamps" / "Shore").mkdir(parents=True, exist_ok=True)  # a song in the music folder, tagged
ff("-f", "lavfi", "-i", "sine=frequency=392:duration=15", "-metadata", "title=Tidewater", "-metadata", "artist=The Lamps", "-metadata", "album_artist=The Lamps",
   "-metadata", "album=Shore", "-metadata", "track=1", "-c:a", "libmp3lame", "-b:a", "96k", str(Path.cwd() / "Lamps" / "Shore" / "01 - Tidewater.mp3"))

PORT = port()
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks, missing = [], {}, []


def session(b, name, size):
    ctx = b.new_context(viewport=size, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", name)
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return ctx, page


def settle(page, ms=700):
    page.wait_for_timeout(ms)


def plays(page, sel, past):
    page.wait_for_function(f"document.querySelector('{sel}') && document.querySelector('{sel}').readyState >= 1", timeout=30000)
    page.evaluate(f"(() => {{ const v = document.querySelector('{sel}'); v.muted = true; return v.play(); }})()")
    page.wait_for_function(f"document.querySelector('{sel}').currentTime > {past}", timeout=30000)
    return True


def report_failure(page):
    """On a timeout: what the page showed, for the log."""
    try:
        page.screenshot(path=str(OUT / "failed.png"))
        print("FAILED at", page.url, "\n", page.inner_text("main")[:600], "\npage errors:", errors)
    except Exception:  # noqa: BLE001  (the report is best effort)
        pass


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None, args=["--autoplay-policy=no-user-gesture-required"])
    vctx, page = session(b, "viewer", {"width": 1366, "height": 900})
    try:
        page.on("response", lambda r: r.status >= 400 and missing.append(f"{r.status} {r.url.replace(BASE, '')}"))
        page.goto(BASE + "/#/jf")
        page.wait_for_selector(".jcard", timeout=30000)
        checks["the home shows each library's newest"] = all(page.locator(f".shelf-head h2:has-text('New in {n}')").count() for n in ("Films", "Shows", "Music", "Books"))
        checks["the nav has Library"] = page.locator("#side [data-g=library]").count() == 1
        settle(page)
        page.screenshot(path=str(OUT / "home-desk.png"))

        # a film: play, seek, and the place is saved
        page.goto(BASE + "/#/jf/movies")
        page.wait_for_selector(".jgrid .jcard", timeout=30000)
        # the Sort menu from the keyboard: each arrow changes the list, and the focus stays on the menu for the next one
        page.focus("select[aria-label='Sort']")
        page.keyboard.press("ArrowDown")
        page.wait_for_function("location.hash === '#/jf/movies/DateCreated/all' && document.activeElement?.value === location.hash && document.querySelector('.jgrid .jcard')", timeout=30000)
        page.keyboard.press("ArrowDown")
        page.wait_for_function("location.hash === '#/jf/movies/ProductionYear/all' && document.activeElement?.value === location.hash && document.querySelector('.jgrid .jcard')", timeout=30000)
        checks["Sort is a menu that works from the keyboard and keeps the focus"] = page.locator("main nav.chips:visible").count() == 0
        page.goto(BASE + "/#/jf/movies/Nonsense/nope")
        page.wait_for_selector(".jgrid .jcard", timeout=30000)
        checks["an order or filter there isn't falls back to A–Z, All"] = page.eval_on_selector_all("select[data-go]", "els => els.map(e => e.selectedOptions[0].textContent)") == ["A–Z", "All"]
        page.click(".jcard a.t:has-text('Night Train')")
        page.wait_for_selector(".hero .primary", timeout=30000)
        settle(page)
        checks["metadata from Jellyfin is shown as text, never run"] = page.evaluate("window.__pwned === undefined") and "<img src=x" in page.inner_text(".hero .meta")
        page.screenshot(path=str(OUT / "film-desk.png"))
        page.click(".hero .primary")
        page.wait_for_selector("#jfVideo", timeout=30000)
        plays(page, "#jfVideo", 0.3)
        page.evaluate("document.querySelector('#jfVideo').currentTime = 7")
        page.wait_for_function("document.querySelector('#jfVideo').currentTime > 7.3", timeout=30000)
        page.evaluate("document.querySelector('#jfVideo').pause()")
        page.wait_for_timeout(800)
        saved = [b for i, b in SAVES if i == FILM and b.get("PlaybackPositionTicks", 0) >= 7 * T]
        checks["the film plays, seeks, and its place is saved in Jellyfin"] = bool(saved)
        page.screenshot(path=str(OUT / "player-desk.png"))
        page.goto(BASE + "/#/jf")
        page.wait_for_selector(".shelf-head h2:has-text('Continue watching')", timeout=30000)
        checks["Continue watching shows it"] = page.locator(".jstrip.wide .jcard:has-text('Night Train') .meter").count() > 0

        # a show: its season, then an episode
        page.goto(BASE + f"/#/jf/item/{SERIES}")
        page.wait_for_selector(".jstrip .jcard a.t:has-text('Season 1')", timeout=30000)
        # the show's page has an episode list of its own (Next up, one row): the season's answer is held back a moment
        # here, so a check that counts before the season's page is painted fails every time, not now and then
        page.route("**/api/jf/**", lambda r: (time.sleep(0.5), r.continue_()))
        page.click(".jstrip .jcard a.t:has-text('Season 1')")
        page.wait_for_selector("h2:has-text('Episodes') + .eps li", timeout=30000)  # the season's list, not the show's Next up
        page.unroute("**/api/jf/**")
        checks["the show lists its season and episodes"] = page.locator(".eps li").count() == 2
        settle(page)
        page.screenshot(path=str(OUT / "season-desk.png"))
        page.click(".eps li:nth-child(2) a")
        page.wait_for_selector("#jfVideo", timeout=30000)
        checks["an episode plays"] = plays(page, "#jfVideo", 0.5) and page.locator(".eps li.now").count() == 1

        # an album in the dock, still playing on another page
        page.goto(BASE + f"/#/jf/item/{ALBUM}")
        page.wait_for_selector("[data-album-play]", timeout=30000)
        page.click("[data-album-play]")
        page.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 0.5", timeout=30000)
        page.wait_for_selector(".jtracks .row.now", timeout=10000)
        settle(page, 1200)
        page.screenshot(path=str(OUT / "album-desk.png"))
        t0 = page.evaluate("dock.audio.currentTime")
        page.goto(BASE + "/#/jf/books")
        page.wait_for_selector(".jgrid .jcard", timeout=30000)
        page.wait_for_function(f"dock.audio.currentTime > {t0 + 1}", timeout=30000)
        checks["the song keeps playing in the dock across pages"] = page.evaluate("!dock.audio.paused") and page.is_visible("#dock")
        page.click("#dockNext")
        page.wait_for_function("dock.at === 1 && !dock.audio.paused && dock.audio.currentTime > 0.3", timeout=30000)
        checks["the dock moves to the next song"] = page.inner_text("#dockTitle") == "Second Wind"

        # the book: open, read, turn a page
        page.goto(BASE + f"/#/jf/read/{BOOK}")
        page.wait_for_selector("#rPage iframe", timeout=30000)
        page.wait_for_function("document.querySelector('#rPage iframe').contentDocument?.body?.innerText.includes('lighthouse keeper')", timeout=30000)
        page.wait_for_function("document.querySelector('#rAt').textContent.endsWith('%')", timeout=30000)
        before = page.evaluate("JSON.parse(localStorage.getItem('parlor:book:" + BOOK + "'))")
        page.click("#rNext")
        page.wait_for_function("JSON.parse(localStorage.getItem('parlor:book:" + BOOK + "')) !== " + json.dumps(before), timeout=30000)
        checks["the book opens and turns a page"] = True
        settle(page)
        page.screenshot(path=str(OUT / "reader-desk.png"))
        page.click(".rbar a.ghost")

        # a song from the music folder plays in the dock
        page.goto(BASE + "/#/library")
        page.wait_for_selector(".tile a.name:has-text('Shore')", timeout=60000)
        page.click(".tile a.name:has-text('Shore')")
        page.wait_for_selector("[data-local-play]", timeout=30000)
        page.click(".hero [data-local-play]")
        page.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 0.5 && dock.audio.src.includes('/api/jf/music/')", timeout=30000)
        checks["a song in the music folder plays in the dock"] = page.inner_text("#dockTitle") == "Tidewater"

        # phone width
        pctx = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True, has_touch=True)
        phone = pctx.new_page()
        phone.on("pageerror", lambda e: errors.append(f"phone: {e}"))
        pctx.add_cookies(vctx.cookies())
        for name, hash_, sel in (("home-phone", "#/jf", ".jcard"), ("film-phone", f"#/jf/item/{FILM}", ".hero .primary"), ("album-phone", f"#/jf/item/{ALBUM}", "[data-album-play]")):
            phone.goto(BASE + "/" + hash_)
            phone.wait_for_selector(sel, timeout=30000)
            if name == "album-phone":
                phone.click("[data-album-play]")
                phone.wait_for_function("!dock.audio.paused", timeout=30000)
            settle(phone, 900)
            phone.screenshot(path=str(OUT / f"{name}.png"))
        checks["no sideways scroll on a phone"] = phone.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")

        # a member without Library
        cctx, closed = session(b, "closed", {"width": 1366, "height": 900})
        closed.goto(BASE + "/#/jf")
        closed.wait_for_selector("main .home, main .note", timeout=30000)
        status = closed.evaluate("fetch('/api/jf/views').then((r) => r.status)")
        checks["a member without Library is refused"] = status == 403 and closed.locator("#side [data-g=library]").count() == 0 and "isn't open to your account" in closed.inner_text("main")

        # an admin gives Parlor a Jellyfin of their own: its API key and who plays
        actx, boss = session(b, "uiadmin", {"width": 1366, "height": 900})
        boss.goto(BASE + "/#/settings")
        boss.wait_for_selector("#jfKey", timeout=30000)
        kept = boss.get_attribute("#jfKey", "type") == "password" and boss.input_value("#jfKey") == "" and "saved" in boss.get_attribute("#jfKey", "placeholder")
        boss.fill("#jfUser", "alice")
        boss.click("#signinForm button")
        boss.wait_for_function("fetch('/api/settings').then((r) => r.json()).then((s) => s.jellyfin_user === 'alice')", timeout=10000)
        same = core.setting("jellyfin_key")  # left empty: the saved key stays
        boss.fill("#jfKey", "k" * 20)
        boss.click("#signinForm button")
        boss.wait_for_function("document.querySelector('#jfKey').value === ''", timeout=10000)
        checks["an admin sets Jellyfin's API key and who plays, and the key is never shown back"] = kept and bool(same) and core.setting("jellyfin_key") == "k" * 20 and "kkkk" not in boss.content()

        # which Jellyfin user an account plays as: set, then cleared (setup links the first admin by name, wrong for another Jellyfin)
        link = lambda: core.one("SELECT jf_user FROM users WHERE id = ?", ids["viewer"])["jf_user"]  # noqa: E731
        field = f"tr[data-user='{ids['viewer']}'] [data-jf]"
        boss.fill(field, "alice")
        boss.press(field, "Enter")
        boss.wait_for_function("document.querySelector('#toast, .toast')?.textContent.includes('Saved')", timeout=10000)
        linked = link()
        boss.fill(field, "")
        boss.press(field, "Tab")
        for _ in range(50):
            if link() is None:
                break
            time.sleep(0.1)
        checks["an admin sets and clears the Jellyfin user an account plays as"] = linked == "alice" and link() is None
    except Exception:
        report_failure(page)
        raise
    b.close()
server.should_exit = True
JF.shutdown()
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print("failed requests:", missing or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 17 and all(checks.values()) and not errors and len(shots) == 9
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
