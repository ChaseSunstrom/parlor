"""API keys in a real browser: Settings → API keys makes a key (shown once), a program uses it with no browser
(search across the sources, get something, follow it), the key's last use shows, Delete stops it, and the API docs
open for a signed-in person. The sources are fakes; screenshots at desktop and phone width:
   python tests/ui_api.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import requests

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import auth
import core
import music
import seerr
import uvicorn
import yt
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-api-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-api-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("scripter", PASSWORD, False)
jobs = []
for p in (mock.patch.object(yt, "search", lambda q, request, user: {"items": [{"type": "video", "id": "dQw4w9WgXcQ", "title": "A video", "channel": "Chan"}]}),
          mock.patch.object(seerr, "search", lambda q, page=1, _=None: {"items": [{"type": "movie", "id": 550, "title": "A Film", "year": "1999"}]}),
          mock.patch.object(music, "search", lambda q: {"albums": [{"id": "alb1", "name": "Rec", "artists": [{"name": "Band"}]}]}),
          mock.patch.object(music, "queue_download", lambda kind, id, title=None, subtitle=None, image=None: jobs.append(
              {"job": "mj1", "status": "downloading", "title": "Rec", "error": None, "songs": {}}) or jobs[-1]),
          mock.patch.object(music, "jobs", jobs)):
    p.start()

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

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    ctx = b.new_context(viewport={"width": 1366, "height": 1000}, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", "scripter")
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#apiKeyForm", timeout=30000)
    page.fill("#apiKeyName", "Home Assistant")
    page.click("#apiKeyForm button")
    page.wait_for_selector("#freshApiKey", timeout=30000)
    key = page.inner_text("#freshApiKey").strip()
    page.locator("#apiKeys").scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / "keys-desk.png"))
    bot = requests.Session()
    bot.headers["X-Api-Key"] = key
    found = bot.get(BASE + "/api/v1/search", params={"q": "rec", "in": "youtube,seerr,spotify"}).json()
    checks["a program searches with the key"] = {x["source"] for x in found["items"]} == {"youtube", "seerr", "spotify"}
    started = bot.post(BASE + "/api/v1/get", json={"kind": "album", "id": "alb1"}).json()
    checks["gets something and follows it"] = bot.get(BASE + f"/api/v1/jobs/{started['job']}").json()["state"] == "downloading"
    page.reload()
    page.wait_for_selector("#apiKeyList li", timeout=30000)
    checks["Settings shows its last use, never the key"] = "last used" in page.inner_text("#apiKeyList") and key not in page.content()
    page.on("dialog", lambda d: d.accept())
    page.click("[data-drop-api]")
    page.wait_for_selector("#apiKeyList li", state="detached", timeout=30000)
    checks["Delete stops it at once"] = bot.get(BASE + "/api/me").status_code == 401
    docs = ctx.new_page()
    docs.goto(BASE + "/api/docs")
    docs.wait_for_selector(".opblock-summary-path[data-path='/api/v1/search']", timeout=30000)
    checks["the API docs open for a signed-in person"] = True
    docs.screenshot(path=str(OUT / "docs-desk.png"))
    checks["and not for anyone else"] = requests.get(BASE + "/api/docs").status_code == 401
    phone = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True)
    phone.add_cookies(ctx.cookies())
    pp = phone.new_page()
    pp.on("pageerror", lambda e: errors.append(str(e)))
    pp.goto(BASE + "/#/settings")
    pp.wait_for_selector("#apiKeyForm", timeout=30000)
    pp.locator("#apiKeys").scroll_into_view_if_needed()
    pp.wait_for_timeout(400)
    pp.screenshot(path=str(OUT / "keys-phone.png"))
    checks["no sideways scroll on a phone"] = pp.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
shots = sorted(x.name for x in OUT.glob("*.png"))
print("screenshots:", ", ".join(shots))
ok = len(checks) == 7 and all(checks.values()) and not errors and len(shots) == 3
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
