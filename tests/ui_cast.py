"""A film on a TV set's own player, in a real browser, against the stand-in DLNA player and Jellyfin from
test_cast.py: an admin adds an LG in Settings → TVs and sets the address TVs reach Parlor at; a film's page offers
On TV with no Parlor screen paired; pressing it sends the film to the set and turns the phone into its remote (You →
TVs), which shows what is playing and pauses, seeks and stops it; the TV fetches the film with no sign-in; the link's
name is in no page. Screenshots at desktop and phone width:
   python tests/ui_cast.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import cast
import core
import jf
import test_cast as fakes
import tvhw
import uvicorn
import yt
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-cast-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-cast-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
for k, v in (("jellyfin_url", "http://jellyfin.test:8096"), ("jellyfin_key", fakes.KEY), ("jellyfin_user", "family")):
    core.set_setting(k, v)
jf.requests.request = fakes.Jellyfin()
player = fakes.Player()
yt.video_info = lambda vid, user_id=None, fresh=False: fakes.INFO  # a Watch video, with no YouTube behind it
cast.SSDP_PORT, cast.SEEK_WAIT = player.ssdp, 0
tvhw.ALSO, tvhw.OWN, tvhw.TIMEOUT = fakes.LOCAL, True, 3  # the stand-ins, and this Parlor, are on 127.0.0.1
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


def signed_in(b, width):
    ctx = b.new_context(viewport={"width": width, "height": 900}, color_scheme="dark", is_mobile=width < 500, has_touch=width < 500)
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


def until(what, seconds=10):
    end = time.time() + seconds
    while time.time() < end and not what():
        time.sleep(0.05)
    return bool(what())


def press(page, selector):
    with page.expect_response(lambda r: r.url.endswith("/playing") and r.request.method == "POST", timeout=15000):
        page.click(selector)


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    boss = signed_in(b, 1280)

    # Settings → TVs: the set, and the address TVs reach Parlor at
    boss.goto(BASE + "/#/settings")
    boss.wait_for_selector("#hwAdd", timeout=30000)
    boss.select_option("#hwKind", "webos")
    boss.fill("#hwName", "Den LG")
    boss.fill("#hwHost", "127.0.0.1")
    boss.click("#hwAdd .primary")
    boss.wait_for_selector("#hwPanel li:has-text('Den LG')", timeout=10000)
    boss.fill("#hwAddrText", "http://93.184.216.34:8800")
    boss.click("#hwAddr button")
    boss.wait_for_selector(".toast:has-text('home network')", timeout=5000)
    checks["an address off the home network is refused for TVs, in words"] = core.setting("tv_address") == ""
    boss.fill("#hwAddrText", f" {BASE}/ ")
    boss.click("#hwAddr button")
    checks["the address TVs reach Parlor at is saved, tidied"] = until(lambda: core.setting("tv_address") == BASE) and until(lambda: boss.input_value("#hwAddrText") == BASE)
    boss.wait_for_timeout(400)
    boss.screenshot(path=str(OUT / "settings-tv-address.png"))

    # a film's page: On TV with no Parlor screen paired
    phone = signed_in(b, 390)
    phone.goto(f"{BASE}/#/jf/item/{fakes.FILM}")
    phone.wait_for_selector("[data-cast-jf]:not([hidden])", timeout=30000)
    checks["a film offers On TV when a set can play it, with no Parlor screen paired"] = core.one("SELECT COUNT(*) AS n FROM tvs")["n"] == 0
    phone.screenshot(path=str(OUT / "film-on-tv-phone.png"))
    phone.click("[data-cast-jf]")
    phone.wait_for_selector("#hwNow:not([hidden])", timeout=20000)
    checks["pressing it sends the film to the set and plays it from where it was left"] = player.did()[:3] == ["SetAVTransportURI", "Play", "Seek"] and player.at == 600
    checks["and this phone becomes its remote, on You → TVs"] = phone.evaluate("location.hash") == "#/tvs"
    checks["the remote says what is playing and where it got to"] = until(
        lambda: (phone.inner_text("#hwTitle"), phone.inner_text("#hwAt"), phone.inner_text("#hwLen")) == ("Stalker <1979> & co", "10:00", "1:40:00"))
    phone.wait_for_timeout(400)
    phone.screenshot(path=str(OUT / "remote-playing-phone.png"))

    # the TV fetches the film: no sign-in, byte ranges
    with urllib.request.urlopen(urllib.request.Request(player.uri, headers={"Range": "bytes=0-99"})) as r:
        checks["the TV fetches the film by its link with no sign-in"] = player.uri.startswith(BASE + "/cast/") and r.status == 206 and r.read() == fakes.BODY[:100]

    press(phone, "#hwToggle")
    checks["Pause pauses it, and the button turns to Play"] = player.state == "PAUSED_PLAYBACK" and until(lambda: phone.get_attribute("#hwToggle", "aria-label") == "Play")
    press(phone, "#hwToggle")
    checks["Play carries on"] = player.state == "PLAYING" and until(lambda: phone.get_attribute("#hwToggle", "aria-label") == "Pause")
    press(phone, "[data-hwp='seek:30']")
    checks["+30 skips ahead"] = player.at == 630
    box = phone.locator("#hwLine").bounding_box()
    with phone.expect_response(lambda r: r.url.endswith("/playing") and r.request.method == "POST", timeout=15000):
        phone.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    checks["pressing along the line jumps there"] = 2800 <= player.at <= 3200 and until(lambda: phone.inner_text("#hwAt").startswith(("4", "5")))
    token = player.uri.split("/cast/")[1].split("/")[0]
    checks["the link's name is in no page"] = token not in phone.content() + boss.content()
    press(phone, "[data-hwp='stop']")
    checks["Stop stops it, the remote clears, and the link ends"] = player.state == "STOPPED" and until(lambda: phone.is_hidden("#hwNow")) and cast._links == {}
    checks["where it got to was saved to Jellyfin"] = bool(jf.requests.request.saves)

    # a Watch video goes the same way
    phone.goto(f"{BASE}/#/watch/{fakes.VID}")
    phone.wait_for_selector("[data-cast-yt]:not([hidden])", timeout=30000)
    phone.screenshot(path=str(OUT / "watch-on-tv-phone.png"))
    phone.click("[data-cast-yt]")
    phone.wait_for_selector("#hwNow:not([hidden])", timeout=20000)
    checks["a Watch video's On TV sends it to the set too, and the remote shows it"] = (
        player.uri.endswith("/video.mp4") and until(lambda: phone.inner_text("#hwTitle") == "A <talk> & more"))
    checks["and says it is the small picture, with the way to the full one"] = "360p" in " ".join(phone.locator(".toast").all_inner_texts())
    press(phone, "[data-hwp='stop']")
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
if not all(checks.values()):
    print("the player heard:", player.actions, "state", player.state, "at", player.at)
print("screenshots:", ", ".join(sorted(x.name for x in OUT.glob("*.png"))), "in", OUT)
ok = len(checks) == 16 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
