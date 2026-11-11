"""Parlor's desktop app for real: the packaged app (desktop/out/linux-unpacked, made by scripts/build-desktop.sh) runs
in Docker on a virtual screen, with a real mpv beside it (no picture or sound device there, so --vo=null --ao=null),
against Parlor served here and the Jellyfin stand-in. Driven through the app's own window (Chromium's debugging port):
it opens on the address screen, refuses an address that isn't Parlor, signs in (a POST with a body, through the app),
keeps the session where the page can't see it, passes Range on, and saves a film for offline. Play starts mpv on the
original file with --hwdec=auto and nothing secret on its command line; the place is saved every 10 s and when mpv
closes; Resume starts mpv from there. A retro game's card starts a real RetroArch (nestopia, drawing to the virtual
screen) on the game fetched by the app, with the person's save from Parlor, and the changed save goes back when it
closes. Started again without mpv or RetroArch, as a TV box, the app is still signed in and the film plays in the
page's own player, and the game's card opens the page's own emulator.
   cd app && ../.venv/bin/python ../tests/live_desktop.py [OUT_DIR]   (needs Docker, Playwright and ffmpeg; build first)"""
import os
import secrets
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
import games
import uvicorn
from jf_standin import DOWNLOADS, FILM, JF, SAVES, T, point
from nes_rom import nes_rom
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "desktop" / "out" / "linux-unpacked"
if not (APP / "parlor-desktop").exists():
    sys.exit("FAIL: no packaged app: run scripts/build-desktop.sh first")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="live-desktop-"))
OUT.mkdir(parents=True, exist_ok=True)
IMAGE, TAG = "parlor-desktop-build:1", f"parlor-desktop-{secrets.token_hex(3)}"
PROFILE = Path(tempfile.mkdtemp(prefix="parlor-desktop-profile-"))  # the app's own folder: kept between its two runs
NAME, PASSWORD = "deskuser", "Live-desktop-pass-42!"  # pragma: allowlist secret (made up)
checks = {}


def free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(what, fn, seconds=30, every=0.25):
    end = time.time() + seconds
    while True:
        got = fn()
        if got:
            return got
        if time.time() > end:
            raise SystemExit(f"FAIL: {what}")
        time.sleep(every)


def start(cdp, **more):
    """the packaged app in a container that shares this machine's network, on a virtual screen"""
    subprocess.run(["docker", "rm", "-f", TAG], capture_output=True)
    envs = {"HOME": "/tmp", "PARLOR_DATA": "/profile", "PARLOR_MPV_ARGS": "--vo=null --ao=null", "PARLOR_RETROARCH_CONFIG": "/profile/ra.cfg", **more}
    subprocess.run(["docker", "run", "-d", "--init", "--name", TAG, "--network", "host", "--shm-size", "1g", "-u", f"{os.getuid()}:{os.getgid()}",
                    *[x for k, v in envs.items() for x in ("-e", f"{k}={v}")], "-v", f"{APP}:/app:ro", "-v", f"{PROFILE}:/profile", IMAGE,
                    "xvfb-run", "-a", "/app/parlor-desktop", "--no-sandbox", "--disable-gpu", f"--remote-debugging-port={cdp}"], check=True, capture_output=True)


def inside(*cmd):
    return subprocess.run(["docker", "exec", TAG, *cmd], capture_output=True, text=True).stdout


def window(pw, cdp):
    def attach():
        try:
            b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp}")
        except Exception:  # noqa: BLE001  (not up yet)
            return None
        pages = [p for c in b.contexts for p in c.pages if p.url.startswith("parlor://app")]
        return pages[0] if pages else None
    try:
        return wait("the app's window didn't open", attach, 60, 1)
    except SystemExit:
        print(subprocess.run(["docker", "logs", "--tail", "30", TAG], capture_output=True, text=True))
        raise


def need(page, selector, **kw):
    """wait for it; when it never comes, say what the window showed"""
    try:
        page.wait_for_selector(selector, **kw)
    except Exception:
        print("  at", page.evaluate("location.href"), "showing:", page.evaluate("document.body.innerText.slice(0, 400)"), "| errors:", errors)
        raise


core.db()
auth.create_user(NAME, PASSWORD, True)
games.GAMES = env.TMP / "games"  # one game: a cartridge with battery memory that writes "PL" at the start of its save
(games.GAMES / "roms" / "nes").mkdir(parents=True)
(games.GAMES / "roms" / "nes" / "Pixel Green.nes").write_bytes(nes_rom())
(PROFILE / "ra.cfg").write_text('video_driver = "sdl2"\naudio_driver = "null"\n')  # no graphics card or sound in the container
SAVE = "/api/games/retro/save/nes/Pixel%20Green.nes/sram"
point(core)
PORT = free()
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
errors = []
saved = lambda: [b for i, b in SAVES if i == FILM]  # noqa: E731  (what Jellyfin was told about the film)
mpv = lambda page: page.evaluate("parlorDesktop.mpv()")  # noqa: E731

