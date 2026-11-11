"""Parlor on a TV, in a real browser at 1920×1080 driven by the keyboard alone (the remote's arrows, OK and Back):
/tv shows a pairing code; a phone signed in enters it and the TV signs itself in; every page of Library, Watch,
Music and Request is reachable with the arrows from the menu, focus always shows, nothing traps it, Back goes back.
With --remote, the phone's remote works the TV: arrows move the TV's focus (key to TV p95 measured, under 300 ms),
an album and a film are cast from the phone and play on the TV, play/pause, seek, volume and typing act on it, a
second remote sees the same state, and a TV that drops off says so on the remote and comes back by itself.
Media comes from a Jellyfin stand-in and the music folder; screenshots at 1920×1080 and phone width:
   python tests/ui_tv.py [--remote] [OUT_DIR]   (needs Playwright, its Chromium and ffmpeg)"""
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

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
TARGETS = ["#/jf", "#/jf/movies", "#/jf/shows", "#/jf/music", "#/jf/books", "#/yt/home", "#/yt/subs", "#/yt/rec",
           "#/music", "#/library", "#/playlists", "#/media", "#/lidarr", "#/gamereq", "#/requests"]  # every page of Library, Watch, Music and Request in the menu:
# a section's own entry first, since the menu lists a section's pages only once it is open


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
    """drag the remote's joystick to one side and let go: one arrow"""
    box = page.locator("#rStick").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    dx, dy = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[direction]
    page.mouse.move(cx, cy)
    page.mouse.down()
    for k in range(1, 5):
        page.mouse.move(cx + dx * box["width"] * 0.08 * k, cy + dy * box["width"] * 0.08 * k)
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
    phone = signed_in_phone(b)
    phone.goto(BASE + "/#/tvs")
    phone.wait_for_selector("#tvAdd", timeout=30000)
    phone.fill("#tvAddCode", code.lower())
    phone.fill("#tvAddName", "Living room")
    phone.click("#tvAdd .primary")
    screen.wait_for_selector("#tvCode", state="detached", timeout=30000)
    screen.wait_for_function("ME && ME.name === 'family' && tvMe && tvMe.name === 'Living room' && document.body.classList.contains('tv')", timeout=30000)
    checks["/tv shows a code; a phone enters it and the TV signs itself in"] = len(code) == 8

    # the arrows reach every page of Library, Watch, Music and Request from the menu; Enter opens it; focus shows
    reached = []
    for target in TARGETS:
        for _ in range(8):  # left, until the menu
            if (focused(screen) or ["", ""])[0] == "side":
                break
            press(screen, "ArrowLeft")
        press(screen, "ArrowUp", 30)
        for _ in range(45):
            if (focused(screen) or ["", ""])[1] == target:
                break
            press(screen, "ArrowDown")
        if (focused(screen) or ["", ""])[1] == target:
            press(screen, "Enter")
            screen.wait_for_timeout(400)
            if screen.evaluate("location.hash") == target:
                reached.append(target)
    print("reached with the arrows:", len(reached), "of", len(TARGETS), [t for t in TARGETS if t not in reached])
    checks["every page of Library, Watch, Music and Request is reachable with the arrows and OK"] = reached == TARGETS
    outline = screen.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth)")
    checks["focus always shows"] = outline >= 3

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
    screen.wait_for_timeout(1200)
    screen.screenshot(path=str(OUT / "tv-home.png"))

    if REMOTE:
        tid = phone.evaluate("fetch('/api/tv').then(r => r.json()).then(r => r.tvs[0].id)")
        phone.goto(BASE + f"/#/tvs/{tid}")
        phone.wait_for_function("document.querySelector('#rStatus')?.textContent.startsWith('On')", timeout=30000)
        other = signed_in_phone(b, 1280)
        other.goto(BASE + f"/#/tvs/{tid}")
        other.wait_for_function("document.querySelector('#rStatus')?.textContent.startsWith('On')", timeout=30000)
        # the joystick: each flick moves the TV's focus; key to TV timed over 20 flicks
        screen.evaluate("(() => { const heard = tvDo; tvDo = (m) => { window.tvLastDo = { ...m, heard: Date.now() }; return heard(m); }; })()")
        gaps, moved = [], 0
        for i in range(20):
            before = screen.evaluate("document.activeElement?.outerHTML.slice(0, 80)")
            sent = screen.evaluate("Date.now()")
            flick(phone, "down" if i % 2 == 0 else "up")
            screen.wait_for_function(f"window.tvLastDo && window.tvLastDo.sent >= {sent}", timeout=5000)
            gaps.append(screen.evaluate("tvLastDo.heard - tvLastDo.sent"))
            moved += screen.evaluate("document.activeElement?.outerHTML.slice(0, 80)") != before
        p95 = sorted(gaps)[int(len(gaps) * 0.95)]
        print(f"remote key to TV: p95 {p95} ms, worst {max(gaps)} ms; focus moved on {moved} of 20")
        checks["the remote's joystick moves the TV's focus, key to TV p95 under 300 ms"] = moved >= 18 and p95 < 300
        # held, it keeps going; pressed, it's OK
        heard = screen.evaluate("(() => { window.tvKeys = []; const was = tvDo; window.tvDo = (m) => { tvKeys.push(m.key || m.cmd); was(m); }; return true; })()")
        box = phone.locator("#rStick").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        phone.mouse.move(cx, cy)
        phone.mouse.down()
        for k in range(1, 6):
            phone.mouse.move(cx, cy + box["width"] * 0.08 * k)
        phone.wait_for_timeout(700)
        phone.locator("#rStick").screenshot(path=str(OUT / "remote-joystick.png"))  # held, mid-drag
        phone.wait_for_timeout(800)
        phone.mouse.up()
        phone.wait_for_timeout(300)
        held = screen.evaluate("tvKeys.filter((k) => k === 'down').length")
        phone.mouse.click(cx, cy)
        screen.wait_for_function("tvKeys.includes('enter')", timeout=5000)
        print(f"held down for 1.5 s: {held} steps")
        checks["holding the joystick keeps going, pressing it is OK"] = heard and held >= 4 and phone.evaluate("byId('rKnob').style.translate") in ("", "none")
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
        # play/pause, volume, then a film cast from its page, seeking on it
        phone.click("#rToggle")
        screen.wait_for_function("dock.audio.paused", timeout=5000)
        phone.wait_for_function("document.querySelector('#rToggle').getAttribute('aria-label') && !document.querySelector('#rToggle').innerHTML.includes('M7.5')", timeout=5000)
        phone.click("[data-r='volume:-0.1']")
        screen.wait_for_function("Math.abs(dock.audio.volume - 0.9) < 0.01", timeout=5000)
        checks["play/pause and volume from the remote"] = True
        phone.goto(BASE + f"/#/jf/item/{jfs.FILM}")
        phone.wait_for_selector("[data-cast-jf]", timeout=30000)
        phone.click("[data-cast-jf]")
        screen.wait_for_function("document.querySelector('#jfVideo') && !document.querySelector('#jfVideo').paused && document.querySelector('#jfVideo').currentTime > 3", timeout=30000)
        phone.goto(BASE + f"/#/tvs/{tid}")
        phone.wait_for_function("document.querySelector('#rTitle').textContent.includes('Night Train')", timeout=10000)
        phone.click("[data-r='seek:-10']")
        screen.wait_for_function("document.querySelector('#jfVideo').currentTime < 2.5", timeout=5000)
        screen.wait_for_timeout(500)
        screen.screenshot(path=str(OUT / "tv-film.png"))
        checks["a film cast from its page plays on the TV, and the remote seeks it"] = True
        # a TV that blocks playing without a press asks for OK: the arrows stay on it, Back closes it and stays put
        was = screen.evaluate("location.hash")
        screen.evaluate("tvPlay({ play: () => Promise.reject(new Error('blocked')) })")
        screen.wait_for_selector("#tvAsk")
        press(screen, "ArrowLeft")
        press(screen, "ArrowUp")
        stays = screen.evaluate("document.activeElement.id") == "tvAskGo"
        press(screen, "Escape")
        screen.wait_for_timeout(300)
        checks["“Press OK to play” keeps the arrows, Back closes it"] = stays and screen.evaluate("!document.querySelector('#tvAsk')") and screen.evaluate("location.hash") == was
        # typing: the remote's text goes into the TV's search
        phone.click("[data-r='key:back']")
        screen.wait_for_timeout(600)
        phone.fill("#rText", "Gale")
        phone.click("#rType .primary")
        screen.wait_for_function("location.hash.includes('search/') && decodeURIComponent(location.hash).includes('Gale')", timeout=10000)
        phone.wait_for_function("document.querySelector('#rText').value === ''", timeout=5000)  # sent: the box empties
        checks["typing on the remote searches on the TV"] = True
        # the TV drops off: the remote says so, and it comes back by itself
        screen.evaluate("tvStream.close()")  # the TV's connection drops
        phone.wait_for_function("document.querySelector('#rStatus').textContent.startsWith('Off')", timeout=(tv.PING + 10) * 1000)
        screen.evaluate("tvListen()")  # what EventSource does by itself after a drop
        phone.wait_for_function("document.querySelector('#rStatus').textContent.startsWith('On')", timeout=15000)
        checks["a TV that drops off shows as off on the remote, and comes back"] = True
        phone.wait_for_timeout(400)
        phone.screenshot(path=str(OUT / "remote-phone.png"))
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == (13 if REMOTE else 5) and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
