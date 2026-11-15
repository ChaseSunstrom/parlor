"""TV hardware in a real browser, against the fakes of each protocol from test_tv_hardware.py (a Roku, an LG, a
Samsung, an adb and a cec-client stand-in): an admin adds one set of each type in Settings → TVs, pairs the LG and
the Samsung, tests the others and ticks who may use which; a member's phone remote then shows only the sets they
were given, and its Power, Input, Vol and HDMI buttons reach the right fake; the admin's remote reaches all five;
a TV's refusal is said on the remote; no key or token is ever in a page. Screenshots at desktop and phone width:
   python tests/ui_tv_hardware.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import test_tv_hardware as fakes
import tvhw
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-tv-hw-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-tv-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
auth.create_user("family", PASSWORD, False)
roku, lg, samsung = fakes.Roku(), fakes.Lg(), fakes.Samsung()
adb, adb_ran = fakes.tool("adb", "if sys.argv[1] == 'keygen':\n    open(sys.argv[2], 'w').write('PRIVATE-ADB-KEY')\n    sys.exit(0)\nprint('connected to x')")
cec, cec_ran = fakes.tool("cec-client", "print('power status: on')")
tvhw.ALSO, tvhw.TLS, tvhw.TIMEOUT, tvhw.ADB, tvhw.CEC = fakes.LOCAL, False, 3, adb, cec  # the fakes are on 127.0.0.1, in plain ws
tvhw.PORTS.update(roku=roku.port, webos=lg.port, tizen=samsung.port, adb=5599)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
server = uvicorn.Server(uvicorn.Config(env.yeerr.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.1)
BASE = f"http://127.0.0.1:{PORT}"
SHELL = next(iter(sorted(Path.home().glob(".cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))), None)
errors, checks, pages, answers = [], {}, [], []
SETS = [("roku", "Den Roku"), ("webos", "Den LG"), ("tizen", "Den Samsung"), ("adb", "Den Android"), ("cec", "Den CEC")]


def signed_in(b, name, width):
    ctx = b.new_context(viewport={"width": width, "height": 900}, color_scheme="dark", is_mobile=width < 500, has_touch=width < 500)
    p = ctx.new_page()
    p.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
    p.on("dialog", lambda d: d.accept())
    p.goto(BASE + "/")
    p.wait_for_selector("#gateForm")
    p.fill("#gName", name)
    p.fill("#gPw", PASSWORD)
    p.click("#gateForm .primary")
    p.wait_for_selector("#gateForm", state="detached", timeout=30000)
    pages.append(p)
    return p


def until(what, seconds=8):
    end = time.time() + seconds
    while time.time() < end and not what():
        time.sleep(0.05)
    return bool(what())


def hw(page, key, name=None):
    """press a button of the remote's hardware row, on the set called `name` when there's a choice, and wait for
    Parlor's answer: two presses at once reach a TV in either order"""
    if name:
        page.select_option("#rHwSet", label=name)
    with page.expect_response(lambda r: r.url.endswith("/press"), timeout=15000) as answer:
        page.click(f"#rHw [data-hw='{key}']")
    answers.append((name, key, answer.value.status))


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    boss = signed_in(b, "boss", 1280)

    # Settings → TVs: one set of each type, added through the form
    boss.goto(BASE + "/#/settings")
    boss.wait_for_selector("#hwAdd", timeout=30000)
    checks["the address and MAC boxes follow the type"] = True
    for kind, name in SETS:
        boss.select_option("#hwKind", kind)
        checks["the address and MAC boxes follow the type"] &= (boss.is_hidden("#hwHost") == (kind == "cec")) and (boss.is_visible("#hwMac") == (kind in ("webos", "tizen")))
        boss.fill("#hwName", name)
        if kind != "cec":
            boss.fill("#hwHost", "127.0.0.1")
        boss.click("#hwAdd .primary")
        boss.wait_for_selector(f"#hwPanel li:has-text('{name}')", timeout=10000)
    checks["an admin adds a set of each type in Settings → TVs"] = boss.locator("#hwPanel li").count() == 5

    # an address off the home network is refused, in words
    boss.select_option("#hwKind", "roku")
    boss.fill("#hwName", "Somewhere else")
    boss.fill("#hwHost", "169.254.169.254")
    boss.click("#hwAdd .primary")
    boss.wait_for_selector(".toast:has-text('home network')", timeout=5000)
    checks["an address off the home network is refused"] = boss.locator("#hwPanel li").count() == 5

    # Pair (LG, Samsung) and Test (the rest): each reaches its fake
    for kind, name in SETS:
        row = f"#hwPanel li:has-text('{name}')"
        want = "Pair" if kind in ("webos", "tizen") else "Test"
        assert boss.inner_text(f"{row} [data-hw-test]") == want, (name, boss.inner_text(f"{row} [data-hw-test]"))
        boss.click(f"{row} [data-hw-test]")
        boss.wait_for_selector(f"{row} [data-hw-test]:not([disabled]):has-text('Test')", timeout=15000)
    secrets = {r["kind"]: r["secret"] for r in core.rows("SELECT kind, secret FROM tv_sets")}
    checks["Pair gets the LG's key and the Samsung's token; Test reaches the Roku, adb and the CEC adapter"] = (
        secrets["webos"] == lg.KEY and secrets["tizen"] == samsung.TOKEN and lg.prompts == 1 and "/query/device-info" in roku.seen
        and ["connect", "127.0.0.1:5599"] in [r["argv"] for r in adb_ran()] and [r["stdin"] for r in cec_ran()] == ["pow 0\n"]
        and boss.locator("#hwPanel li small:has-text('not paired')").count() == 0)

    # who may use it: the member gets the Roku and the LG
    for name in ("Den Roku", "Den LG"):
        boss.check(f"#hwPanel li:has-text('{name}') .hwwho input")
    checks["an admin ticks who may use a set"] = until(lambda: core.one("SELECT COUNT(*) AS n FROM tv_access")["n"] == 2)
    boss.wait_for_timeout(600)
    boss.locator("#hwPanel").screenshot(path=str(OUT / "settings-tv-sets.png"))

    # a paired TV, for its remote (the pairing itself is ui_tv.py's)
    screen = b.new_context(viewport={"width": 1280, "height": 720}).new_page()
    screen.goto(BASE + "/tv")
    screen.wait_for_selector("#tvCode", timeout=30000)
    code = screen.inner_text("#tvCode").replace("\n", "").replace(" ", "")
    phone = signed_in(b, "family", 390)
    phone.goto(BASE + "/#/tvs")
    phone.wait_for_selector("#tvAdd", timeout=30000)
    # with no TV paired yet, the sets are already here, and the LG opens Parlor's TV screen by itself
    phone.wait_for_selector("#rHw [data-hw='parlor']", timeout=30000)
    tvhw.OWN = True  # this test's Parlor is on 127.0.0.1, an address a real TV couldn't open
    phone.click("#rHw [data-hw='parlor']")
    checks["with no TV paired, You → TVs works the set, and an LG opens Parlor's TV screen"] = until(lambda: lg.seen == [("ssap://system.launcher/open", {"target": BASE + "/tv"})])
    lg.seen.clear()
    phone.screenshot(path=str(OUT / "tvs-sets-phone.png"))
    phone.fill("#tvAddCode", code)
    phone.fill("#tvAddName", "Den")
    phone.click("#tvAdd .primary")
    assert until(lambda: core.one("SELECT id FROM tvs"), 30)
    tid = core.one("SELECT id FROM tvs")["id"]

    # the member's remote: only their sets, and the buttons reach them
    roku.seen.clear()
    phone.goto(f"{BASE}/#/tvs/{tid}")
    phone.wait_for_selector("#rHw [data-hw='power']", timeout=30000)
    checks["a member's remote has only the sets they were given"] = phone.locator("#rHwSet option").all_inner_texts() == ["Den LG", "Den Roku"]
    for key in ("power", "volup", "voldown", "mute", "hdmi2"):
        hw(phone, key, "Den Roku")
    checks["Power, Vol +, Vol −, Mute and HDMI 2 on the remote reach the Roku"] = until(
        lambda: roku.seen == ["/keypress/Power", "/keypress/VolumeUp", "/keypress/VolumeDown", "/keypress/VolumeMute", "/keypress/InputHDMI2"])
    checks["a button a set doesn't have isn't drawn"] = phone.locator("#rHw [data-hw='input']").count() == 0
    hw(phone, "volup", "Den LG")
    hw(phone, "hdmi2")
    checks["and the LG, with its stored key"] = until(lambda: [u for u, _ in lg.seen] == ["ssap://audio/volumeUp", "ssap://tv/switchInput"]) and lg.prompts == 1
    phone.reload()
    phone.wait_for_selector("#rHw [data-hw='power']", timeout=30000)
    checks["the remote remembers which set goes with this TV"] = phone.eval_on_selector("#rHwSet", "s => s.selectedOptions[0].textContent") == "Den LG"
    phone.wait_for_timeout(500)
    phone.screenshot(path=str(OUT / "remote-hardware-phone.png"))
    roku.status = 403  # "Control by mobile apps" switched off on the Roku
    hw(phone, "power", "Den Roku")
    phone.wait_for_selector(".toast:has-text('Control by mobile apps')", timeout=5000)
    checks["a TV's refusal is said on the remote"] = True
    roku.status = 200

    # the admin's remote reaches every set
    boss.goto(f"{BASE}/#/tvs/{tid}/all")
    boss.wait_for_selector("#rHw [data-hw='power']", timeout=30000)
    hw(boss, "input", "Den Samsung")
    hw(boss, "power", "Den Android")
    hw(boss, "volup", "Den CEC")
    checks["the admin's remote reaches the Samsung, the Android TV and the CEC adapter"] = until(
        lambda: [m["params"]["DataOfCmd"] for m in samsung.seen] == ["KEY_SOURCE"] and samsung.tokens[-1] == samsung.TOKEN
        and ["-s", "127.0.0.1:5599", "shell", "input", "keyevent", "26"] in [r["argv"] for r in adb_ran()]
        and [r["stdin"] for r in cec_ran()][-1] == "volup\n") and boss.locator("#rHwSet option").count() == 5

    # no key or token in any page, and Remove takes a set away
    boss.goto(BASE + "/#/settings")
    boss.wait_for_selector("#hwPanel li", timeout=30000)
    html = boss.content() + phone.content()
    checks["no key or token is in a page"] = not any(x in html for x in (lg.KEY, samsung.TOKEN, "PRIVATE-ADB-KEY"))
    boss.click("#hwPanel li:has-text('Den CEC') [data-hw-drop]")
    boss.wait_for_selector("#hwPanel li:has-text('Den CEC')", state="detached", timeout=5000)
    checks["Remove takes a set away"] = core.one("SELECT COUNT(*) AS n FROM tv_sets")["n"] == 4
    b.close()
server.should_exit = True
checks["every press was answered, and only the one the Roku refused wasn't a yes"] = [a for a in answers if a[2] != 200] == [("Den Roku", "power", 502)]
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
if not all(checks.values()):
    print("presses:", answers, "\nthe LG heard:", lg.seen, "prompts", lg.prompts, "\nthe Roku heard:", roku.seen)
print("screenshots:", ", ".join(sorted(x.name for x in OUT.glob("*.png"))), "in", OUT)
ok = len(checks) == 16 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