try:
    with sync_playwright() as pw:
        cdp = free()
        start(cdp)
        page = window(pw, cdp)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_selector("#serverForm", timeout=30000)
        checks["it opens on the address screen, as a page of its own"] = page.evaluate("location.origin") == "parlor://app"
        page.screenshot(path=str(OUT / "desktop-address.png"))
        page.fill("#serverUrl", f"http://127.0.0.1:{JF.server_address[1]}")
        page.click("#serverForm .primary")
        page.wait_for_function("document.querySelector('#serverErr')?.textContent.includes(\"isn't Parlor\")", timeout=15000)
        checks["an address that isn't Parlor is refused"] = True
        page.fill("#serverUrl", f"127.0.0.1:{PORT}/")
        page.click("#serverForm .primary")
        page.wait_for_selector("#gateForm", timeout=15000)
        page.fill("#gName", NAME)
        page.fill("#gPw", PASSWORD)
        page.click("#gateForm .primary")
        page.wait_for_selector("#gateForm", state="detached", timeout=30000)
        me = page.evaluate("fetch('/api/me').then((r) => r.json())")
        checks["sign-in goes through the app to the server"] = me.get("name") == NAME
        checks["the session is the app's: the page sees no cookie"] = page.evaluate("document.cookie") == "" and page.evaluate(
            "fetch('/_parlor/server').then((r) => r.status)") == 403
        part = page.evaluate(f"fetch('/api/jf/stream/Items/{FILM}/Download', {{ headers: {{ Range: 'bytes=10-19' }} }}).then(async (r) => [r.status, (await r.arrayBuffer()).byteLength])")
        checks["a Range request passes through"] = part == [206, 10]
        DOWNLOADS.clear()

        # saving for offline
        page.goto(f"parlor://app/#/jf/item/{FILM}")
        page.click("[data-jf-save]", timeout=15000)
        kept = "Object.values(JSON.parse(localStorage.getItem('parlor:offline') || '{}')).some((e) => e.jf === '%s' && e.done)" % FILM
        page.wait_for_function(kept, timeout=60000)
        checks["a film is saved for offline"] = True
        page.evaluate("localStorage.removeItem('parlor:offline')")  # so Play goes to the server's copy
        page.reload()
        DOWNLOADS.clear()
        SAVES.clear()

        # mpv, to the end of the film (12 s): the 10 s save, then watched when it closes
        page.wait_for_selector(f'a.primary[href="#/jf/play/{FILM}"]', timeout=15000)
        checks["mpv is found"] = mpv(page)["available"] is True
        page.click(f'a.primary[href="#/jf/play/{FILM}"]')
        page.wait_for_selector(".mpvnote", timeout=15000)
        page.screenshot(path=str(OUT / "desktop-mpv.png"))
        state = wait("mpv never started playing", lambda: (lambda s: s if s["playing"] == FILM and (s["position"] or 0) > 0.5 and s["hwdec"] is not None else None)(mpv(page)), 30)
        line = inside("sh", "-c", "tr '\\0' ' ' < /proc/$(pgrep -x mpv | head -1)/cmdline")
        print(f"  mpv runs as: {line.strip()}\n  mpv says: hwdec-current={state['hwdec']!r} (a container has no video hardware), time-pos={state['position']:.1f}")
        checks["mpv plays the original file, asked for with Range by mpv itself"] = any(i == FILM for i, _ in DOWNLOADS)
        checks["mpv has --hwdec=auto, and no address or cookie on its command line"] = "--hwdec=auto" in line and "http" not in line and "ookie" not in line
        checks["mpv reports its decoder (hwdec-current)"] = isinstance(state["hwdec"], str)
        tick = wait("no place was saved while mpv played", lambda: [b for b in saved() if b.get("PlaybackPositionTicks", 0) >= 8 * T], 20)
        checks["the place is saved every 10 s from mpv's time-pos"] = 8 * T <= tick[0]["PlaybackPositionTicks"] <= 12 * T
        wait("the film ending wasn't saved as watched", lambda: any(b.get("Played") for b in saved()), 20)
        checks["at the end mpv closes and the film is marked watched"] = wait("mpv didn't close", lambda: mpv(page)["playing"] is None, 10) and True
        page.wait_for_selector(".mpvnote", state="detached", timeout=10000)

        # closed part-way: the place is saved at once, and Resume starts mpv there
        SAVES.clear()
        page.evaluate(f"fetch('/api/jf/progress', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json', 'X-Requested-With': 'parlor' }}, body: JSON.stringify({{ id: '{FILM}', played: false }}) }})")
        page.goto(f"parlor://app/#/jf/play/{FILM}/start")
        page.wait_for_selector(".mpvnote", timeout=15000)
        wait("mpv never reached 3 s", lambda: (mpv(page)["position"] or 0) >= 3, 30)
        SAVES.clear()
        inside("pkill", "-x", "mpv")
        closed = wait("closing mpv saved nothing", lambda: [b for b in saved() if b.get("PlaybackPositionTicks")], 10)
        checks["closing mpv saves the place at once"] = 3 * T <= closed[0]["PlaybackPositionTicks"] <= 7 * T
        page.goto(f"parlor://app/#/jf/item/{FILM}")
        page.wait_for_selector(f'a.primary[href="#/jf/play/{FILM}"]:has-text("Resume")', timeout=15000)
        page.click(f'a.primary[href="#/jf/play/{FILM}"]')
        first = wait("mpv never resumed", lambda: mpv(page)["position"], 30)
        checks["Resume starts mpv from the saved place"] = first >= 2.5
        page.click(".mpvnote a")  # Play here instead: mpv closes, the page's player takes over
        need(page, "#jfVideo", timeout=15000)
        checks["Play here instead closes mpv"] = wait("mpv stayed open", lambda: mpv(page)["playing"] is None, 10) and not inside("pgrep", "-x", "mpv").strip()

        # a retro game in RetroArch: the save on Parlor is there when it starts, and the game's change goes back
        card = 'a.gcard.retro[data-file="Pixel Green.nes"]'
        put = page.evaluate("fetch('%s', { method: 'PUT', headers: { 'X-Requested-With': 'parlor' }, body: new Uint8Array(8192).fill(90) }).then((r) => r.status)" % SAVE)
        page.goto("parlor://app/#/games")
        page.click(card, timeout=15000)
        playing = wait("RetroArch never started", lambda: page.evaluate("parlorDesktop.retro()")["playing"], 30)
        line = wait("no RetroArch process", lambda: inside("sh", "-c", "tr '\\0' ' ' < /proc/$(pgrep -x retroarch | head -1)/cmdline").strip(), 15)
        print(f"  RetroArch runs as: {line}")
        checks["a game's card starts RetroArch with a core for its system"] = put == 200 and playing == {"system": "nes", "file": "Pixel Green.nes"} and "nestopia_libretro" in line
        checks["RetroArch gets the game as a file: no address or cookie on its command line"] = "Pixel Green.nes" in line and "http" not in line and "ookie" not in line
        time.sleep(5)  # the cartridge writes its two bytes in its first frames
        inside("pkill", "-TERM", "-x", "retroarch")
        wait("RetroArch didn't close", lambda: page.evaluate("parlorDesktop.retro()")["playing"] is None, 20)
        back = page.evaluate("fetch('%s').then(async (r) => Array.from(new Uint8Array(await r.arrayBuffer()).slice(0, 4)))" % SAVE)
        checks["the save came from Parlor and the game's change went back (PL over ZZZZ)"] = back == [0x50, 0x4C, 90, 90]
        checks["the game's file is gone from the computer afterwards"] = not inside("sh", "-c", "ls -d /tmp/parlor-game-* 2>/dev/null").strip()

        # again, as a TV box without mpv: still signed in, and the page's own player plays
        cdp = free()
        start(cdp, PARLOR_MPV="/nowhere/mpv", PARLOR_RETROARCH="/nowhere/retroarch", PARLOR_TV="1")
        page = window(pw, cdp)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.wait_for_function("document.querySelector('#account') || document.querySelector('#gateForm')", timeout=30000)
        again = page.evaluate("fetch('/api/me').then((r) => r.json())")
        checks["started again, it is still connected and signed in"] = again.get("name") == NAME and page.locator("#serverForm, #gateForm").count() == 0
        checks["asked for the TV layout, it opens in it"] = page.evaluate("TV_MODE") is True
        checks["without mpv it says so"] = mpv(page)["available"] is False
        page.goto(f"parlor://app/#/jf/play/{FILM}/start")
        need(page, "#jfVideo", timeout=15000)
        page.wait_for_function("document.querySelector('#jfVideo')?.currentTime > 1", timeout=30000)
        checks["without mpv the film plays in the app's own player"] = page.locator(".mpvnote").count() == 0
        page.screenshot(path=str(OUT / "desktop-own-player.png"))
        page.goto("parlor://app/#/games")
        page.click(card, timeout=15000, no_wait_after=True)
        page.wait_for_url("**/api/games/play/nes/**", timeout=30000, wait_until="commit")
        page.wait_for_selector("#retro", state="attached", timeout=15000)
        checks["without RetroArch the game opens in the window's own emulator page"] = page.evaluate("JSON.parse(document.getElementById('retro').textContent).core") == "nes"
finally:
    subprocess.run(["docker", "rm", "-f", TAG], capture_output=True)
    server.should_exit = True

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print(f"screenshots: desktop-address.png, desktop-mpv.png, desktop-own-player.png in {OUT}")
ok = len(checks) == 24 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
