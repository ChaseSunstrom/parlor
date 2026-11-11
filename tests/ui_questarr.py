"""Questarr's row in Settings → Services in a real browser, against the fake stack from test_stack.py: after setup it
says it needs IGDB keys and shows the form; a wrong password and a bad key are said; the right ones go to Questarr,
and the form and the note are gone. Screenshots at desktop and phone width:
   python tests/ui_questarr.py [OUT_DIR]   (needs Playwright and its Chromium)"""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

import yeerr_test_env as env  # noqa: F401  (a throwaway data folder, then the app)

import arr
import auth
import core
import stack
import test_stack as fakes
import uvicorn
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="ui-questarr-"))
OUT.mkdir(parents=True, exist_ok=True)
PASSWORD = "Ui-quest-pass-42!"  # pragma: allowlist secret (made up)
core.db()
auth.create_user("boss", PASSWORD, True)
fake = fakes.FakeStack()
for p in (mock.patch.object(stack.requests, "request", fake), mock.patch.object(arr.requests, "request", fake), mock.patch.object(arr, "STACK", fakes.stack_dir()),
          mock.patch.object(stack, "STACK", fakes.stack_dir()), mock.patch.object(stack, "DATA", fakes.TMP / "media"), mock.patch.dict(os.environ, {"STACK": "1"}),
          mock.patch.object(stack, "INDEXERS", ["yts", "eztv"])):
    p.start()
stack.state.update(results=stack.run("boss", PASSWORD), finished=time.time(), started=time.time() - 5)
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
ID, SECRET = "a" * 30, "b" * 30  # pragma: allowlist secret (made up)


def give(page, cid, pw, says):
    page.fill("#igdbId", cid)
    page.fill("#igdbSecret", SECRET)
    page.fill("#igdbPw", pw)
    with page.expect_response(lambda r: r.url.endswith("/questarr/igdb"), timeout=15000):
        page.click("#igdbForm button")
    page.wait_for_function("(t) => [...document.querySelectorAll('.toast')].some((e) => e.textContent.includes(t))", arg=says, timeout=8000)


with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=str(SHELL) if SHELL else None)
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, color_scheme="dark")
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector("#gateForm")
    page.fill("#gName", "boss")
    page.fill("#gPw", PASSWORD)
    page.click("#gateForm .primary")
    page.wait_for_selector("#gateForm", state="detached", timeout=30000)
    page.goto(BASE + "/#/settings")
    page.wait_for_selector("#igdbForm", timeout=15000)
    row = page.locator(".svc", has_text="Questarr")
    checks["Questarr's row says it needs IGDB keys, with its setup steps"] = "needs IGDB keys" in row.inner_text() and "connections" in row.inner_text() and "from Prowlarr" in row.inner_text()
    checks["the form links to where the keys come from"] = page.get_attribute("#igdbForm a", "href") == "https://dev.twitch.tv/console/apps"
    row.scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "questarr-needs-keys.png"))
    give(page, ID, "not my password", "That isn't your password")
    give(page, "short", PASSWORD, "doesn't look valid")
    checks["a wrong password and a bad key are said, and nothing is sent on"] = fake.quest["igdb"] is None
    give(page, ID, PASSWORD, "Questarr has its IGDB keys")
    page.wait_for_selector("#igdbForm", state="detached", timeout=8000)
    row = page.locator(".svc", has_text="Questarr")
    checks["the right ones reach Questarr, and the form and the note go"] = fake.quest["igdb"] == (ID, SECRET) and "needs IGDB" not in row.inner_text()
    checks["the secret is in no page"] = SECRET not in page.content()
    core.set_setting("questarr_igdb", "")
    phone = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="light", is_mobile=True, has_touch=True, storage_state=ctx.storage_state()).new_page()
    phone.on("pageerror", lambda e: errors.append(f"phone: {e}"))
    phone.goto(BASE + "/#/settings")
    phone.wait_for_selector("#igdbForm", timeout=15000)
    phone.locator("#igdbForm").scroll_into_view_if_needed()
    checks["on a phone the form fits the screen"] = phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    phone.screenshot(path=str(OUT / "questarr-needs-keys-phone.png"))
    b.close()
server.should_exit = True
for k, v in checks.items():
    print(f"  {'✓' if v else '✗'} {k}")
print("page errors:", errors or "none")
print(f"screenshots: questarr-needs-keys.png, questarr-needs-keys-phone.png in {OUT}")
ok = len(checks) == 6 and all(checks.values()) and not errors
print("RESULT:", "ok" if ok else "FAIL")
sys.exit(0 if ok else 1)
