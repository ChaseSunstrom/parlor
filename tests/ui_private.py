"""Private items in a real browser: Parlor runs here with a real (tiny) downloaded video. Its owner turns private
items on (and sees the recovery code once), makes the video private, plays it (seeking included) and shares it with
a friend, who can play it; a third person and an admin can't. No page errors; screenshots at desktop and phone width:
   python tests/ui_private.py [OUT_DIR]   (needs Playwright, its Chromium and ffmpeg)"""
import json
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
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-private-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-private-pass-42!"  # pragma: allowlist secret (made up)
core.db()
ids = {name: auth.create_user(name, PASSWORD, name == "uiadmin") for name in ("uiadmin", "owner", "friend", "stranger")}
VID = "PrIvAtE1234"
rel = f"Channel/A private one [{VID}].mp4"
(core.VIDEOS / rel).parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x180:rate=24", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(core.VIDEOS / rel)], check=True)
core.db().execute("INSERT INTO videos(id, item, path, size, quality, user_id, added) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (VID, json.dumps({"id": VID, "title": "A private one", "channel": "Channel", "duration": 6}), rel,
                   (core.VIDEOS / rel).stat().st_size, "180", ids["owner"], time.time()))

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


def session(b, name, size):
    ctx = b.new_context(viewport=size, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", name)
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    return ctx, page


def turn_on(page, label, who):
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#privOn", timeout=30000)
    page.fill("#privPw", PASSWORD)
    page.click("#privOn button")
    page.wait_for_selector("#privatePanel code.secret", timeout=30000)
    if who == "owner":
        page.locator("#privatePanel").scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / f"recovery-{label}.png"))


def plays(page, item):
    page.goto(BASE + f"/#/private/{item}")
    page.wait_for_selector(".private-page video", timeout=30000)
    page.wait_for_function("document.querySelector('.private-page video').readyState >= 1", timeout=30000)
    page.evaluate("(() => { const v = document.querySelector('.private-page video'); v.muted = true; v.currentTime = 4; return v.play(); })()")
    page.wait_for_function("document.querySelector('.private-page video').currentTime > 4.1", timeout=30000)
    return True


with sync_playwright() as p:
    b = p.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    label, size = "desk", {"width": 1280, "height": 900}
    octx, owner = session(b, "owner", size)
    turn_on(owner, label, "owner")
    fctx, friend = session(b, "friend", size)
    turn_on(friend, label, "friend")
    owner.goto(BASE + "/#/yt/downloads")
    owner.wait_for_selector("[data-vprivate]", timeout=30000)
    owner.click("[data-vprivate]")
    owner.wait_for_selector("#privateShelf .vcard.private", timeout=30000)
    checks["the video moved to Private, encrypted"] = not (core.VIDEOS / rel).exists() and b"testsrc" not in b"".join(
        f.read_bytes()[:4096] for f in (core.VIDEOS / ".parlor-private").glob("[0-9a-f]*"))
    owner.screenshot(path=str(OUT / f"downloaded-{label}.png"))
    item = owner.get_attribute("#privateShelf .vcard.private a.t", "href").split("/")[-1]
    checks["its owner plays it, seeking included"] = plays(owner, item)
    owner.fill("#shareName", "friend")
    owner.click("#shareForm button")
    owner.wait_for_selector(".shares li:has-text('friend')", timeout=30000)
    owner.screenshot(path=str(OUT / f"private-{label}.png"))
    checks["a friend it's shared with plays it"] = plays(friend, item)
    for name in ("stranger", "uiadmin"):
        sctx, other = session(b, name, size)
        other.goto(BASE + f"/#/private/{item}")
        other.wait_for_selector(".note, .private-unlock", timeout=30000)
        status = other.evaluate(f"fetch('/api/private/{item}/file').then((r) => r.status)")
        checks[f"{name} can't open it"] = status in (404, 409) and "A private one" not in other.inner_text("main")
        sctx.close()
    pctx = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    phone = pctx.new_page()
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.context.add_cookies(octx.cookies())
    phone.goto(BASE + f"/#/private/{item}")
    phone.wait_for_selector(".private-page video", timeout=30000)
    phone.screenshot(path=str(OUT / "private-phone.png"))
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 5 and all(checks.values()) and not errors and len(shots) == 4
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
