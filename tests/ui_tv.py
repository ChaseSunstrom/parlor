"""Parlor on a TV, in a real browser at 1920×1080 driven by the keyboard alone (the remote's arrows, OK and Back):
/tv shows a pairing code; a phone signed in enters it and the TV signs itself in; Home is a hero and rows of posters
where the focus is never lost and Back returns to the tile a title was opened from; every page of Library, Watch,
Music and Request is reachable with the arrows (a section from the menu, its pages from the row along its top), focus
always shows, nothing traps it, Back goes back; search has a keyboard on the screen; an idle TV goes dark.
With --remote, the phone's remote works the TV: swipes on its touchpad move the TV's focus (key to TV p95 measured,
under 300 ms), an album and a film are cast from the phone and play on the TV, play/pause, the line to drag, the
volume slider, mute, the film's lists, typing, quick search and the sleep timer act on it, a second remote sees the
same state, a bar on other pages says what plays, and a TV that drops off says so and comes back by itself.
Media comes from a Jellyfin stand-in and the music folder; screenshots at 1920×1080 and phone width:
   python tests/ui_tv.py [--remote] [OUT_DIR]   (needs Playwright, its Chromium and ffmpeg)"""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("JF_STANDIN_FILMS", "24")  # rows long enough to slide
import yeerr_test_env as env  # noqa: E402,F401  (a throwaway data folder, then the app)

import auth
import core
import jf_standin as jfs
import tv
import uvicorn
from playwright.sync_api import sync_playwright

REMOTE = "--remote" in sys.argv
args = [a for a in sys.argv[1:] if a != "--remote"]
OUT = Path(args[0] if args else tempfile.mkdtemp(prefix="ui-tv-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-tv-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
auth.create_user("family", PASSWORD, False)
auth.create_user("kid", PASSWORD, False)  # joins the household at the end (Who's watching)
auth.create_user("guest", PASSWORD, False)  # never in the household: adds itself to the TV by its Add a remote code
jfs.point(core)
folder = Path.cwd() / "The Gulls" / "Harbor"
folder.mkdir(parents=True, exist_ok=True)
for n, (title, hz) in enumerate([("Dawn", 330), ("Gale", 440)], 1):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=20", "-metadata", f"title={title}",
                    "-metadata", "artist=The Gulls", "-metadata", "album_artist=The Gulls", "-metadata", "album=Harbor", "-metadata", f"track={n}",
                    "-c:a", "libmp3lame", "-b:a", "96k", str(folder / f"{n:02d} - {title}.mp3")], check=True)
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
TARGETS = ["#/jf", "#/jf/movies", "#/jf/shows", "#/jf/music", "#/jf/books", "#/games", "#/yt/home", "#/yt/subs", "#/live",
           "#/music", "#/library", "#/request", "#/requests"]  # every page of Library, Watch, Music and Request
SECTION = lambda t: "#/jf" if t.startswith("#/jf") or t == "#/games" else "#/yt/home" if t in ("#/yt/subs", "#/live") else "#/music" if t == "#/library" else "#/request" if t == "#/requests" else t  # noqa: E731
# a TV's menu is a rail of sections; a section's pages are the row of links along the top of each of its pages


def signed_in_phone(b, width=390):
    ctx = b.new_context(viewport={"width": width, "height": 844}, color_scheme="dark", is_mobile=width < 500, has_touch=width < 500)
    p = ctx.new_page()
    p.on("pageerror", lambda e: errors.append(f"phone: {e}"))
    p.goto(BASE + "/")
    p.wait_for_selector("#gateForm")
    p.fill("#gName", "family")
    p.fill("#gPw", PASSWORD)
    p.click("#gateForm .primary")
    p.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return p


def press(page, key, times=1):
    for _ in range(times):
        page.keyboard.press(key)
        page.wait_for_timeout(40)


def flick(page, direction):
    """a short swipe on the remote's touchpad (36 px, from its middle): one arrow"""
    box = page.locator("#rPad").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
    page.mouse.move(cx, cy)
    page.mouse.down()
    for k in range(1, 4):
        page.mouse.move(cx + dx * 12 * k, cy + dy * 12 * k)
    page.mouse.up()


