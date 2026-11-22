"""The small player, in a real browser: a film left while it plays keeps playing in the bottom-right corner as Parlor is
used (the same element, still playing), its buttons pause and play it, put it back full size on its own page, and close
it (the film stops); the player's keys are inert while it is small; full screen still works; a TV makes it small only
from its Small button; a Watch video goes small the same way. Media from the Jellyfin stand-in; screenshots:
   python tests/ui_mini.py [OUT_DIR]   (needs Playwright, its Chromium and ffmpeg)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import jf_standin as jfs
import test_cast as fakes
import uvicorn
import yt
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-mini-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-mini-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
jfs.point(core)
yt.video_info = lambda vid, user_id=None, fresh=False: fakes.INFO  # a Watch video, with no YouTube behind it
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
FILM = f"#/jf/play/{jfs.FILM}"
PLAYING = "(() => { const v = document.querySelector('#jfVideo'); return v && v.currentTime > 0.3 && !v.paused; })()"
errors, checks = [], {}


def signed_in(b, width, height):
    ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme="dark", is_mobile=width < 500, has_touch=width < 500)
    p = ctx.new_page()
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.on("dialog", lambda d: d.accept())
    p.goto(BASE + "/")
    p.wait_for_selector("#gateForm")
    p.fill("#gName", "boss")
    p.fill("#gPw", PASSWORD)
    p.click("#gateForm .primary")
    p.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return p


def play(p):
    p.evaluate(f"go('#/jf')")
    p.wait_for_selector("#view .jcard, #view .jart", timeout=20000)
    p.evaluate(f"go('{FILM}')")
    p.wait_for_function(PLAYING, timeout=30000)
    p.evaluate("window.film = document.querySelector('#jfVideo'); film.playbackRate = 0.5")  # 12 s at half speed: time to look


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None, args=["--autoplay-policy=no-user-gesture-required"])
        p = signed_in(b, 1280, 800)
        play(p)
        p.evaluate("go('#/jf')")
        p.wait_for_selector("#mini video", timeout=10000)
        p.wait_for_selector("#view .jcard, #view .jart", timeout=20000)
        small = p.evaluate("""(() => { const m = document.querySelector('#mini .player').getBoundingClientRect();
          return { same: document.querySelector('#mini video') === film, playing: !film.paused, alive: jfp !== null, page: !document.querySelector('#view #jfVideo'),
                   corner: m.right > innerWidth - 40 && m.bottom > innerHeight - 40 && m.width < innerWidth / 2 }; })()""")
        checks["a film left while it plays keeps playing small in the bottom-right corner, the same player, as the next page shows"] = all(small.values()) or small
        p.screenshot(path=str(OUT / "mini-desk.png"))
        p.keyboard.press("k")  # the player's play/pause key, on another page
        p.keyboard.press("i")
        checks["the player's keys do nothing while it is small"] = p.evaluate("!film.paused")
        p.click("#mini [data-mini='play']")
        paused = p.evaluate("film.paused")
        p.wait_for_function("document.querySelector('#mini').classList.contains('paused')", timeout=5000)  # the pause event comes a moment later
        p.click("#mini [data-mini='play']")
        p.wait_for_function("!film.paused", timeout=5000)
        checks["its play/pause button pauses it and plays it again"] = paused
        at = p.evaluate("film.currentTime")
        p.click("#mini [data-mini='full']")
        p.wait_for_function(f"location.hash === '{FILM}' && !document.querySelector('#mini')", timeout=10000)
        back = p.evaluate(f"document.querySelector('#view #jfVideo') === film && !film.paused && film.currentTime >= {at} && jfp !== null")
        checks["back to full size puts the same film back on its page, still playing from where it was"] = back
        p.click("#jfFull")
        p.wait_for_function("document.fullscreenElement?.id === 'jfBox'", timeout=5000)
        p.mouse.move(600, 300)
        p.click("#jfBox .fsexit")  # the page's buttons stay behind: the way out is on the picture
        p.wait_for_function("!document.fullscreenElement", timeout=5000)
        checks["full screen goes in, and out from a button on the picture"] = True
        p.go_back()  # the browser's Back: small again
        p.wait_for_selector("#mini video", timeout=10000)
        p.click("#mini [data-mini='close']")
        p.wait_for_function("!document.querySelector('#mini')", timeout=5000)
        checks["close ends it: the film stops"] = p.evaluate("jfp === null && film.paused && !film.getAttribute('src')")
        # a TV: Back leaves a film; only its Small button makes it small
        p.evaluate("document.body.classList.add('tv')")
        play(p)
        p.evaluate("go('#/jf')")
        p.wait_for_selector("#view .jcard, #view .jart", timeout=20000)
        left = p.evaluate("!document.querySelector('#mini') && jfp === null")
        play(p)
        p.evaluate("document.querySelector('[data-mini-ask]').click()")
        p.wait_for_selector("#mini video", timeout=10000)
        checks["on a TV leaving a film ends it, and its Small button makes it small instead"] = left and p.evaluate("location.hash === '#/jf' && !film.paused") \
            and p.evaluate("tvPlayer() === film && tvBusy() && (film.closest('#mini') || document).querySelector('.vinfo h1, .jplay h1')?.textContent === 'Night Train'")  # the phone remote and the reload guard see it
        p.click("#mini [data-mini='close']")
        p.evaluate("document.body.classList.remove('tv')")
        # a Watch video (no stream behind it here: its player is made to say it plays)
        p.evaluate(f"go('#/watch/{fakes.VID}')")
        p.wait_for_selector("#view #video", timeout=20000)
        p.evaluate("window.wv = document.querySelector('#video'); Object.defineProperty(wv, 'paused', { get: () => false })")
        p.evaluate("go('#/jf')")
        p.wait_for_selector("#mini #video", timeout=10000)
        kept = p.evaluate("document.querySelector('#mini video') === wv && player !== null")
        p.click("#mini [data-mini='close']")
        checks["a Watch video goes small the same way, and close ends it"] = kept and p.evaluate("player === null && !document.querySelector('#mini')")
        # a phone: shown above its tab bar
        ph = signed_in(b, 390, 844)
        play(ph)
        ph.evaluate("go('#/jf')")
        ph.wait_for_selector("#mini video", timeout=10000)
        ph.wait_for_timeout(400)
        ph.screenshot(path=str(OUT / "mini-phone.png"))
        clear = ph.evaluate("""(() => { const m = document.querySelector('#mini .player').getBoundingClientRect(), bar = document.querySelector('header .nav, .nav');
          const top = bar && getComputedStyle(bar).display !== 'none' ? bar.getBoundingClientRect().top : innerHeight; return m.bottom <= top && m.right <= innerWidth && m.left >= 0; })()""")
        checks["on a phone it sits above the tab bar, inside the screen"] = clear
        b.close()
finally:
    server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v is True else '✗'} {k}" + ("" if v in (True, False) else f"  {v}"))
print("page errors:", errors or "none")
print("screenshots:", ", ".join(sorted(x.name for x in OUT.glob("*.png"))), "in", OUT)
ok = len(checks) == 9 and all(v is True for v in checks.values()) and not errors
print("RESULT:", "ok" if ok else f"FAIL: {[k for k, v in checks.items() if v is not True]} {errors or ''} {len(checks)} checks")
sys.exit(0 if ok else 1)
