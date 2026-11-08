"""Live streams in a browser, against stand-ins for YouTube's and Twitch's servers (made-up addresses; segments made
here with ffmpeg): a YouTube live plays in Parlor's player with the LIVE badge, going back turns it into Go live,
and Go live returns; Watch → Live lists who's live; a Twitch channel plays with its chat beside it; when Twitch runs
an ad, the ad's segments are never fetched and the hold card shows; past broadcasts and clips play; on a phone the
pages fit.

Run from app/: ../.venv/bin/python ../tests/ui_live.py   (prints RESULT: ok)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import live
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-live-"))
OUT.mkdir(parents=True, exist_ok=True)
from live_standin import ADS, FETCHED  # noqa: E402  (starts the stand-ins)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started or live.TW_CHAT.startswith("wss://"):
    time.sleep(0.05)
CHROME = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome"))), None)
checks, errors = {}, []


def device(b, width=1280, height=860):
    ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme="dark", is_mobile=width < 500, has_touch=width < 500)
    p = ctx.new_page()
    p.on("pageerror", lambda e: errors.append(str(e)[:200]))
    p.goto(BASE + "/")
    p.wait_for_selector("#gateForm")
    p.fill("#gName", "admin")
    p.fill("#gPw", "correct horse")
    p.click("#gateForm .primary")
    p.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return p


playing = "(() => { const v = document.querySelector('#video'); return v && v.currentTime > 2 && !v.paused && v.readyState >= 3; })()"
try:
    env.signed_in("admin")
    import core

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=str(CHROME) if CHROME else None, args=["--autoplay-policy=no-user-gesture-required"])
        p = device(b)
        # a YouTube live: Parlor's player, LIVE, back an hour, Go live
        p.goto(BASE + "/#/watch/abcdefghijk")
        p.wait_for_function(playing, timeout=30000)
        checks["a YouTube live plays in Parlor's player with LIVE"] = p.inner_text(".livebadge") == "LIVE"
        p.evaluate("const v = document.querySelector('#video'); v.currentTime = Math.max(0, v.currentTime - 40)")
        p.wait_for_function("document.querySelector('.livebadge').classList.contains('behind')", timeout=10000)
        behind = p.inner_text(".livebadge")
        p.click(".livebadge")
        p.wait_for_function("!document.querySelector('.livebadge').classList.contains('behind')", timeout=10000)
        checks["going back shows how far, and Go live returns"] = "Go live" in behind
        p.screenshot(path=str(OUT / "youtube-live.png"))
        # Watch → Live: follow a Twitch channel found by search; who's live
        p.goto(BASE + "/#/live")
        p.wait_for_selector("#twName", timeout=20000)
        p.fill("#twName", "speed")
        p.wait_for_selector("[data-follow=speedy]", timeout=10000)
        p.click("[data-follow=speedy]")
        p.wait_for_selector("[data-unfollow=speedy]", timeout=10000)
        checks["Twitch: found by search, followed, and shown live"] = p.locator(".grid .tcard", has_text="Speedrun practice").count() == 1
        p.screenshot(path=str(OUT / "live.png"), full_page=True)
        # the channel: live with chat beside it
        p.click(".grid .tcard")
        p.wait_for_function(playing, timeout=30000)
        p.wait_for_function("document.querySelectorAll('#chat li').length >= 3", timeout=15000)
        checks["a Twitch channel plays, chat beside it"] = "nice run" in p.inner_text("#chat")
        # an ad starts: its segments are never fetched, the hold card shows; it ends: the card goes
        FETCHED.clear()
        ADS["on"] = True
        p.wait_for_function("!document.querySelector('.livehold').hidden", timeout=20000)
        p.wait_for_timeout(4000)
        ad_fetched = [x for x in FETCHED if "/ad" in x]
        p.screenshot(path=str(OUT / "twitch-ad-hold.png"))
        ADS["on"] = False
        p.wait_for_function("document.querySelector('.livehold').hidden", timeout=20000)
        checks["during an ad: never fetched, the hold card shows, then it's back"] = not ad_fetched
        p.screenshot(path=str(OUT / "twitch.png"))
        # a past broadcast and a clip
        p.click("text=Last night's runs")
        p.wait_for_function(playing, timeout=30000)
        checks["a past broadcast plays, without the LIVE badge"] = p.locator(".livebadge").count() == 0
        p.goto(BASE + "/#/live/tw/speedy/clip/BigMoment-abc")
        p.wait_for_function("(() => { const v = document.querySelector('#video'); return v && v.currentTime > 0.5; })()", timeout=20000)
        checks["a clip plays"] = True
        # an offline channel; a phone
        p.goto(BASE + "/#/live/tw/napper")
        p.wait_for_selector(".toff", timeout=10000)
        checks["an offline channel says so and lists its broadcasts"] = "offline" in p.inner_text(".toff") and p.locator(".tcard").count() == 2
        phone = device(b, 390, 844)
        for path in ("/#/live", "/#/live/tw/speedy"):
            phone.goto(BASE + path)
            phone.wait_for_timeout(2500)
            if phone.evaluate("document.documentElement.scrollWidth > innerWidth + 1"):
                errors.append(f"sideways scroll on a phone at {path}")
        phone.screenshot(path=str(OUT / "twitch-phone.png"), full_page=True)
        checks["on a phone the pages fit"] = not any("sideways" in e for e in errors)
        b.close()
except Exception as exc:  # report what was reached
    import traceback

    errors.append(f"{type(exc).__name__}: {str(exc)[:200]} at line {traceback.extract_tb(exc.__traceback__)[-1].lineno}")
finally:
    server.should_exit = True

for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print("screenshots:", OUT)
ok = len(checks) == 9 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