def focused(page):
    return page.evaluate("(() => { const a = document.activeElement; return a ? [a.closest('#side') ? 'side' : 'main', a.getAttribute('href') || a.textContent.trim().slice(0, 30)] : null; })()")


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None, args=["--autoplay-policy=no-user-gesture-required"])
    tvctx = b.new_context(viewport={"width": 1920, "height": 1080}, color_scheme="dark")
    screen = tvctx.new_page()
    screen.on("pageerror", lambda e: errors.append(f"tv: {e}"))

    # /tv: a pairing code, no password
    screen.goto(BASE + "/tv")
    screen.wait_for_selector("#tvCode", timeout=30000)
    code = screen.inner_text("#tvCode").replace("\n", "").replace(" ", "")
    screen.wait_for_timeout(500)
    screen.screenshot(path=str(OUT / "tv-pair.png"))
    square = screen.evaluate("!!document.querySelector('.tvqr svg')")  # the code as a square to scan
    phone = signed_in_phone(b)
    phone.goto(BASE + "/#/tvs")
    phone.wait_for_selector("#tvAdd", timeout=30000)
    phone.fill("#tvAddCode", code.lower())
    phone.fill("#tvAddName", "Living room")
    phone.click("#tvAdd .primary")
    screen.wait_for_selector("#tvCode", state="detached", timeout=30000)
    screen.wait_for_function("typeof tvMe !== 'undefined' && ME && ME.name === 'family' && tvMe && tvMe.name === 'Living room' && document.body.classList.contains('tv')", timeout=30000)
    checks["/tv shows a code; a phone enters it and the TV signs itself in"] = len(code) == 8

    # Home on a TV: a hero that follows the focused title over rows of posters; no top bar; the menu is a slim rail
    screen.wait_for_selector("#tvh .tvt:focus", timeout=30000)
    screen.wait_for_timeout(700)  # the hero waits a moment for the focus to settle
    home = screen.evaluate("""(() => { const t = document.activeElement, box = t.getBoundingClientRect();
      return { hero: document.querySelector('#tvhHero h1')?.textContent, tile: t.getAttribute('aria-label'), rows: document.querySelectorAll('.tvh-row').length,
        bar: getComputedStyle(document.querySelector('.bar')).display, rail: document.querySelector('#side').getBoundingClientRect().width, lifted: getComputedStyle(t).transform !== 'none',
        scroll: getComputedStyle(document.documentElement).overflowY === 'hidden', onScreen: box.left >= 0 && box.right <= innerWidth + 2, clock: document.querySelector('#tvhClock')?.textContent }; })()""")
    print("home:", home)
    screen.screenshot(path=str(OUT / "tv-home.png"))
    # the hero's tallest form (a two-line title, three facts, three lines of overview, Resume and its progress) ends above the
    # rows and starts on the screen: a long title once put the first row's name on the Resume button
    screen.evaluate("""tvhHero({ title: 'Continue watching', accent: 'var(--amber)' }, { title: 'Spider-Man: Far From Home, the Extended Fan Cut', kind: 'Film',
      meta: ['2019', '1:55:34 left', 'PG-13', 'Action'], over: 'Peter Parker and his friends go on a summer trip to Europe. ' .repeat(8), go: 'Resume', pct: 0.4, href: '#/' })""")
    screen.wait_for_timeout(500)
    fit = screen.evaluate("""(() => { const h = document.querySelector('#tvhHero').getBoundingClientRect(), r = document.querySelector('.tvh-rows').getBoundingClientRect(),
      go = document.querySelector('#tvhHero .tvh-go').getBoundingClientRect(), clock = document.querySelector('#tvhClock').getBoundingClientRect();
      return { top: Math.round(h.top), bottom: Math.round(h.bottom), go: Math.round(go.bottom), rows: Math.round(r.top), clock: Math.round(clock.bottom),
               lines: Math.round(document.querySelector('#tvhHero h1').clientHeight / parseFloat(getComputedStyle(document.querySelector('#tvhHero h1')).lineHeight)) }; })()""")
    print("tallest hero:", fit)
    screen.screenshot(path=str(OUT / "tv-home-long.png"))
    checks["On a TV's Home the hero's tallest form ends above the rows and starts on the screen"] = fit["top"] >= 36 * 1.5 and fit["go"] <= fit["bottom"] <= fit["rows"] - 8 and fit["lines"] == 2
    # a newer Parlor on a TV: nobody presses a bar with a remote, so with nothing playing it reloads by itself, back on Home
    barless = screen.evaluate("window.oldPage = 1; pwaUpdated(); !document.querySelector('#updateBar')")
    try:
        screen.wait_for_function("!window.oldPage && document.querySelector('#tvh .tvt:focus')", timeout=30000)
        reloaded = True
    except Exception:
        reloaded = False
    checks["A TV takes a newer Parlor by itself when nothing plays"] = barless and reloaded
    screen.wait_for_timeout(700)
    checks["Home on a TV is a hero from the focused title over rows of posters, with no top bar"] = bool(home["hero"]) and home["hero"] == home["tile"] and home["rows"] >= 3 \
        and home["bar"] == "none" and home["rail"] < 200 and home["lifted"] and home["scroll"] and bool(home["clock"])
    # mash the arrows: after every press the focus is on a tile in sight or on the menu, and the hero ends on the tile
    lost, walk = 0, ["ArrowRight"] * 7 + ["ArrowDown"] * 3 + ["ArrowRight"] * 30 + ["ArrowUp"] * 5 + ["ArrowLeft"] * 9 + ["ArrowDown"] * 9 + ["ArrowLeft"] * 3 + ["ArrowRight", "ArrowUp", "ArrowRight", "ArrowDown"] * 4
    in_sight = """(() => { const a = document.activeElement, b = a.getBoundingClientRect();
      return a.matches('#tvh .tvt, #side a') && b.width > 0 && b.left >= 0 && b.right <= innerWidth && b.top >= 0 && b.bottom <= innerHeight && scrollY === 0; })()"""
    for n, key in enumerate(walk):
        screen.keyboard.press(key)
        lost += not screen.evaluate("document.activeElement.matches('#tvh .tvt, #side a')")  # at once: the focus is on a tile or the menu
        if n % 6 == 5:  # and when the slide has come to rest, that tile is whole on the screen
            screen.wait_for_timeout(450)
            if not screen.evaluate(in_sight):  # a loaded machine can stall a 0.3 s slide: give it once more to come to rest
                screen.wait_for_timeout(900)
            if not screen.evaluate(in_sight):
                lost += 1
                print("  out of sight after", n + 1, "presses:", screen.evaluate("(() => { const a = document.activeElement, b = a.getBoundingClientRect(); return [a.tagName, a.className, a.getAttribute('aria-label'), Math.round(b.left), Math.round(b.top), Math.round(b.right), Math.round(b.bottom), innerWidth, innerHeight, scrollY]; })()"))
    for end, away in (("ArrowDown", "ArrowUp"), ("ArrowUp", "ArrowDown")):  # past the last row and the first, the focus stays on its tile
        press(screen, end, 10)
        press(screen, "ArrowRight", 2)
        at = screen.evaluate("document.activeElement.getAttribute('href') + document.activeElement.dataset.i")
        press(screen, end)
        lost += screen.evaluate("document.activeElement.getAttribute('href') + document.activeElement.dataset.i") != at or not at.endswith("2")
    screen.wait_for_timeout(1100)  # the slide and the hero settle
    lost += not screen.evaluate(in_sight) or not screen.evaluate("document.activeElement.matches('#tvh .tvt') && document.querySelector('#tvhHero h1').textContent === document.activeElement.getAttribute('aria-label')")
    screen.screenshot(path=str(OUT / "tv-home-walked.png"))
    print(f"focus lost or out of sight on {lost} of {len(walk) + len(walk) // 6 + 3} looks")
    checks["on Home the focus is never lost or out of sight, however the arrows are pressed"] = lost == 0
    # OK opens the title; Back returns to the very tile (from a row of films: a title that was just played moves within Continue watching)
    press(screen, "ArrowDown", 2)
    press(screen, "ArrowRight", 3)
    tile = screen.evaluate("[document.activeElement.getAttribute('href'), document.activeElement.closest('.tvh-row').dataset.r]")
    press(screen, "Enter")
    screen.wait_for_function("!document.querySelector('#tvh')", timeout=10000)
    screen.wait_for_timeout(600)
    press(screen, "Escape")
    screen.wait_for_selector("#tvh .tvt:focus", timeout=10000)
    checks["Back from a title returns to the tile it was opened from"] = screen.evaluate("[document.activeElement.getAttribute('href'), document.activeElement.closest('.tvh-row').dataset.r]") == tile
    # nobody presses anything: the household's pictures, one after another, with their name and a clock in a corner that
    # changes with each; with no pictures the screen goes dark with a clock; the first key only wakes it, where it was
    screen.evaluate("tvIdleAfter = 400; tvAwake()")
    screen.wait_for_selector("#tvIdle:not(.bare) .tvidle-art img.on", timeout=8000)
    screen.wait_for_timeout(2700)
    screen.screenshot(path=str(OUT / "tv-idle.png"))
    was = screen.evaluate("[tvSlide.now.back, document.querySelector('#tvIdle small').textContent, document.querySelector('#tvIdle .tvidle-card').style.transform]")
    screen.evaluate("clearTimeout(tvSlideTimer); tvSlide(1)")  # the next picture, without the 12 seconds
    screen.wait_for_function(f"tvSlide.now.back !== {was[0]!r} && document.querySelectorAll('#tvIdle .tvidle-art img').length <= 2", timeout=8000)
    slid = screen.evaluate("[document.querySelector('#tvIdle small').textContent, document.querySelector('#tvIdle .tvidle-card').style.transform]")
    press(screen, "ArrowRight")
    woke = screen.evaluate("!document.querySelector('#tvIdle') && [document.activeElement.getAttribute('href'), document.activeElement.closest('.tvh-row')?.dataset.r]")
    screen.evaluate("window.keepPics = tvSlidePics; tvSlidePics = () => []; tvAwake()")  # nothing to show
    screen.wait_for_selector("#tvIdle.bare", timeout=5000)
    screen.wait_for_timeout(1300)
    screen.screenshot(path=str(OUT / "tv-idle-bare.png"))
    bare = screen.evaluate("!document.querySelector('#tvIdle img') && /\\d/.test(document.querySelector('#tvIdle b').textContent) && !document.querySelector('#tvIdle small').textContent")
    press(screen, "ArrowRight")
    screen.evaluate("tvSlidePics = keepPics; tvIdleAfter = 5 * 60e3; tvAwake()")
    print("idle:", was[1], "→", slid[0], "| card", was[2], "→", slid[1], "| bare clock:", bare)
    checks["an idle TV shows the household's pictures with their name and a clock that move, a dark clock with none, and the first key only wakes it"] = \
        woke == tile and bool(was[1]) and slid[1] != was[2] and bare
    # search: the remote's green key opens a keyboard on the screen; letters with the arrows and OK find a film; Right reaches it
    screen.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ColorF1Green', bubbles: true }))")
    screen.wait_for_selector("#tvSearch [data-k]:focus", timeout=10000)
    for letter in "night":
        screen.evaluate(f"document.querySelector('#tvSearch [data-k=\"{letter}\"]').focus()")
        press(screen, "Enter")
    screen.wait_for_selector("#tvFound a[href]", timeout=10000)
    screen.wait_for_timeout(300)
    screen.screenshot(path=str(OUT / "tv-search.png"))
    press(screen, "ArrowUp", 8)  # the top row of the keyboard: straight across from what was found
    for _ in range(8):
        if screen.evaluate("!!document.activeElement.closest('#tvFound')"):
            break
        press(screen, "ArrowRight")
    checks["search on a TV: a keyboard on the screen finds a film, and the arrows reach it"] = screen.evaluate("document.querySelector('#tvq').textContent") == "night" \
        and "Night Train" in screen.inner_text("#tvFound") and screen.evaluate("!!document.activeElement.closest('#tvFound')")

    # the arrows reach every page of Library, Watch, Music and Request: its section from the rail, then the row of
    # the section's pages along the top; Enter opens it; focus shows
    reached = []
    for target in TARGETS:
        for _ in range(12):  # left, until the menu
            if (focused(screen) or ["", ""])[0] == "side":
                break
            press(screen, "ArrowLeft")
        press(screen, "ArrowUp", 10)
        for _ in range(10):
            if (focused(screen) or ["", ""])[1] == SECTION(target):
                break
            press(screen, "ArrowDown")
        if (focused(screen) or ["", ""])[1] != SECTION(target):
            continue
        press(screen, "Enter")
        screen.wait_for_timeout(400)
        if target != SECTION(target):  # into the page, up to its top row, along it
            press(screen, "ArrowRight")
            for _ in range(12):
                if screen.evaluate("!!document.activeElement.closest('.subnav')"):
                    break
                press(screen, "ArrowUp")
            press(screen, "ArrowLeft", 8)
            for _ in range(8):
                if (focused(screen) or ["", ""])[1] == target:
                    break
                press(screen, "ArrowRight")
            if (focused(screen) or ["", ""])[1] == target:
                press(screen, "Enter")
                screen.wait_for_timeout(400)
        if screen.evaluate("location.hash") == target:
            reached.append(target)
        if target == "#/jf/movies":
            screen.wait_for_timeout(600)
            screen.screenshot(path=str(OUT / "tv-films.png"))
    print("reached with the arrows:", len(reached), "of", len(TARGETS), [t for t in TARGETS if t not in reached])
    checks["every page of Library, Watch, Music and Request is reachable with the arrows and OK"] = reached == TARGETS
    press(screen, "ArrowRight")
    press(screen, "ArrowDown")
    outline = screen.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth)")
    checks["focus always shows"] = outline >= 3 and focused(screen)[0] == "main"

    # into a page's content and back out to the menu: nothing traps the focus; Back goes back
    screen.goto(BASE + "/tv#/library/The%20Gulls/Harbor")
    screen.wait_for_selector(".jtracks [data-local-play]", timeout=30000)
    press(screen, "ArrowRight", 3)
    press(screen, "ArrowDown", 4)
    inside = focused(screen)
    back_out = 0
    while (focused(screen) or ["", ""])[0] != "side" and back_out < 12:
        press(screen, "ArrowLeft")
        back_out += 1
    checks["from a page's content, Left always gets back to the menu"] = inside and inside[0] == "main" and (focused(screen) or [""])[0] == "side"
    screen.evaluate("location.hash = '#/jf'")
    screen.wait_for_timeout(500)
    press(screen, "Escape")
    screen.wait_for_timeout(500)
    checks["Back goes back"] = screen.evaluate("location.hash") == "#/library/The%20Gulls/Harbor"
    screen.evaluate("location.hash = '#/'")
    screen.wait_for_selector("#tvh .tvt:focus", timeout=10000)
    # a TV is left on: when a newer Parlor answers, it reloads itself (nothing is playing)
    screen.wait_for_function("tvVersion", timeout=10000)
    screen.evaluate("window.oldPage = true; tvVersion = '000000000000'; tvFresh()")
    screen.wait_for_function("!window.oldPage && typeof tvVersion !== 'undefined' && ME && tvMe && tvVersion && tvVersion !== '000000000000'", timeout=30000)  # typeof: mid-reload the scripts aren't in yet (tv.js, the last to define, is after core.js)
    checks["a TV left on reloads itself when Parlor was updated"] = True

    if REMOTE:
        tid = phone.evaluate("fetch('/api/tv').then(r => r.json()).then(r => r.tvs[0].id)")
        phone.goto(BASE + f"/#/tvs/{tid}")
        phone.wait_for_function("document.querySelector('#rStatus')?.textContent.startsWith('On')", timeout=30000)
        other = signed_in_phone(b, 1280)
        other.goto(BASE + f"/#/tvs/{tid}")
        other.wait_for_function("document.querySelector('#rStatus')?.textContent.startsWith('On')", timeout=30000)
        # the touchpad: each swipe moves the TV's focus; timed over 20 swipes from the server passing the press on to
        # the TV's answer after the frame that shows it (the number the remote shows)
        screen.evaluate("(() => { window.tvHeard = []; const heard = tvDo; tvDo = (m) => { tvHeard.push(m); return heard(m); }; })()")
        gaps, moved = [], 0
        for i in range(20):
            before = screen.evaluate("document.activeElement?.outerHTML.slice(0, 80)")
            flick(phone, "down" if i % 2 == 0 else "up")
            screen.wait_for_function(f"tvHeard.length > {i}", timeout=5000)
            phone.wait_for_function(f"remote.ack === {screen.evaluate('tvHeard.at(-1).n')}", timeout=5000)
            gaps.append(phone.evaluate("remote.lag"))
            moved += screen.evaluate("document.activeElement?.outerHTML.slice(0, 80)") != before
        p95 = sorted(gaps)[int(len(gaps) * 0.95)]
        print(f"remote key to the TV's frame and back: p95 {p95} ms, worst {max(gaps)} ms; focus moved on {moved} of 20")
        checks["a swipe on the remote's touchpad moves the TV's focus, key to the TV's frame p95 under 300 ms"] = moved >= 18 and p95 < 300
        # what the TV's focus is on shows on the phone
        phone.wait_for_function("byId('rTitle').textContent === (remote.tv.state.focus || {}).title", timeout=5000)
        print("the remote shows:", phone.inner_text("#rWhere"), "/", phone.inner_text("#rTitle"))
        checks["the remote shows what the TV's focus is on"] = phone.inner_text("#rTitle") == screen.evaluate("tvFocused().title") and "On its screen: Home" in phone.text_content("#rWhere")
        # one path: the TV's own remote and its media keys go through tvDo like a phone's presses
        own = screen.evaluate("tvHeard.length")
        press(screen, "ArrowDown")
        screen.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'MediaPlayPause', bubbles: true }))")  # a remote's play/pause key
        press(screen, "Escape")
        checks["the TV's own keys and a phone's presses take the same path"] = screen.evaluate(f"tvHeard.slice({own}).map((m) => m.key || m.cmd).join()") == "down,toggle,back"
        # the TV answers each press after the frame that shows it; the remote says how long the whole way took
        phone.wait_for_function("/answers in \\d+ ms/.test(document.querySelector('#rStatus').textContent)", timeout=5000)
        said = phone.evaluate("remote.tv.state")
        print(f"the remote says: {phone.inner_text('#rStatus')}; the TV took {said.get('took')} ms to act, {said.get('paint')} ms to its frame")
        checks["the remote shows how fast the TV answers"] = said["lag"] < 400 and said.get("paint") is not None
        # a long swipe is a step for every 44 px; resting at its end keeps going; a tap is OK; a tap on an edge is one step that way
        heard = screen.evaluate("(() => { window.tvKeys = []; const was = tvDo; window.tvDo = (m) => { tvKeys.push(m.key || m.cmd); was(m); }; return true; })()")
        box = phone.locator("#rPad").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        phone.mouse.move(cx, cy)
        phone.mouse.down()
        for k in range(1, 6):
            phone.mouse.move(cx, cy + box["width"] * 0.08 * k)
        phone.wait_for_timeout(700)
        phone.locator("#rPad").screenshot(path=str(OUT / "remote-pad.png"))  # held, mid-swipe
        phone.wait_for_timeout(800)
        phone.mouse.up()
        phone.wait_for_timeout(300)
        held = screen.evaluate("tvKeys.filter((k) => k === 'down').length")
        phone.mouse.click(cx, cy)
        screen.wait_for_function("tvKeys.includes('enter')", timeout=5000)
        phone.mouse.click(box["x"] + box["width"] * 0.93, cy)  # its right edge
        screen.wait_for_function("tvKeys.at(-1) === 'right'", timeout=5000)
        print(f"a swipe of {round(box['width'] * 0.4)} px, then held for 1.5 s: {held} steps")
        checks["a long swipe steps along, resting keeps going, a tap is OK and a tap on an edge is that arrow"] = heard and held >= 6 and not phone.evaluate("byId('rPad').classList.contains('held')")
        # an album from the phone: ⋯ → Play on TV; it plays in the TV's dock, and both remotes see it
        phone.goto(BASE + "/#/library/The%20Gulls/Harbor")
        phone.wait_for_selector(".hero [data-local-more]", timeout=30000)
        phone.click(".hero [data-local-more]")
        phone.click("#queueMenu [data-qm=tv]")
        screen.wait_for_function("!dock.audio.paused && dock.audio.currentTime > 0.3 && dock.queue[dock.at].title === 'Dawn'", timeout=20000)
        phone.goto(BASE + f"/#/tvs/{tid}")
        phone.wait_for_function("document.querySelector('#rTitle').textContent === 'Dawn'", timeout=10000)
        other.wait_for_function("document.querySelector('#rTitle').textContent === 'Dawn'", timeout=10000)
        checks["an album cast from the phone plays on the TV, and both remotes see it"] = True
        # on another page of Parlor, a bar says what the TV plays; its button pauses and plays it
        other.goto(BASE + "/#/")
        other.wait_for_function("byId('tvBar') && !byId('tvBar').hidden && byId('tvBar').textContent.includes('Dawn')", timeout=10000)
        other.click("#tvBar button")
        screen.wait_for_function("dock.audio.paused", timeout=5000)
        other.wait_for_function("byId('tvBar').textContent.includes('paused')", timeout=5000)
        other.screenshot(path=str(OUT / "remote-bar.png"))
        other.click("#tvBar button")
        screen.wait_for_function("!dock.audio.paused", timeout=5000)
        checks["a bar on Parlor's other pages says what the TV plays, and pauses it"] = other.evaluate("byId('tvBar').querySelector('a').hash") == f"#/tvs/{tid}"
        # play/pause, volume, then a film cast from its page, seeking on it
        phone.click("#rToggle")
        screen.wait_for_function("dock.audio.paused", timeout=5000)
        phone.wait_for_function("document.querySelector('#rToggle').getAttribute('aria-label') && !document.querySelector('#rToggle').innerHTML.includes('M7.5')", timeout=5000)
        phone.click("[data-r='volume:-0.1']")
        screen.wait_for_function("Math.abs(dock.audio.volume - 0.9) < 0.01", timeout=5000)
        checks["play/pause and volume from the remote"] = True
        phone.locator("#rVolume").fill("40")  # the slider
        screen.wait_for_function("Math.abs(dock.audio.volume - 0.4) < 0.01", timeout=5000)
        phone.click("#rMute")
        screen.wait_for_function("dock.audio.muted", timeout=5000)
        phone.wait_for_function("byId('rMute').getAttribute('aria-pressed') === 'true' && byId('rVol').textContent === 'Muted'", timeout=5000)
        phone.click("#rMute")
        screen.wait_for_function("!dock.audio.muted", timeout=5000)
        phone.locator("#rVolume").fill("100")
        screen.wait_for_function("dock.audio.volume === 1", timeout=5000)
        checks["the volume slider and mute"] = True
        phone.goto(BASE + f"/#/jf/item/{jfs.FILM}")
        phone.wait_for_selector("[data-cast-jf]", timeout=30000)
        phone.click("[data-cast-jf]")
        screen.wait_for_function("document.querySelector('#jfVideo') && !document.querySelector('#jfVideo').paused && document.querySelector('#jfVideo').currentTime > 3", timeout=30000)
        phone.goto(BASE + f"/#/tvs/{tid}")
        phone.wait_for_function("document.querySelector('#rTitle').textContent.includes('Night Train')", timeout=10000)
        phone.click("[data-r='seek:-10']")
        screen.wait_for_function("document.querySelector('#jfVideo').currentTime < 2.5", timeout=5000)
        checks["a film cast from its page plays on the TV, and the remote seeks it"] = True
        # the line under the title: it moves by itself while the film plays, and dragged it jumps there
        phone.wait_for_function("+byId('rSeek').max > 10 && !byId('rSeek').disabled", timeout=5000)
        phone.locator("#rSeek").fill("4")
        screen.wait_for_function("Math.abs(document.querySelector('#jfVideo').currentTime - 4) < 1.5", timeout=5000)
        phone.wait_for_function("+byId('rSeek').value >= 6 && byId('rAt').textContent === clock(+byId('rSeek').value)", timeout=8000)
        checks["the remote's line is dragged to jump, and moves by itself while the film plays"] = "/api/" in phone.evaluate("byId('rArt').getAttribute('src') || ''")
        # the film's own lists (sound, subtitles, quality) are in More on the remote
        lists = phone.evaluate("remote.tv.state.lists")
        print("the film's lists on the remote:", [(l["name"], len(l["all"])) for l in lists])
        phone.click("#rMore summary")
        n = next(i for i, l in enumerate(lists) if l["name"] != "Quality" and len(l["all"]) > 1)
        phone.select_option(f"#rLists select[data-opt='{n}']", lists[n]["all"][1][0])
        screen.wait_for_function(f"tvLists()[{n}].selectedIndex === 1", timeout=5000)
        phone.screenshot(path=str(OUT / "remote-more.png"), full_page=True)
        phone.click("#rMore summary")
        checks["a film's subtitles and sound are chosen from the remote"] = True
        phone.click("[data-r='seek:-10']")  # back to its start, for what follows
        screen.wait_for_function("document.querySelector('#jfVideo').currentTime < 2.5", timeout=5000)
        # the film fills the screen; OK pauses with a strip over it; Down opens its options, where OK steps a list on; Up returns
        press(screen, "Enter")
        screen.wait_for_function("document.querySelector('#jfVideo').paused && document.querySelector('.player.osd')", timeout=5000)
        screen.wait_for_timeout(400)
        screen.screenshot(path=str(OUT / "tv-film.png"))
        film = screen.evaluate("""(() => { const b = document.querySelector('#jfBox').getBoundingClientRect(), v = document.querySelector('#jfVideo');
          return { full: b.left === 0 && b.top === 0 && Math.abs(b.width - innerWidth) < 2 && Math.abs(b.height - innerHeight) < 2, bare: !v.controls,
            title: document.querySelector('.tvosd-top').textContent.trim(), at: document.querySelector('.tvosd-at').textContent, tools: getComputedStyle(document.querySelector('#view .ptools')).display }; })()""")
        press(screen, "ArrowDown")
        screen.wait_for_function("document.body.classList.contains('tvopts') && document.activeElement.closest('.ptools')", timeout=5000)
        screen.evaluate("document.querySelector('#jfSubs').focus()")
        subs = screen.evaluate("[document.querySelector('#jfSubs').selectedIndex, document.querySelector('#jfSubs').options.length]")
        press(screen, "Enter")
        stepped = screen.evaluate("document.querySelector('#jfSubs').selectedIndex") == (subs[0] + 1) % subs[1]
        screen.screenshot(path=str(OUT / "tv-film-options.png"))
        press(screen, "ArrowUp", 4)
        film["out"] = screen.evaluate("!document.body.classList.contains('tvopts') && document.activeElement === document.querySelector('#jfBox')")
        t0 = screen.evaluate("document.querySelector('#jfVideo').currentTime")
        press(screen, "ArrowRight")
        film["seek"] = screen.evaluate("document.querySelector('#jfVideo').currentTime") - t0
        print("film:", film, "subtitle list", subs, "stepped", stepped)
        checks["a film fills the TV: OK pauses under a strip, Down opens its options, OK steps a list, Up returns, the arrows seek"] = film["full"] and film["bare"] and "Night Train" in film["title"] \
            and film["tools"] == "none" and stepped and film["out"] and 9 < film["seek"] < 11
        press(screen, "Enter")  # playing again
        # a TV that blocks playing without a press asks for OK: the arrows stay on it, Back closes it and stays put
        was = screen.evaluate("location.hash")
        screen.evaluate("tvPlay({ paused: true, play: () => Promise.reject(new Error('blocked')) })")
        screen.wait_for_selector("#tvAsk")
        phone.wait_for_function("!byId('rWarn').hidden && byId('rWarn').textContent.includes('key is pressed on the TV')", timeout=5000)  # the remote says why nothing plays
        press(screen, "ArrowLeft")
        press(screen, "ArrowUp")
        stays = screen.evaluate("document.activeElement.id") == "tvAskGo"
        press(screen, "Escape")
        screen.wait_for_timeout(300)
        checks["“Press OK to play” keeps the arrows, Back closes it"] = stays and screen.evaluate("!document.querySelector('#tvAsk')") and screen.evaluate("location.hash") == was
        phone.wait_for_function("byId('rWarn').hidden", timeout=5000)
        # a page that was muted says so on the remote, and turning the sound up takes the mute off
        screen.evaluate("tvPlayer().muted = true; tvReport(true)")  # the film has ended by now: the music dock is what plays
        phone.wait_for_function("!byId('rWarn').hidden && byId('rWarn').textContent.includes('muted')", timeout=5000)
        phone.click("[data-r='volume:0.1']")
        screen.wait_for_function("!tvPlayer().muted", timeout=5000)
        phone.wait_for_function("byId('rWarn').hidden", timeout=5000)
        checks["a TV that holds the sound back, or is muted, says so on the remote"] = True
        # typing: the remote's text goes into the TV's search
        phone.click("[data-r='key:back']")
        screen.wait_for_timeout(600)
        phone.fill("#rText", "Gale")
        phone.click("#rType .primary")
        screen.wait_for_function("location.hash.includes('search/') && decodeURIComponent(location.hash).includes('Gale')", timeout=10000)
        phone.wait_for_function("document.querySelector('#rText').value === ''", timeout=5000)  # sent: the box empties
        checks["typing on the remote searches on the TV"] = True
        # the same box searches from the phone when the TV has no text box open: a title found plays on the TV. With the TV's
        # search open, what is typed shows there letter by letter.
        phone.click("[data-r='key:home']")
        phone.wait_for_function("remote.tv.state.page === '#/' && !remote.tv.state.input", timeout=10000)
        phone.fill("#rText", "Night")
        phone.wait_for_selector("#rFound [data-found]", timeout=10000)
        stayed = screen.evaluate("location.hash") in ("", "#/")
        phone.screenshot(path=str(OUT / "remote-search.png"))
        phone.click("#rFound [data-found^='jf:']")
        screen.wait_for_function("location.hash.startsWith('#/jf/play/')", timeout=10000)
        screen.evaluate("go('#/tvsearch')")
        phone.wait_for_function("remote.tv.state.input", timeout=10000)
        phone.locator("#rText").press_sequentially("Gal")
        screen.wait_for_function("document.querySelector('#tvq')?.textContent === 'Gal'", timeout=10000)
        checks["the remote's box finds a title and plays it on the TV, and types into the TV's own search"] = stayed and phone.evaluate("byId('rFound').hidden")
        phone.fill("#rText", "")
        # the TV drops off: the remote says so, and it comes back by itself
        # Parlor restarts behind a proxy: the TV's stream and its pairing check both get the proxy's 502, which ends an
        # EventSource for good. The TV has to open it again by itself once Parlor answers.
        gateway = lambda route: route.fulfill(status=502, body="Bad Gateway")  # noqa: E731
        for path in ("**/api/tv/events", "**/api/tv/me"):
            screen.route(path, gateway)
        screen.evaluate("tvListen()")  # the stream it had is cut, and the new one is refused
        phone.wait_for_function("document.querySelector('#rStatus').textContent.startsWith('Off')", timeout=(tv.PING + 10) * 1000)
        screen.wait_for_timeout(1500)  # its first look at the pairing gets the 502 too
        for path in ("**/api/tv/events", "**/api/tv/me"):
            screen.unroute(path, gateway)
        phone.wait_for_function("document.querySelector('#rStatus').textContent.startsWith('On')", timeout=20000)
        checks["a TV that drops off shows as off on the remote, and comes back"] = True
        phone.evaluate("scrollTo(0, 0)")
        phone.wait_for_timeout(400)
        phone.screenshot(path=str(OUT / "remote-phone.png"))
        # the sleep timer: set from More; when its time comes what plays pauses and the screen goes dark
        phone.click("#rMore summary")
        screen.evaluate("dock.audio.currentTime = 0; tvDo({ cmd: 'play' })")  # the song from before (20 s long), playing again
        screen.wait_for_function("!dock.audio.paused", timeout=5000)
        phone.select_option("#rSleep", "-1")  # when this ends: the moment follows the song
        screen.wait_for_function("tvSleepEnd && tvSleep > Date.now() + 10e3 && tvSleep < Date.now() + 21e3", timeout=5000)
        phone.wait_for_function("byId('rSleepAt').textContent.includes('when this ends') && byId('rSleep').value === '-1'", timeout=5000)
        phone.select_option("#rSleep", "15")
        screen.wait_for_function("!tvSleepEnd", timeout=5000)
        screen.wait_for_function("tvSleep > Date.now() + 14 * 60e3", timeout=5000)
        phone.wait_for_function("byId('rSleepAt').textContent.includes('goes dark at')", timeout=5000)
        screen.evaluate("tvSleep = Date.now() + 300")  # its time has come
        screen.wait_for_function("tvSleep === 0 && document.querySelector('#tvIdle') && dock.audio.paused", timeout=8000)
        phone.wait_for_function("!byId('rSleepAt').textContent.includes('goes dark at')", timeout=5000)
        checks["a sleep timer from the remote pauses the TV and darkens its screen"] = True
        # Who's watching: family (who paired this TV) and kid become the household; kid has a PIN. The TV's picker switches
        # to kid after the PIN on its pad (a wrong one first), and the remote's round picture switches it back.
        core.db().execute("UPDATE users SET household = 1 WHERE name IN ('family', 'kid')")
        core.db().execute("UPDATE users SET pin = ? WHERE name = 'kid'", (core.hash_password("4821"),))
        press(screen, "ArrowDown")  # wakes the dark screen
        screen.evaluate("go('#/who')")
        screen.wait_for_selector("#tvWho [data-who]", timeout=10000)
        screen.wait_for_timeout(300)
        screen.screenshot(path=str(OUT / "tv-who.png"))
        people = screen.evaluate("[...document.querySelectorAll('#tvWho [data-who]')].map((b) => b.querySelector('b').textContent)")
        current = screen.evaluate("document.activeElement.querySelector('b')?.textContent")
        press(screen, "ArrowRight")
        press(screen, "Enter")
        screen.wait_for_selector("#tvWhoPin:not([hidden])", timeout=5000)
        screen.keyboard.type("1111")
        press(screen, "ArrowDown", 2)
        press(screen, "ArrowRight")
        on_ok = screen.evaluate("document.activeElement.dataset.d")
        press(screen, "Enter")
        screen.wait_for_function("document.querySelector('#tvWhoSaid').textContent.includes('PIN')", timeout=5000)
        screen.screenshot(path=str(OUT / "tv-who-pin.png"))
        for d in "4821":  # with the arrows this time: each digit's key is pressed with OK
            screen.evaluate(f"document.querySelector('#tvWhoPin [data-d=\"{d}\"]').focus()")
            press(screen, "Enter")
        screen.evaluate("document.querySelector('#tvWhoPin [data-d=ok]').focus()")
        press(screen, "Enter")
        screen.wait_for_function("typeof ME !== 'undefined' && ME && ME.name === 'kid' && tvMe && document.querySelector('#tvh')", timeout=30000)
        screen.wait_for_selector("#side [data-g=who]", timeout=10000)
        print("who's watching:", people, "focus began on", current, "| OK key reached with the arrows:", on_ok, "| the TV is now", screen.evaluate("ME.name"), "admin:", screen.evaluate("ME.admin"))
        checks["a TV asks who is watching: a person with a PIN is picked after it, on a pad the arrows work"] = people == ["family", "kid"] and current == "family" and on_ok == "ok" \
            and screen.evaluate("location.hash") in ("", "#/")
        phone.wait_for_selector("#rWho:not([hidden])", timeout=10000)
        phone.click("#rWho")
        phone.wait_for_selector("#whoAsk [data-who]", timeout=5000)
        phone.screenshot(path=str(OUT / "remote-who.png"))
        phone.click("#whoAsk [data-who]:first-child")  # family, from their own phone: no PIN asked
        screen.wait_for_function("typeof ME !== 'undefined' && ME && ME.name === 'family' && tvMe", timeout=30000)
        checks["the remote's round picture says who the TV acts as, and switches it"] = True
        # a phone that scanned the pairing screen's square opens You → TVs with the code filled in
        other.goto(BASE + "/#/tvs/pair/abcd2345")
        other.wait_for_function("document.querySelector('#tvAddCode')?.value === 'ABCD2345'", timeout=10000)
        checks["the pairing screen shows a square to scan, and the phone it opens has the code filled in"] = square
        # Add a remote: a guest outside the household opens the TV's square on a phone that isn't signed in, signs in,
        # and lands on that TV's remote; the TV's page shows them at once with a fresh code, and Who's watching lists them
        screen.evaluate("go('#/tvremote')")
        screen.wait_for_selector("#tvLinkCode", timeout=10000)
        link = screen.evaluate("document.querySelector('#tvLinkCode').textContent")
        qr = screen.evaluate("!!document.querySelector('.tvlink-qr svg')")
        screen.screenshot(path=str(OUT / "tv-add-remote.png"))
        gctx = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True, has_touch=True)
        guest = gctx.new_page()
        guest.on("pageerror", lambda e: errors.append(f"guest: {e}"))
        asked = []  # the phone names the TV and its owner and asks before adding itself
        guest.on("dialog", lambda d: (asked.append(d.message), d.accept()))
        guest.goto(BASE + f"/#/tvs/link/{link}")
        guest.wait_for_selector("#gateForm")
        guest.fill("#gName", "guest")
        guest.fill("#gPw", PASSWORD)
        guest.click("#gateForm .primary")
        guest.wait_for_function(f"location.hash === '#/tvs/{tid}' && !!document.querySelector('#rPad')", timeout=30000)
        guest.screenshot(path=str(OUT / "guest-remote.png"))
        screen.wait_for_function(f"document.querySelector('#tvLinkCode') && document.querySelector('#tvLinkCode').textContent !== '{link}'"
                                 " && tvPeople.people.some((p) => p.name === 'guest')", timeout=15000)
        screen.evaluate("go('#/who')")
        screen.wait_for_selector("#tvWho [data-who]", timeout=10000)
        listed = screen.evaluate("[...document.querySelectorAll('#tvWho [data-who] b')].map((b) => b.textContent)")
        phone.goto(BASE + "/#/tvs")  # the owner sees who added themselves, with a way to take them off
        phone.wait_for_selector(".tvguests [data-who]", timeout=10000)
        owner_sees = phone.evaluate("document.querySelector('.tvguests').textContent.includes('guest')")
        phone.screenshot(path=str(OUT / "owner-guests.png"))
        print("add a remote:", len(link), "letters, square:", qr, "| who's watching:", listed, "| owner sees:", owner_sees)
        checks["a TV's Add a remote: a guest's phone scans its square, signs in and lands on its remote; Who's watching lists them"] = \
            len(link) == 8 and qr and "guest" in listed and bool(asked) and "family's TV" in asked[0] and owner_sees and screen.evaluate("!!document.querySelector('#tvWho .tvwho-add')")
        gctx.close()
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == (36 if REMOTE else 13) and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else f"FAIL: {[k for k, v in checks.items() if not v] or ''} {errors or ''} {len(checks)} checks")  # the last line says why: it is what a runner keeps
sys.exit(0 if ok else 1)
